import os

# ⚠️ Doit être défini AVANT tout subprocess.run() dans ce process. Sur macOS,
# lancer un sous-processus (fork+exec) depuis un process devenu multi-thread
# (uvloop + clients réseau async) fait planter les handlers post-fork de
# Network.framework/os_log ("multi-threaded process forked" / "crashed on
# child side of fork pre-exec"). Sans effet sur Linux (production/Dokploy),
# où ce mécanisme Objective-C n'existe pas.
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import json
import re
import secrets
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from audit_engine import (
    run_audit,
    update_plugin_via_agent,
    update_core_via_agent,
    delete_theme_via_agent,
    refresh_site_in_results,
    call_pb_worker,
    RESULTS_FILE,
)
from services.pocketbase_client import is_php_obsolete

# Chargement des variables d'environnement
load_dotenv()

# Initialisation Sentry (désactivé si SENTRY_DSN absent)
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        environment=os.getenv("ENVIRONMENT", "development"),
    )
    print("✅ Sentry activé")

# Clé partagée avec le proxy serveur du frontend (jamais exposée au navigateur) —
# protège l'API contre un accès direct par quiconque trouve l'URL publique.
_BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")

def require_api_key(x_api_key: str = Header(default="")):
    if not _BACKEND_API_KEY or not secrets.compare_digest(x_api_key, _BACKEND_API_KEY):
        raise HTTPException(status_code=401, detail="Clé API manquante ou invalide")

# État de progression de l'audit en cours (partagé entre background task et API)
_audit_progress: dict = {"status": "idle", "percent": 0, "label": ""}

def _on_progress(data: dict):
    _audit_progress.update(data)

async def _run_audit_locked(*args, **kwargs):
    """
    Empêche deux audits de tourner en même temps (ex. deux onglets ouverts,
    ou double-clic malgré la désactivation du bouton côté front) — deux
    run_audit() concurrents se marchent dessus sur results.json et sur
    l'écriture PocketBase groupée.
    """
    try:
        await run_audit(*args, **kwargs)
    finally:
        _audit_progress["_locked"] = False

# Initialisation FastAPI
app = FastAPI(
    title="WeRocket Maintenance API",
    description="API pour auditer les sites WordPress",
    version="1.0.0"
)

# Configuration CORS — le frontend n'appelle plus ce backend depuis le
# navigateur (il passe par son propre proxy serveur), donc ceci ne sert
# plus que de garde-fou pour des appels directs/tests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# MODÈLES PYDANTIC (Ce que le Front envoie/reçoit)
# ============================================================================

class ScanRequest(BaseModel):
    url: Optional[str] = None # Si absent : audit sur les sites actifs en BDD (voir `limit`)
    client_name: Optional[str] = "Client API"
    limit: Optional[int] = None # Borne le nombre de sites (audit de test), ignoré si `url` est fourni
    random_sample: Optional[bool] = False # Tire `limit` sites au hasard plutôt que les premiers, ignoré si `url` est fourni

class ScanResponse(BaseModel):
    message: str
    status: str
    url: str

class PluginUpdateRequest(BaseModel):
    url: str
    plugin_slug: str

class PluginUpdateResponse(BaseModel):
    success: bool
    message: str

class ThemeDeleteRequest(BaseModel):
    url: str
    theme_slug: str

class ThemeDeleteResponse(BaseModel):
    success: bool
    message: str

class CoreUpdateRequest(BaseModel):
    url: str

class CoreUpdateResponse(BaseModel):
    success: bool
    message: str
    version: Optional[str] = None

class SiteResult(BaseModel):
    """Correspondance avec les données envoyées au Dashboard"""
    client: str
    url: str
    ia_status: Optional[str] = "N/A"
    ia_score: Optional[int] = 0
    ia_status_contact: Optional[str] = "N/A"
    ia_score_contact: Optional[int] = 0
    ia_status_mobile: Optional[str] = "N/A"
    ia_score_mobile: Optional[int] = 0
    wp_version: Optional[str] = "N/A"
    php_version: Optional[str] = "N/A"
    theme: Optional[str] = "N/A"
    builder: Optional[str] = "N/A"
    updates_count: Optional[int] = 0
    mises_a_jour: Optional[str] = "Aucune"
    methode: Optional[str] = "N/A"
    licenses: Optional[dict] = None
    themes: Optional[list] = None

class PaginatedResults(BaseModel):
    items: List[SiteResult]
    total: int
    page: int
    per_page: int
    total_pages: int

# ============================================================================
# RÉCAP — calculé depuis results.json (l'audit affiché dans le tableau),
# jamais depuis tout l'historique PocketBase, pour rester cohérent avec ce
# que montre l'onglet Résultats.
# ============================================================================

_RISK_RE = re.compile(r"-\s*(CRITIQUE|ÉLEVÉ|MOYEN|FAIBLE|INCONNU|AUCUN)\s*:")
_PLUGIN_NAME_RE = re.compile(r"^\S+\s+(.+?)\s+\(")

def _parse_plugin_updates(mises_a_jour: str) -> list[dict]:
    """Reparse la chaîne 'slug||emoji Nom (v1 → v2) - RISQUE: message, ...'
    (même format que celui affiché dans le tableau) en entrées structurées."""
    if not mises_a_jour or mises_a_jour == "Aucune":
        return []
    entries = []
    for chunk in mises_a_jour.split(", "):
        slug, text = chunk.split("||", 1) if "||" in chunk else ("", chunk)
        risk_match = _RISK_RE.search(text)
        name_match = _PLUGIN_NAME_RE.match(text)
        entries.append({
            "slug": slug or (name_match.group(1) if name_match else text[:40]),
            "name": name_match.group(1) if name_match else (slug or text[:40]),
            "risk": risk_match.group(1) if risk_match else "INCONNU",
        })
    return entries

def _compute_recap_from_results(data: list[dict]) -> dict:
    ia_faible = []
    for d in data:
        checks = [
            ("accueil", d.get("ia_status"), d.get("ia_score")),
            ("contact", d.get("ia_status_contact"), d.get("ia_score_contact")),
            ("mobile", d.get("ia_status_mobile"), d.get("ia_score_mobile")),
        ]
        faibles = {label: score for label, status, score in checks if status and status != "N/A" and (score or 0) < 70}
        if faibles:
            ia_faible.append({"client": d.get("client"), "url": d.get("url"), "scores": faibles})
    ia_faible.sort(key=lambda s: min(s["scores"].values()))

    plugin_agg: dict[str, dict] = {}
    for d in data:
        for p in _parse_plugin_updates(d.get("mises_a_jour") or ""):
            agg = plugin_agg.setdefault(p["slug"], {"plugin_name": p["name"], "sites": {}, "risk_counts": {}})
            # dict clé=url plutôt qu'un set : un site ne doit apparaître qu'une
            # fois par plugin même s'il a plusieurs entrées de MAJ pour lui
            # (ne devrait pas arriver, mais mieux vaut être robuste)
            agg["sites"][d.get("url")] = {"client": d.get("client"), "url": d.get("url"), "risk": p["risk"]}
            agg["risk_counts"][p["risk"]] = agg["risk_counts"].get(p["risk"], 0) + 1
    plugin_frequency = sorted(
        [
            {
                "plugin_slug": slug,
                "plugin_name": v["plugin_name"],
                "sites_count": len(v["sites"]),
                "risk_counts": v["risk_counts"],
                "sites": list(v["sites"].values()),
            }
            for slug, v in plugin_agg.items()
        ],
        key=lambda x: x["sites_count"],
        reverse=True,
    )

    php_obsolete = [
        {"client": d.get("client"), "url": d.get("url"), "php_version": d.get("php_version")}
        for d in data
        if is_php_obsolete(d.get("php_version") or "")
    ]

    methode_counts: dict[str, int] = {}
    for d in data:
        methode = d.get("methode") or "Inconnu"
        methode_counts[methode] = methode_counts.get(methode, 0) + 1

    return {
        "total_sites": len(data),
        "ia_faible": ia_faible,
        "plugin_frequency": plugin_frequency,
        "php_obsolete": php_obsolete,
        "methode_counts": methode_counts,
    }

def _compute_stats_from_results(data: list[dict]) -> dict:
    """Même forme que get_stats_summary() (PocketBase), mais calculée depuis
    results.json — pour que les pastilles de filtres IA/MAJ du tableau
    reflètent le dernier audit affiché, pas tout l'historique du parc."""
    total_sites = len(data)
    ia_ok = sum(1 for d in data if d.get("ia_status") == "OK")
    ia_attention = sum(1 for d in data if d.get("ia_status") == "ATTENTION")
    ia_alerte = sum(1 for d in data if d.get("ia_status") == "ALERTE")
    total_updates = sum(d.get("updates_count") or 0 for d in data)
    scans_agent = sum(1 for d in data if d.get("methode") == "Agent")

    avec_maj = sum(1 for d in data if (d.get("updates_count") or 0) > 0)
    critiques = sum(1 for d in data if "🔴" in (d.get("mises_a_jour") or ""))
    a_jour = sum(1 for d in data if (d.get("updates_count") or 0) == 0)

    return {
        "total_sites": total_sites,
        "ia_status": {"ok": ia_ok, "attention": ia_attention, "alerte": ia_alerte},
        "maj_status": {"toutes": total_sites, "avec_maj": avec_maj, "critiques": critiques, "a_jour": a_jour},
        "kpis": {"updates_pending": total_updates, "agent_coverage": scans_agent},
    }

# ============================================================================
# ROUTES API
# ============================================================================

@app.on_event("startup")
def check_pocketbase():
    import time
    max_attempts = 15
    delay_seconds = 2

    for attempt in range(1, max_attempts + 1):
        try:
            call_pb_worker("ping")
            return
        except Exception:
            if attempt == max_attempts:
                raise
            print(f"⏳ PocketBase non prêt (tentative {attempt}/{max_attempts}), nouvelle tentative dans {delay_seconds}s...")
            time.sleep(delay_seconds)

@app.get("/")
async def root():
    return {"message": "🚀 WeRocket Maintenance API is running!"}

@app.post("/api/scan", response_model=ScanResponse, dependencies=[Depends(require_api_key)])
async def launch_scan(
    request: Request,
    background_tasks: BackgroundTasks,
    scan_request: Optional[ScanRequest] = None,
):
    """
    Lance l'audit en tâche de fond (ne bloque pas l'interface)
    """
    print(f"[audit] /api/scan from {request.client.host} origin={request.headers.get('origin')} limit={scan_request.limit if scan_request else None}")

    if _audit_progress.get("_locked"):
        raise HTTPException(status_code=409, detail="Un audit est déjà en cours — attends qu'il se termine.")

    limit = None
    random_sample = False
    if scan_request and scan_request.url:
        sites_list = [{"Client": scan_request.client_name, "URL": scan_request.url}]
    else:
        sites_list = None  # Le moteur lira les sites actifs depuis PocketBase
        limit = scan_request.limit if scan_request else None
        random_sample = bool(scan_request and scan_request.random_sample)

    _audit_progress.update({"status": "running", "percent": 0, "label": "Démarrage de l'audit...", "_locked": True})
    background_tasks.add_task(_run_audit_locked, sites_list, _on_progress, limit, random_sample)

    return ScanResponse(
        message="Audit lancé en arrière-plan 🚀",
        status="pending",
        url=scan_request.url if scan_request and scan_request.url else ("BDD" + (f" (limité à {limit})" if limit else ""))
    )

@app.get("/api/results", response_model=PaginatedResults, dependencies=[Depends(require_api_key)])
async def get_results(
    request: Request,
    page: int = 1,
    per_page: int = 25,
    search: str = "",
    ia_status: str = "",
    maj: str = "",
):
    print(
        f"[audit] /api/results from {request.client.host} origin={request.headers.get('origin')} "
        f"page={page} search={search!r} ia_status={ia_status!r} maj={maj!r}"
    )

    # Priorité : fichier JSON local (indépendant de la DB)
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Garde-fou : PHP sérialise un tableau associatif vide en JSON
            # `[]`, ce que Pydantic (Optional[dict]) rejette.
            for entry in data:
                if not isinstance(entry.get("licenses"), dict):
                    entry["licenses"] = {}

            if search:
                needle = search.strip().lower()
                data = [
                    d for d in data
                    if needle in (d.get("client") or "").lower() or needle in (d.get("url") or "").lower()
                ]
            if ia_status:
                data = [d for d in data if d.get("ia_status") == ia_status]
            if maj == "avec_maj":
                data = [d for d in data if (d.get("updates_count") or 0) > 0]
            elif maj == "critiques":
                data = [d for d in data if "🔴" in (d.get("mises_a_jour") or "")]
            elif maj == "a_jour":
                data = [d for d in data if (d.get("updates_count") or 0) == 0]

            total = len(data)
            total_pages = max(1, -(-total // per_page))  # ceil division
            start = (page - 1) * per_page
            items = data[start:start + per_page]

            return PaginatedResults(
                items=items, total=total, page=page, per_page=per_page, total_pages=total_pages
            )
        except Exception as e:
            print(f"⚠️  Erreur lecture results.json: {e}")

    # Fallback : PocketBase (pagination + filtres natifs, chaque projet porte
    # déjà son dernier état — pas besoin de dédoublonnage)
    try:
        import asyncio
        return await asyncio.to_thread(
            call_pb_worker,
            "get_results_summary",
            {"page": page, "per_page": per_page, "search": search, "ia_status": ia_status, "maj": maj},
        )
    except Exception as e:
        print(f"❌ Erreur PocketBase : {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-plugin", response_model=PluginUpdateResponse, dependencies=[Depends(require_api_key)])
async def update_plugin(request: Request, body: PluginUpdateRequest):
    """
    Déclenche la mise à jour d'un plugin spécifique via l'Agent WordPress.
    """
    print(f"[audit] /api/update-plugin from {request.client.host} url={body.url} plugin={body.plugin_slug}")
    import asyncio
    result = await asyncio.to_thread(update_plugin_via_agent, body.url, body.plugin_slug)
    if result.get('success'):
        refresh = await asyncio.to_thread(refresh_site_in_results, body.url)
        if not refresh.get('success'):
            print(f"[audit] ⚠️  results.json non rafraîchi pour {body.url}: {refresh.get('error')}")
        return PluginUpdateResponse(success=True, message=result.get('message', 'Plugin mis à jour avec succès'))
    raise HTTPException(status_code=400, detail=result.get('error', 'Erreur inconnue'))

@app.post("/api/delete-theme", response_model=ThemeDeleteResponse, dependencies=[Depends(require_api_key)])
async def delete_theme(request: Request, body: ThemeDeleteRequest):
    """
    Supprime un thème WordPress inutilisé via l'Agent.
    """
    print(f"[audit] /api/delete-theme from {request.client.host} url={body.url} theme={body.theme_slug}")
    import asyncio
    result = await asyncio.to_thread(delete_theme_via_agent, body.url, body.theme_slug)
    if result.get('success'):
        refresh = await asyncio.to_thread(refresh_site_in_results, body.url)
        if not refresh.get('success'):
            print(f"[audit] ⚠️  results.json non rafraîchi pour {body.url}: {refresh.get('error')}")
        return ThemeDeleteResponse(success=True, message=result.get('message', 'Thème supprimé avec succès'))
    raise HTTPException(status_code=400, detail=result.get('error', 'Erreur inconnue'))

@app.post("/api/update-core", response_model=CoreUpdateResponse, dependencies=[Depends(require_api_key)])
async def update_core(request: Request, body: CoreUpdateRequest):
    """
    Déclenche la mise à jour du core WordPress via l'Agent WordPress.
    """
    print(f"[audit] /api/update-core from {request.client.host} url={body.url}")
    import asyncio
    result = await asyncio.to_thread(update_core_via_agent, body.url)
    if result.get('success'):
        refresh = await asyncio.to_thread(refresh_site_in_results, body.url)
        if not refresh.get('success'):
            print(f"[audit] ⚠️  results.json non rafraîchi pour {body.url}: {refresh.get('error')}")
        return CoreUpdateResponse(success=True, message=result.get('message', 'WordPress mis à jour avec succès'), version=result.get('version'))
    raise HTTPException(status_code=400, detail=result.get('error', 'Erreur inconnue'))

@app.get("/api/progress", dependencies=[Depends(require_api_key)])
async def get_progress():
    return {k: v for k, v in _audit_progress.items() if not k.startswith("_")}

@app.get("/api/stats", dependencies=[Depends(require_api_key)])
async def get_stats():
    """
    Statistiques pour les pastilles de filtres IA/MAJ du dashboard — calculées
    depuis results.json (comme /api/results et /api/recap), pour rester
    cohérentes avec le dernier audit affiché plutôt qu'avec tout l'historique
    PocketBase. Fallback PocketBase uniquement si aucun audit n'a encore été lancé.
    """
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _compute_stats_from_results(data)
        except Exception as e:
            print(f"⚠️  Erreur lecture results.json pour /api/stats: {e}")

    try:
        import asyncio
        return await asyncio.to_thread(call_pb_worker, "get_stats_summary")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/recap", dependencies=[Depends(require_api_key)])
async def get_recap():
    """
    Vue d'ensemble de l'audit actuel (sites à IA faible, fréquence des MAJ
    par plugin, PHP obsolète, répartition Agent/Scraping) — calculée depuis
    results.json, exactement comme /api/results, pour rester cohérente avec
    ce qui est affiché dans le tableau. Fallback PocketBase (vue du parc
    entier) uniquement si aucun audit n'a encore été lancé.
    """
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _compute_recap_from_results(data)
        except Exception as e:
            print(f"⚠️  Erreur lecture results.json pour /api/recap: {e}")

    try:
        import asyncio
        return await asyncio.to_thread(call_pb_worker, "get_recap_summary", timeout=60)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Pour lancer : uvicorn main:app --reload
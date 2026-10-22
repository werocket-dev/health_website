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
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from audit_engine import (
    run_audit,
    update_plugin_via_agent,
    update_core_via_agent,
    delete_theme_via_agent,
    delete_plugin_via_agent,
    activate_license_via_agent,
    install_plugin_zip,
    refresh_site_in_results,
    call_pb_worker,
)
from services.config import RESULTS_FILE, LAST_AUDIT_FILE, scope_files
from services.pocketbase_client import is_php_obsolete
from services import breakdance_zip, job_guard, plugin_zip, url_guard

# Chargement des variables d'environnement
load_dotenv()

# Initialisation Sentry (désactivé si SENTRY_DSN absent)
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        environment=os.getenv("ENVIRONMENT", "development"),
    )
    print("✅ Sentry activé")

# Clé partagée avec le proxy serveur du frontend (jamais exposée au navigateur) —
# protège l'API contre un accès direct par quiconque trouve l'URL publique.
_BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")

def _check_site_url(url: str, require_known: bool = True):
    """Le backend signe et envoie des ordres à l'URL reçue : on refuse tout ce qui n'est
    pas un site public connu (SSRF vers localhost, métadonnées cloud, réseau interne)."""
    try:
        url_guard.validate_site_url(url, require_known=require_known)
    except url_guard.UrlRejected as e:
        raise HTTPException(status_code=400, detail=str(e))

async def _check_site_urls(urls: List[str]):
    """Valide plusieurs URLs d'un coup : la liste des sites connus est lue une seule fois, et les
    résolutions DNS (bloquantes) tournent hors de la boucle d'événements."""
    known = url_guard.known_hosts()

    def check_all():
        for url in urls:
            url_guard.validate_site_url(url, known=known)

    import asyncio
    try:
        await asyncio.to_thread(check_all)
    except url_guard.UrlRejected as e:
        raise HTTPException(status_code=400, detail=str(e))

def _read_entries(path: str) -> list:
    """Entrées d'un fichier de résultats ; fichier absent ou illisible : liste vide."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict)]

def _results_files(scope: str) -> tuple[str, str]:
    """(fichier de résultats, fichier du dernier audit) du parc ou de l'onglet Test."""
    try:
        return scope_files(scope)
    except ValueError:
        raise HTTPException(status_code=400, detail="scope invalide (parc ou test)")

def require_api_key(x_api_key: str = Header(default="")):
    # Comparaison en octets : compare_digest lève une TypeError sur du non-ASCII (500 au lieu de 401).
    if not _BACKEND_API_KEY or not secrets.compare_digest(x_api_key.encode("utf-8"), _BACKEND_API_KEY.encode("utf-8")):
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
    except Exception as e:
        # Sans ça le statut restait « running » à jamais : le front sondait /api/progress
        # sans fin et la barre ne bougeait plus.
        print(f"[audit] ❌ audit interrompu : {e}")
        sentry_sdk.capture_exception(e)
        reason = (str(e).splitlines() or [type(e).__name__])[0][:200]
        _audit_progress.update({"status": "error", "percent": 0, "label": f"Audit interrompu : {reason}"})
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
    urls: Optional[List[str]] = None # Sites précis à auditer (onglet Test), prioritaire sur `limit`
    scope: str = "parc" # "test" : résultats écrits dans leurs propres fichiers, le parc n'est pas touché

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

class LicenseActivateRequest(BaseModel):
    url: str
    license_key: str

class LicenseActivateResponse(BaseModel):
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
    http_status: Optional[int] = None

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

def _is_http_error(d: dict) -> bool:
    status = d.get("http_status")
    return isinstance(status, int) and status >= 400

# Dossier de l'agent WeRocket côté WordPress : jamais proposé à la suppression.
AGENT_PLUGIN_FOLDER = "werocket-agent"

def _compute_installed_plugins(data: list[dict]) -> list[dict]:
    """Tous les plugins installés (pas seulement ceux à mettre à jour), groupés par chemin
    dossier/fichier.php : sert à l'onglet de suppression. L'agent WeRocket est exclu : il
    refuse de se supprimer lui-même, inutile de le proposer."""
    installed_agg: dict[str, dict] = {}
    for d in data:
        for p in d.get("plugins_installed") or []:
            path = p.get("path") or ""
            if not path or path.split("/")[0] == AGENT_PLUGIN_FOLDER:
                continue
            agg = installed_agg.setdefault(path, {"plugin_name": p.get("name") or path, "sites": {}})
            agg["sites"][d.get("url")] = {
                "client": d.get("client"), "url": d.get("url"),
                "version": p.get("version"), "is_active": p.get("is_active"),
            }
    return sorted(
        (
            {
                "plugin_path": path,
                "plugin_name": v["plugin_name"],
                "sites_count": len(v["sites"]),
                "sites": list(v["sites"].values()),
            }
            for path, v in installed_agg.items()
        ),
        key=lambda x: (-x["sites_count"], x["plugin_name"].lower()),
    )

def _compute_recap_from_results(data: list[dict]) -> dict:
    # Sites dont l'accueil répond 4xx/5xx (403 pare-feu, 500 erreur critique...) :
    # la capture est alors une page d'erreur, le score IA n'a pas de sens — on les
    # liste à part au lieu de les mélanger aux faux positifs visuels.
    http_erreur = sorted(
        ({"client": d.get("client"), "url": d.get("url"), "http_status": d["http_status"]} for d in data if _is_http_error(d)),
        key=lambda s: (-s["http_status"], s["client"] or ""),
    )

    ia_faible = []
    for d in data:
        if _is_http_error(d):
            continue
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

    # Licences Breakdance jamais vérifiées (status "a_verifier") OU explicitement
    # invalides/désactivées (valid=False) — pas juste les jamais-contrôlées.
    breakdance_licenses_a_verifier = [
        {"client": d.get("client"), "url": d.get("url"), "valid": lic.get("valid"), "plugin_version": lic.get("plugin_version")}
        for d in data
        for lic in [(d.get("licenses") or {}).get("breakdance")]
        if lic and lic.get("valid") is not True
    ]

    # Sites accumulant des thèmes WordPress inutilisés (> 3) — candidats pour
    # la fonctionnalité de suppression de thèmes déjà en place.
    sites_trop_de_themes = sorted(
        (
            {"client": d.get("client"), "url": d.get("url"), "themes_count": len(themes), "themes": themes}
            for d in data
            for themes in [d.get("themes") or []]
            if len(themes) > 3
        ),
        key=lambda s: s["themes_count"],
        reverse=True,
    )

    return {
        "total_sites": len(data),
        "ia_faible": ia_faible,
        "http_erreur": http_erreur,
        "plugin_frequency": plugin_frequency,
        "php_obsolete": php_obsolete,
        "methode_counts": methode_counts,
        "breakdance_licenses_a_verifier": breakdance_licenses_a_verifier,
        "sites_trop_de_themes": sites_trop_de_themes,
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
    # PocketBase n'est qu'un secours (results.json est la source principale) :
    # une route publique lente/injoignable ne doit jamais empêcher l'API de
    # démarrer. Avant, ce check bloquait le démarrage jusqu'à ~25 min (15
    # tentatives) puis faisait planter tout le conteneur en boucle.
    try:
        call_pb_worker("ping", timeout=10)
    except Exception as e:
        print(f"⚠️  PocketBase injoignable au démarrage ({e}) — l'API démarre quand même.")

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

    scope = scan_request.scope if scan_request else "parc"
    _results_files(scope)  # valide le scope

    limit = None
    random_sample = False
    if scan_request and scan_request.urls:
        if scope != "test":
            raise HTTPException(status_code=400, detail="Le choix de sites n'est possible que dans l'onglet Test")
        urls = list(dict.fromkeys(scan_request.urls))
        await _check_site_urls(urls)  # sites connus uniquement
        names = {e.get("url"): e.get("client") for e in _read_entries(RESULTS_FILE)}
        sites_list = [{"Client": names.get(u) or u, "URL": u} for u in urls]
    elif scan_request and scan_request.url:
        _check_site_url(scan_request.url, require_known=False)  # nouveau site possible : seul le contrôle d'IP
        sites_list = [{"Client": scan_request.client_name, "URL": scan_request.url}]
    else:
        sites_list = None  # Le moteur lira les sites actifs depuis PocketBase
        limit = scan_request.limit if scan_request else None
        random_sample = bool(scan_request and scan_request.random_sample)

    _audit_progress.update({"status": "running", "percent": 0, "label": "Démarrage de l'audit...", "_locked": True})
    background_tasks.add_task(_run_audit_locked, sites_list, _on_progress, limit, random_sample, scope)

    return ScanResponse(
        message="Audit lancé en arrière-plan 🚀",
        status="pending",
        url=scan_request.url if scan_request and scan_request.url else ("BDD" + (f" (limité à {limit})" if limit else ""))
    )

@app.get("/api/results", response_model=PaginatedResults, dependencies=[Depends(require_api_key)])
async def get_results(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=200),
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
    _check_site_url(body.url)
    import asyncio
    result = await asyncio.to_thread(update_plugin_via_agent, body.url, body.plugin_slug)
    if result.get('success'):
        refresh = await asyncio.to_thread(refresh_site_in_results, body.url)
        if not refresh.get('success'):
            print(f"[audit] ⚠️  results.json non rafraîchi pour {body.url}: {refresh.get('error')}")
        # L'agent répond « Déjà à jour » (succès) quand WordPress ne peut pas télécharger
        # la mise à jour (ex. licence Breakdance refusée) : la vérité, c'est que le plugin
        # n'a plus de mise à jour en attente une fois le site relu.
        elif any(u.split('||', 1)[0] == body.plugin_slug for u in refresh.get('updates_needed', [])):
            print(f"[audit] ❌ update-plugin sans effet pour {body.url} ({body.plugin_slug}): mise à jour toujours en attente")
            raise HTTPException(status_code=400, detail="Mise à jour non appliquée (toujours en attente sur le site)")
        return PluginUpdateResponse(success=True, message=result.get('message', 'Plugin mis à jour avec succès'))
    print(f"[audit] ❌ update-plugin échoué pour {body.url} ({body.plugin_slug}): {result.get('error')}")
    raise HTTPException(status_code=400, detail=result.get('error', 'Erreur inconnue'))

@app.post("/api/delete-theme", response_model=ThemeDeleteResponse, dependencies=[Depends(require_api_key)])
async def delete_theme(request: Request, body: ThemeDeleteRequest):
    """
    Supprime un thème WordPress inutilisé via l'Agent.
    """
    print(f"[audit] /api/delete-theme from {request.client.host} url={body.url} theme={body.theme_slug}")
    _check_site_url(body.url)
    import asyncio
    result = await asyncio.to_thread(delete_theme_via_agent, body.url, body.theme_slug)
    if result.get('success'):
        refresh = await asyncio.to_thread(refresh_site_in_results, body.url)
        if not refresh.get('success'):
            print(f"[audit] ⚠️  results.json non rafraîchi pour {body.url}: {refresh.get('error')}")
        return ThemeDeleteResponse(success=True, message=result.get('message', 'Thème supprimé avec succès'))
    print(f"[audit] ❌ delete-theme échoué pour {body.url} ({body.theme_slug}): {result.get('error')}")
    raise HTTPException(status_code=400, detail=result.get('error', 'Erreur inconnue'))

@app.post("/api/activate-license", response_model=LicenseActivateResponse, dependencies=[Depends(require_api_key)])
async def activate_license(request: Request, body: LicenseActivateRequest):
    """
    Active/revalide la licence Breakdance sur un site via l'Agent.
    """
    print(f"[audit] /api/activate-license from {request.client.host} url={body.url}")  # pas de license_key ici non plus
    _check_site_url(body.url)
    import asyncio
    result = await asyncio.to_thread(activate_license_via_agent, body.url, body.license_key)
    if result.get('success'):
        refresh = await asyncio.to_thread(refresh_site_in_results, body.url)
        if not refresh.get('success'):
            print(f"[audit] ⚠️  results.json non rafraîchi pour {body.url}: {refresh.get('error')}")
        return LicenseActivateResponse(success=True, message=result.get('message', 'Licence activée avec succès'))
    print(f"[audit] ❌ activate-license échoué pour {body.url}: {result.get('error')}")
    raise HTTPException(status_code=400, detail=result.get('error', 'Erreur inconnue'))

@app.post("/api/update-core", response_model=CoreUpdateResponse, dependencies=[Depends(require_api_key)])
async def update_core(request: Request, body: CoreUpdateRequest):
    """
    Déclenche la mise à jour du core WordPress via l'Agent WordPress.
    """
    print(f"[audit] /api/update-core from {request.client.host} url={body.url}")
    _check_site_url(body.url)
    import asyncio
    result = await asyncio.to_thread(update_core_via_agent, body.url)
    if result.get('success'):
        refresh = await asyncio.to_thread(refresh_site_in_results, body.url)
        if not refresh.get('success'):
            print(f"[audit] ⚠️  results.json non rafraîchi pour {body.url}: {refresh.get('error')}")
        return CoreUpdateResponse(success=True, message=result.get('message', 'WordPress mis à jour avec succès'), version=result.get('version'))
    print(f"[audit] ❌ update-core échoué pour {body.url}: {result.get('error')}")
    raise HTTPException(status_code=400, detail=result.get('error', 'Erreur inconnue'))

# ============================================================================
# BREAKDANCE — zip GPL hébergé ici, installé sur les sites par l'Agent
# ============================================================================

@app.post("/api/breakdance/zip", dependencies=[Depends(require_api_key)])
async def upload_breakdance_zip(request: Request):
    """
    Reçoit le zip Breakdance en corps brut (nom dans X-Filename), le valide
    (uniquement breakdance/, version lue dans plugin.php) puis remplace le zip courant.
    Corps brut plutôt que multipart : pas de dépendance en plus, et le zip est
    écrit sur disque au fil de l'eau sans être chargé en mémoire.
    """
    import tempfile
    filename = request.headers.get("x-filename", "breakdance.zip")
    fd, tmp_path = tempfile.mkstemp(dir=breakdance_zip.ZIP_DIR, suffix=".upload")
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > breakdance_zip.MAX_ZIP_BYTES:
                    raise HTTPException(status_code=413, detail="Zip trop volumineux (max 200 Mo)")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Fichier vide")
        try:
            meta = breakdance_zip.store_zip(tmp_path, filename)
        except breakdance_zip.ZipError as e:
            raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print(f"[audit] zip Breakdance mis à jour : v{meta['version']} ({meta['size']} octets)")
    return meta

@app.get("/api/breakdance/zip", dependencies=[Depends(require_api_key)])
def get_breakdance_zip():
    return {"zip": breakdance_zip.get_meta()}

@app.get("/dl/breakdance/{token}")
def download_breakdance_zip(token: str):
    """Public (l'agent WP n'envoie aucun header) : protégé uniquement par le token HMAC à durée limitée."""
    if not breakdance_zip.verify_token(token):
        raise HTTPException(status_code=403, detail="Lien invalide ou expiré")
    return FileResponse(breakdance_zip.ZIP_FILE, media_type="application/zip", filename="breakdance.zip")

class BreakdanceInstallSite(BaseModel):
    url: str
    license_key: Optional[str] = None

class BreakdanceInstallRequest(BaseModel):
    sites: List[BreakdanceInstallSite]

_BD_CONCURRENCY = 2
_BD_PACE_SECONDS = (1.0, 2.5)  # pause par worker entre deux sites : on évite la rafale
_bd_jobs: dict = {}
_bd_tasks: set = set()

async def _run_breakdance_job(job_id: str, sites: List[BreakdanceInstallSite]):
    import asyncio, random
    job = _bd_jobs[job_id]
    sem = asyncio.Semaphore(_BD_CONCURRENCY)
    guard = job_guard.JobGuard()

    async def one(site: BreakdanceInstallSite):
        state = job["sites"][site.url]
        async with sem:
            try:
                ip = await asyncio.to_thread(job_guard.resolve_ip, site.url)
                skip = guard.skip_reason(ip)
                if skip:
                    state.update(status="skipped", message=skip)
                    job["aborted_reason"] = guard.aborted_reason
                    return

                meta = breakdance_zip.get_meta()
                if not meta:
                    raise RuntimeError("Aucun zip Breakdance uploadé")
                state["status"] = "installing"
                # Token généré au moment de traiter CE site : il n'expire pas
                # pendant l'attente dans la file.
                zip_url = breakdance_zip.build_download_url(meta["sha256"])
                inst = await asyncio.to_thread(install_plugin_zip, site.url, zip_url, meta["sha256"])
                if not inst.get("success"):
                    msg = inst.get("error", "Erreur inconnue")
                    guard.record(ip, job_guard.classify_error(msg))
                    job["aborted_reason"] = guard.aborted_reason
                    print(f"[audit] ❌ breakdance install échoué pour {site.url}: {msg}")
                    state.update(status="error", stage="install", message=msg)
                    return
                guard.record(ip, "ok")
                state.update(status="installed", old_version=inst.get("old_version"), new_version=inst.get("new_version"),
                             message=inst.get("message", "Installé"))

                if site.license_key and site.license_key.strip():
                    state["status"] = "activating"
                    act = await asyncio.to_thread(activate_license_via_agent, site.url, site.license_key.strip())
                    lic = act.get("license") or {}
                    state["license_status"] = lic.get("status")
                    if act.get("success"):
                        final = dict(status="done", message=act.get("message", "Installé et licence activée"))
                    else:
                        msg = act.get("error", "Activation échouée")
                        print(f"[audit] ❌ breakdance activation échouée pour {site.url}: {msg}")
                        final = dict(status="error", stage="activation", message=msg)
                else:
                    final = dict(status="done", message="Installé (aucune clé fournie, licence non activée)")

                # Statut final publié seulement après le rafraîchissement : le front
                # recharge le récap dès que tous les sites sont terminés.
                refresh = await asyncio.to_thread(refresh_site_in_results, site.url)
                if not refresh.get("success"):
                    print(f"[audit] ⚠️  results.json non rafraîchi pour {site.url}: {refresh.get('error')}")
                state.update(final)
            except Exception as e:
                print(f"[audit] ❌ breakdance erreur inattendue pour {site.url}: {e}")
                state.update(status="error", stage=state.get("stage") or "install", message=str(e)[:200])
            finally:
                # Ne pas garder la clé de licence en mémoire une fois le site traité.
                site.license_key = None
                await asyncio.sleep(random.uniform(*_BD_PACE_SECONDS))

    try:
        await asyncio.gather(*(one(s) for s in sites))
    finally:
        job["finished"] = True

@app.post("/api/breakdance/install-and-activate", dependencies=[Depends(require_api_key)])
async def breakdance_install_and_activate(request: Request, body: BreakdanceInstallRequest):
    """
    Lance en arrière-plan l'installation du zip puis l'activation de la licence sur
    chaque site (3 en parallèle max, un échec ne bloque pas les autres). Un job
    de plusieurs minutes dépasserait les timeouts du proxy : on renvoie un job_id
    à interroger via GET /api/breakdance/jobs/{job_id}.
    """
    import asyncio, uuid
    if not breakdance_zip.get_meta():
        raise HTTPException(status_code=400, detail="Aucun zip Breakdance uploadé")
    try:
        breakdance_zip.build_download_url("0" * 64)  # échoue tôt si PUBLIC_BACKEND_URL / secret manquants
    except breakdance_zip.ZipError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if any(not j["finished"] for j in list(_bd_jobs.values()) + list(_pz_jobs.values())):
        raise HTTPException(status_code=409, detail="Une installation est déjà en cours")

    sites = list({s.url: s for s in body.sites}.values())
    if not sites:
        raise HTTPException(status_code=400, detail="Aucun site sélectionné")
    await _check_site_urls([s.url for s in sites])

    print(f"[audit] /api/breakdance/install-and-activate from {request.client.host} sites={len(sites)}")
    job_id = uuid.uuid4().hex
    _bd_jobs[job_id] = {
        "finished": False,
        "aborted_reason": None,
        "sites": {s.url: {"status": "pending"} for s in sites},
    }
    for old in list(_bd_jobs)[:-10]:
        if _bd_jobs[old]["finished"]:
            del _bd_jobs[old]

    task = asyncio.create_task(_run_breakdance_job(job_id, sites))
    _bd_tasks.add(task)
    task.add_done_callback(_bd_tasks.discard)
    return {"job_id": job_id}

@app.get("/api/breakdance/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def breakdance_job_status(job_id: str):
    job = _bd_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable (backend redémarré ?)")
    return job

# ============================================================================
# AJOUT D'UN PLUGIN À DISTANCE (zip quelconque)
# ============================================================================

@app.get("/api/sites", dependencies=[Depends(require_api_key)])
def list_sites(scope: str = "parc"):
    """Sites connus (results.json, ou ceux de l'onglet Test), pour choisir où agir."""
    entries = _read_entries(_results_files(scope)[0])
    sites = [{"client": e.get("client", ""), "url": e["url"]} for e in entries if e.get("url")]
    return {"sites": sorted(sites, key=lambda s: s["client"].lower())}

@app.post("/api/plugins/zip", dependencies=[Depends(require_api_key)])
async def upload_plugin_zip(request: Request):
    """Reçoit un zip de plugin en corps brut (nom dans X-Filename), le valide puis le stocke."""
    import tempfile
    filename = request.headers.get("x-filename", "plugin.zip")
    fd, tmp_path = tempfile.mkstemp(dir=plugin_zip.PLUGINS_DIR, suffix=".upload")
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > plugin_zip.MAX_ZIP_BYTES:
                    raise HTTPException(status_code=413, detail="Zip trop volumineux (max 200 Mo)")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Fichier vide")
        try:
            meta = plugin_zip.store_zip(tmp_path, filename)
        except plugin_zip.ZipError as e:
            raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print(f"[audit] zip plugin enregistré : {meta['slug']} v{meta['version']} ({meta['size']} octets)")
    return meta

@app.get("/api/plugins/zips", dependencies=[Depends(require_api_key)])
def list_plugin_zips():
    return {"zips": plugin_zip.list_zips()}

@app.post("/api/plugins/zip/{slug}/delete", dependencies=[Depends(require_api_key)])
def delete_plugin_zip(slug: str):
    try:
        deleted = plugin_zip.delete_zip(slug)
    except plugin_zip.ZipError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Zip introuvable")
    print(f"[audit] zip plugin supprimé : {slug}")
    return {"success": True}

@app.get("/dl/plugin/{slug}/{token}")
def download_plugin_zip(slug: str, token: str):
    """Public (l'agent WP n'envoie aucun header) : protégé uniquement par le token HMAC à durée limitée."""
    if not plugin_zip.verify_token(slug, token):
        raise HTTPException(status_code=403, detail="Lien invalide ou expiré")
    return FileResponse(plugin_zip.zip_path(slug), media_type="application/zip", filename=f"{slug}.zip")

class PluginInstallRequest(BaseModel):
    slug: str
    urls: List[str]
    license_key: Optional[str] = None # Breakdance uniquement : appliquée après l'installation

class PluginDeleteRequest(BaseModel):
    plugin_path: str
    urls: List[str]

class PluginRefreshRequest(BaseModel):
    scope: str = "parc"

_PLUGIN_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+\.php$")

_pz_jobs: dict = {}

async def _run_site_job(job_id: str, urls: List[str], action, label: str, refresh_after: bool = True):
    """Exécute `action(url, state)` sur chaque site, avec les protections communes des jobs de
    masse : 2 sites en parallèle, pause entre deux sites, arrêt si le serveur ou le pare-feu bloque.

    `action` tourne dans un thread et renvoie {"success": bool, "error": str} ; en cas de succès,
    {"message": str, "status": "done" (défaut) ou "error", "fields": {...}} est publié dans l'état
    du site. Avec refresh_after, results.json est relu pour ce site avant le statut final."""
    import asyncio, random
    job = _pz_jobs[job_id]
    sem = asyncio.Semaphore(_BD_CONCURRENCY)
    guard = job_guard.JobGuard()

    async def one(url: str):
        state = job["sites"][url]
        async with sem:
            try:
                ip = await asyncio.to_thread(job_guard.resolve_ip, url)
                skip = guard.skip_reason(ip)
                if skip:
                    state.update(status="skipped", message=skip)
                    job["aborted_reason"] = guard.aborted_reason
                    return

                state["status"] = "installing"
                res = await asyncio.to_thread(action, url, state)
                if not res.get("success"):
                    msg = res.get("error", "Erreur inconnue")
                    guard.record(ip, job_guard.classify_error(msg))
                    job["aborted_reason"] = guard.aborted_reason
                    print(f"[audit] ❌ {label} échoué pour {url}: {msg}")
                    state.update(status="error", message=msg)
                    return
                guard.record(ip, "ok")

                if refresh_after:
                    refresh = await asyncio.to_thread(refresh_site_in_results, url)
                    if not refresh.get("success"):
                        print(f"[audit] ⚠️  results.json non rafraîchi pour {url}: {refresh.get('error')}")
                state.update(status=res.get("status", "done"), message=res.get("message"), **res.get("fields", {}))
            except Exception as e:
                print(f"[audit] ❌ {label} : erreur inattendue pour {url}: {e}")
                state.update(status="error", message=str(e)[:200])
            finally:
                await asyncio.sleep(random.uniform(*_BD_PACE_SECONDS))

    try:
        await asyncio.gather(*(one(u) for u in urls))
    finally:
        job["finished"] = True

def _start_site_job(slug: str, urls: List[str], action, label: str, refresh_after: bool = True) -> str:
    """Crée le job (un seul à la fois, tous types confondus) et le lance en arrière-plan."""
    import asyncio, uuid
    if any(not j["finished"] for j in list(_pz_jobs.values()) + list(_bd_jobs.values())):
        raise HTTPException(status_code=409, detail="Une opération est déjà en cours")
    job_id = uuid.uuid4().hex
    _pz_jobs[job_id] = {
        "finished": False,
        "aborted_reason": None,
        "slug": slug,
        "sites": {u: {"status": "pending"} for u in urls},
    }
    for old in list(_pz_jobs)[:-10]:
        if _pz_jobs[old]["finished"]:
            del _pz_jobs[old]
    task = asyncio.create_task(_run_site_job(job_id, urls, action, label, refresh_after))
    _bd_tasks.add(task)
    task.add_done_callback(_bd_tasks.discard)
    return job_id

def _install_action(slug: str, license_key: Optional[str]):
    key = (license_key or "").strip()

    def action(url: str, state: dict) -> dict:
        meta = plugin_zip.get_meta(slug)
        if not meta:
            raise RuntimeError("Zip du plugin introuvable (supprimé ?)")
        # Lien généré au moment de traiter CE site : il n'expire pas pendant l'attente.
        zip_url = plugin_zip.build_download_url(slug, meta["sha256"])
        inst = install_plugin_zip(url, zip_url, meta["sha256"])
        if not inst.get("success"):
            return inst

        status, message = "done", inst.get("message", "Installé")
        if slug == "breakdance":
            if key:
                state.update(message="Activation de la licence…")
                act = activate_license_via_agent(url, key)
                if act.get("success"):
                    message = act.get("message", "Installé et licence activée")
                else:
                    err = act.get("error", "Activation échouée")
                    print(f"[audit] ❌ activation licence échouée pour {url}: {err}")
                    status, message = "error", f"Installé, mais licence non activée : {err}"
            else:
                message = "Installé (aucune clé fournie, licence non activée)"
        return {
            "success": True, "status": status, "message": message,
            "fields": {k: inst.get(k) for k in ("installed", "old_version", "new_version", "is_active")},
        }
    return action

async def _run_plugin_install_job(job_id: str, slug: str, urls: List[str], license_key: Optional[str] = None):
    await _run_site_job(job_id, urls, _install_action(slug, license_key), f"installation {slug}")

@app.post("/api/plugins/install", dependencies=[Depends(require_api_key)])
async def install_plugin_on_sites(request: Request, body: PluginInstallRequest):
    """Installe (ou met à jour) le zip stocké sur les sites choisis, en arrière-plan. Suivi par
    GET /api/plugins/jobs/{job_id}. Un plugin déjà présent est remplacé en gardant son état
    d'activation ; une nouvelle installation est activée."""
    meta = plugin_zip.get_meta(body.slug)
    if not meta:
        raise HTTPException(status_code=404, detail="Zip introuvable : uploade-le d'abord")
    try:
        plugin_zip.build_download_url(body.slug, "0" * 64)  # échoue tôt : PUBLIC_BACKEND_URL absent ou non HTTPS
    except plugin_zip.ZipError as e:
        raise HTTPException(status_code=500, detail=str(e))

    urls = list(dict.fromkeys(body.urls))
    if not urls:
        raise HTTPException(status_code=400, detail="Aucun site sélectionné")
    await _check_site_urls(urls)

    print(f"[audit] /api/plugins/install from {request.client.host} plugin={body.slug} v{meta['version']} sites={len(urls)}")
    job_id = _start_site_job(
        body.slug, urls, _install_action(body.slug, body.license_key), f"installation {body.slug}"
    )
    return {"job_id": job_id}

def _delete_action(plugin_path: str):
    def action(url: str, state: dict) -> dict:
        res = delete_plugin_via_agent(url, plugin_path)
        if not res.get("success"):
            return res
        return {"success": True, "message": res.get("message", "Supprimé"), "fields": {"deleted": True}}
    return action

async def _run_plugin_delete_job(job_id: str, plugin_path: str, urls: List[str]):
    await _run_site_job(job_id, urls, _delete_action(plugin_path), f"suppression {plugin_path}")

@app.post("/api/plugins/delete", dependencies=[Depends(require_api_key)])
async def delete_plugin_on_sites(request: Request, body: PluginDeleteRequest):
    """Supprime un plugin (désactivation + fichiers) sur les sites choisis, en arrière-plan.
    Suivi par GET /api/plugins/jobs/{job_id}, comme l'installation."""
    if not _PLUGIN_PATH_RE.match(body.plugin_path):
        raise HTTPException(status_code=400, detail="Chemin de plugin invalide (attendu : dossier/fichier.php)")
    if body.plugin_path.split("/")[0] == AGENT_PLUGIN_FOLDER:
        raise HTTPException(status_code=400, detail="L'agent WeRocket ne peut pas être supprimé")

    urls = list(dict.fromkeys(body.urls))
    if not urls:
        raise HTTPException(status_code=400, detail="Aucun site sélectionné")
    await _check_site_urls(urls)

    print(f"[audit] /api/plugins/delete from {request.client.host} plugin={body.plugin_path} sites={len(urls)}")
    job_id = _start_site_job(
        body.plugin_path, urls, _delete_action(body.plugin_path), f"suppression {body.plugin_path}"
    )
    return {"job_id": job_id}

def _refresh_action(url: str, state: dict) -> dict:
    res = refresh_site_in_results(url)
    return res if not res.get("success") else {"success": True, "message": "Liste mise à jour"}

@app.post("/api/plugins/refresh", dependencies=[Depends(require_api_key)])
async def refresh_plugins_list(request: Request, body: PluginRefreshRequest):
    """Relit chaque site du parc (ou de l'onglet Test) : plugins, mises à jour, thèmes, licences,
    versions — sans capture ni IA. En arrière-plan, suivi par GET /api/plugins/jobs/{job_id}."""
    entries = _read_entries(_results_files(body.scope)[0])
    urls = list(dict.fromkeys(e["url"] for e in entries if isinstance(e, dict) and e.get("url")))
    if not urls:
        raise HTTPException(status_code=400, detail="Aucun site connu : lance d'abord un audit")

    print(f"[audit] /api/plugins/refresh from {request.client.host} scope={body.scope} sites={len(urls)}")
    # refresh_after=False : l'action elle-même relit le site et réécrit results.json.
    job_id = _start_site_job("refresh", urls, _refresh_action, "actualisation", refresh_after=False)
    return {"job_id": job_id}

@app.get("/api/plugins/installed", dependencies=[Depends(require_api_key)])
def list_installed_plugins(scope: str = "parc"):
    """Plugins installés sur les sites, avec leurs sites (onglet Supprimer un plugin). À part du
    récap : la liste est volumineuse (tous les plugins de tous les sites) et ne sert qu'à cet onglet."""
    return {"plugins": _compute_installed_plugins(_read_entries(_results_files(scope)[0]))}

@app.get("/api/plugins/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def plugin_install_job_status(job_id: str):
    job = _pz_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable (backend redémarré ?)")
    return job

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

def _read_last_audit_finished_at(path: str = LAST_AUDIT_FILE) -> Optional[str]:
    """Horodatage (UTC) de fin du dernier audit, écrit par run_audit() à côté
    de results.json — None si aucun audit n'a encore tourné."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("finished_at")
    except Exception:
        return None

@app.get("/api/recap", dependencies=[Depends(require_api_key)])
async def get_recap(scope: str = "parc"):
    """
    Vue d'ensemble de l'audit actuel (sites à IA faible, fréquence des MAJ
    par plugin, PHP obsolète, répartition Agent/Scraping) — calculée depuis
    results.json, exactement comme /api/results, pour rester cohérente avec
    ce qui est affiché dans le tableau. Fallback PocketBase (vue du parc
    entier) uniquement si aucun audit n'a encore été lancé. Avec scope=test : l'échantillon
    de l'onglet Test (vide tant qu'aucun test n'a tourné, jamais de repli sur le parc).
    """
    results_file, last_audit_file = _results_files(scope)
    if scope == "test" and not os.path.exists(results_file):
        recap = _compute_recap_from_results([])
        recap["last_audit_finished_at"] = None
        return recap
    if os.path.exists(results_file):
        try:
            with open(results_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            recap = _compute_recap_from_results(data)
            recap["last_audit_finished_at"] = _read_last_audit_finished_at(last_audit_file)
            return recap
        except Exception as e:
            print(f"⚠️  Erreur lecture results.json pour /api/recap: {e}")

    try:
        import asyncio
        recap = await asyncio.to_thread(call_pb_worker, "get_recap_summary", timeout=60)
        recap["last_audit_finished_at"] = _read_last_audit_finished_at()
        return recap
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Pour lancer : uvicorn main:app --reload
import os
import json
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from audit_engine import (
    run_audit,
    update_plugin_via_agent,
    update_core_via_agent,
    refresh_site_in_results,
    call_pb_worker,
    RESULTS_FILE,
)

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

# État de progression de l'audit en cours (partagé entre background task et API)
_audit_progress: dict = {"status": "idle", "percent": 0, "label": ""}

def _on_progress(data: dict):
    _audit_progress.update(data)

# Initialisation FastAPI
app = FastAPI(
    title="WeRocket Maintenance API",
    description="API pour auditer les sites WordPress",
    version="1.0.0"
)

# Configuration CORS (Pour que ton Next.js puisse parler à ton Python)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En dev, on autorise tout. En prod, mets l'URL de ton Vercel/Netlify
    allow_credentials=True,
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

class PaginatedResults(BaseModel):
    items: List[SiteResult]
    total: int
    page: int
    per_page: int
    total_pages: int

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

@app.post("/api/scan", response_model=ScanResponse)
async def launch_scan(
    request: Request,
    background_tasks: BackgroundTasks,
    scan_request: Optional[ScanRequest] = None,
):
    """
    Lance l'audit en tâche de fond (ne bloque pas l'interface)
    """
    print(f"[audit] /api/scan from {request.client.host} origin={request.headers.get('origin')} limit={scan_request.limit if scan_request else None}")
    limit = None
    if scan_request and scan_request.url:
        sites_list = [{"Client": scan_request.client_name, "URL": scan_request.url}]
    else:
        sites_list = None  # Le moteur lira les sites actifs depuis PocketBase
        limit = scan_request.limit if scan_request else None

    _audit_progress.update({"status": "running", "percent": 0, "label": "Démarrage de l'audit..."})
    background_tasks.add_task(run_audit, sites_list, _on_progress, limit)

    return ScanResponse(
        message="Audit lancé en arrière-plan 🚀",
        status="pending",
        url=scan_request.url if scan_request and scan_request.url else ("BDD" + (f" (limité à {limit})" if limit else ""))
    )

@app.get("/api/results", response_model=PaginatedResults)
async def get_results(request: Request, page: int = 1, per_page: int = 25, search: str = ""):
    print(f"[audit] /api/results from {request.client.host} origin={request.headers.get('origin')} page={page} search={search!r}")

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

            total = len(data)
            total_pages = max(1, -(-total // per_page))  # ceil division
            start = (page - 1) * per_page
            items = data[start:start + per_page]

            return PaginatedResults(
                items=items, total=total, page=page, per_page=per_page, total_pages=total_pages
            )
        except Exception as e:
            print(f"⚠️  Erreur lecture results.json: {e}")

    # Fallback : PocketBase (pagination + recherche natives, chaque projet
    # porte déjà son dernier état — pas besoin de dédoublonnage)
    try:
        return call_pb_worker("get_results_summary", {"page": page, "per_page": per_page, "search": search})
    except Exception as e:
        print(f"❌ Erreur PocketBase : {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-plugin", response_model=PluginUpdateResponse)
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

@app.post("/api/update-core", response_model=CoreUpdateResponse)
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

@app.get("/api/progress")
async def get_progress():
    return _audit_progress

@app.get("/api/stats")
async def get_stats():
    """
    Statistiques globales pour les Widgets du Dashboard
    """
    try:
        return call_pb_worker("get_stats_summary")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Pour lancer : uvicorn main:app --reload
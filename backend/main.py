import os
import json
import time
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from dotenv import load_dotenv
from audit_engine import run_audit, update_plugin_via_agent, update_core_via_agent, refresh_site_in_results, RESULTS_FILE

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

def build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL manquant. Configure une URL Supabase avec sslmode=require.")
    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"
    if "connect_timeout=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}connect_timeout=5"
    return database_url

# Connexion PostgreSQL (Supabase)
engine = create_engine(build_database_url(), pool_pre_ping=True, pool_recycle=300)

# ============================================================================
# MODÈLES PYDANTIC (Ce que le Front envoie/reçoit)
# ============================================================================

class ScanRequest(BaseModel):
    url: str # Simplifié en str pour éviter des erreurs de validation trop strictes au début
    client_name: Optional[str] = "Client API"

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

# ============================================================================
# ROUTES API
# ============================================================================

@app.on_event("startup")
def migrate():
    max_attempts = 15
    delay_seconds = 2

    for attempt in range(1, max_attempts + 1):
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == max_attempts:
                raise
            print(f"⏳ Base non prête (tentative {attempt}/{max_attempts}), nouvelle tentative dans {delay_seconds}s...")
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
    print(f"[audit] /api/scan from {request.client.host} origin={request.headers.get('origin')}")
    if scan_request:
        sites_list = [{"Client": scan_request.client_name, "URL": scan_request.url}]
    else:
        sites_list = None  # Le moteur lira les sites depuis la BDD

    _audit_progress.update({"status": "running", "percent": 0, "label": "Démarrage de l'audit..."})
    background_tasks.add_task(run_audit, sites_list, _on_progress)

    return ScanResponse(
        message="Audit lancé en arrière-plan 🚀",
        status="pending",
        url=scan_request.url if scan_request else "BDD"
    )

@app.get("/api/results", response_model=List[SiteResult])
async def get_results(request: Request, limit: int = 100):
    print(f"[audit] /api/results from {request.client.host} origin={request.headers.get('origin')}")

    # Priorité : fichier JSON local (indépendant de la DB)
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data[:limit]
        except Exception as e:
            print(f"⚠️  Erreur lecture results.json: {e}")

    # Fallback : DB
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT DISTINCT ON (project_id)
                    project_name as client,
                    site_url as url,
                    diagnostic_ia as ia_status,
                    score_ia as ia_score,
                    diagnostic_ia_contact as ia_status_contact,
                    score_ia_contact as ia_score_contact,
                    diagnostic_ia_mobile as ia_status_mobile,
                    score_ia_mobile as ia_score_mobile,
                    version_wp as wp_version,
                    version_php as php_version,
                    theme_actif as theme,
                    builder_detecte as builder,
                    nb_updates as updates_count,
                    mises_a_jour as mises_a_jour,
                    methode_scan as methode
                FROM site_audits
                WHERE statut_scan IS NOT NULL
                ORDER BY project_id, created_at DESC
                LIMIT :limit
            """)
            results = conn.execute(query, {"limit": limit}).mappings().all()
            return results
    except Exception as e:
        print(f"❌ Erreur SQL : {e}")
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
        with engine.connect() as conn:
            query = text("""
                SELECT
                    COUNT(*) as total_sites,
                    COUNT(CASE WHEN diagnostic_ia = 'OK' THEN 1 END) as ia_ok,
                    COUNT(CASE WHEN diagnostic_ia = 'ATTENTION' THEN 1 END) as ia_attention,
                    COUNT(CASE WHEN diagnostic_ia = 'ALERTE' THEN 1 END) as ia_alerte,
                    SUM(nb_updates) as total_updates,
                    COUNT(CASE WHEN methode_scan = 'Agent' THEN 1 END) as scans_agent
                FROM (
                    SELECT DISTINCT ON (project_id)
                        diagnostic_ia,
                        nb_updates,
                        methode_scan
                    FROM site_audits
                    WHERE statut_scan IS NOT NULL
                    ORDER BY project_id, created_at DESC
                ) latest
            """)
            
            stats = conn.execute(query).mappings().first()
            
            # Gestion des valeurs nulles si la base est vide
            return {
                "total_sites": stats["total_sites"] or 0,
                "ia_status": {
                    "ok": stats["ia_ok"] or 0,
                    "attention": stats["ia_attention"] or 0,
                    "alerte": stats["ia_alerte"] or 0
                },
                "kpis": {
                    "updates_pending": stats["total_updates"] or 0,
                    "agent_coverage": stats["scans_agent"] or 0
                }
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Pour lancer : uvicorn main:app --reload
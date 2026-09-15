import os
import random
import string
from dotenv import load_dotenv
from pocketbase import PocketBase

load_dotenv()


def _generate_record_id() -> str:
    """
    Génère un id conforme au format PocketBase ([a-z0-9]{15}).

    ⚠️ Nécessaire pour la collection 'projects' : une modification de
    schéma (ajout de latest_mises_a_jour via ensure_fields) a réinitialisé
    par effet de bord le auto_generate_pattern du champ système "id" côté
    serveur (confirmé irréversible via l'API — PocketBase refuse de le
    réécrire une fois vidé). On génère donc l'id nous-mêmes à la création
    plutôt que de compter sur l'auto-génération serveur.
    """
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=15))

POCKETBASE_PUBLIC_URL = os.getenv("POCKETBASE_PUBLIC_URL", "").strip().rstrip("/")
POCKETBASE_ADMIN_EMAIL = os.getenv("POCKETBASE_ADMIN_EMAIL")
POCKETBASE_ADMIN_PASSWORD = os.getenv("POCKETBASE_ADMIN_PASSWORD")

_pb: PocketBase | None = None


def get_client() -> PocketBase:
    """
    Client PocketBase authentifié (singleton, ré-authentifie automatiquement
    si le token en cache n'est plus valide — la connexion a déjà montré des
    timeouts ponctuels en test, donc on ne présume jamais que le token tenu
    en mémoire est encore bon).
    """
    global _pb
    if _pb is not None and _pb.auth_store.is_valid:
        return _pb

    if not POCKETBASE_PUBLIC_URL:
        raise RuntimeError("POCKETBASE_PUBLIC_URL manquant dans backend/.env")

    pb = PocketBase(POCKETBASE_PUBLIC_URL)
    pb.collection("_superusers").auth_with_password(POCKETBASE_ADMIN_EMAIL, POCKETBASE_ADMIN_PASSWORD)
    _pb = pb
    return _pb


def _project_to_result(r) -> dict:
    """Formate un record `projects` au format attendu par le dashboard (SiteResult)."""
    return {
        "client": r.client_name,
        "url": r.url,
        "ia_status": r.latest_ia_status or "N/A",
        "ia_score": r.latest_ia_score or 0,
        "ia_status_contact": r.latest_ia_status_contact or "N/A",
        "ia_score_contact": r.latest_ia_score_contact or 0,
        "ia_status_mobile": r.latest_ia_status_mobile or "N/A",
        "ia_score_mobile": r.latest_ia_score_mobile or 0,
        "wp_version": r.latest_wp_version or "N/A",
        "php_version": r.latest_php_version or "N/A",
        "theme": r.latest_theme or "N/A",
        "builder": r.latest_builder or "N/A",
        "updates_count": r.latest_updates_count or 0,
        "mises_a_jour": r.latest_mises_a_jour or "Aucune",
        "methode": r.latest_methode_scan or "N/A",
        "licenses": r.latest_licenses or {},
    }


def get_results_summary(limit: int = 100) -> list[dict]:
    """
    Équivalent du SELECT DISTINCT ON (project_id) ... FROM site_audits — sauf
    qu'ici chaque projet EST déjà son propre dernier état (latest_*), donc
    une simple lecture triée suffit, sans dédoublonnage.
    """
    pb = get_client()
    page = pb.collection("projects").get_list(1, limit, {"sort": "-last_audit_at"})
    return [_project_to_result(r) for r in page.items]


def get_stats_summary() -> dict:
    """Statistiques globales pour les widgets du dashboard."""
    pb = get_client()
    projects = pb.collection("projects").get_full_list()

    total_sites = len(projects)
    ia_ok = sum(1 for p in projects if p.latest_ia_status == "OK")
    ia_attention = sum(1 for p in projects if p.latest_ia_status == "ATTENTION")
    ia_alerte = sum(1 for p in projects if p.latest_ia_status == "ALERTE")
    total_updates = sum(p.latest_updates_count or 0 for p in projects)
    scans_agent = sum(1 for p in projects if p.latest_methode_scan == "Agent")

    return {
        "total_sites": total_sites,
        "ia_status": {"ok": ia_ok, "attention": ia_attention, "alerte": ia_alerte},
        "kpis": {"updates_pending": total_updates, "agent_coverage": scans_agent},
    }


def get_active_projects() -> list[dict]:
    """Sites actifs à auditer (remplace le SELECT * FROM projects WHERE is_active)."""
    pb = get_client()
    records = pb.collection("projects").get_full_list(query_params={"filter": "is_active = true"})
    return [
        {
            "project_id": r.id,
            "project_name": r.client_name,
            "site_url": r.url,
        }
        for r in records
    ]


def get_project_by_url(url: str):
    """Retrouve un projet par son URL (unique). Retourne None si absent."""
    pb = get_client()
    try:
        return pb.collection("projects").get_first_list_item(
            pb.filter("url = {:url}", {"url": url})
        )
    except Exception:
        return None


def upsert_project_from_notion(client_name: str, url: str, date_mise_en_ligne, notion_page_id: str) -> dict:
    """
    Crée le projet s'il n'existe pas (par URL), sinon met à jour son nom et sa
    date — équivalent du ON CONFLICT(url) DO UPDATE de l'ancien import Postgres.

    is_active n'est forcé à True qu'à la création : sur une mise à jour, on
    ne touche pas ce champ pour ne pas réactiver silencieusement un site
    désactivé manuellement à chaque resynchronisation Notion.
    """
    pb = get_client()
    existing = get_project_by_url(url)

    data = {
        "client_name": client_name,
        "url": url,
        "date_mise_en_ligne": date_mise_en_ligne.isoformat() if date_mise_en_ligne else None,
    }
    if notion_page_id:
        data["notion_page_id"] = notion_page_id

    if existing:
        return pb.collection("projects").update(existing.id, data)
    data["is_active"] = True
    return pb.collection("projects").create({"id": _generate_record_id(), **data})


def get_random_active_projects(limit: int = 350) -> list[dict]:
    """
    Échantillon aléatoire de sites actifs — utilisé par le script de
    génération du dataset d'entraînement (3_generate_dataset.py).
    PocketBase ne supporte pas un ORDER BY RANDOM() natif, donc on tire
    l'échantillon côté Python après avoir récupéré la liste complète.
    """
    import random
    pb = get_client()
    records = pb.collection("projects").get_full_list(query_params={"filter": "is_active = true"})
    sample = random.sample(records, min(limit, len(records)))
    return [{"client_name": r.client_name, "url": r.url} for r in sample]


def update_project_status(project_id: str, latest: dict) -> dict:
    """
    Met à jour l'état courant d'un projet après un audit. Décale d'abord
    latest_ia_status/latest_updates_count/latest_licenses vers les champs
    previous_* — c'est cette paire previous/latest qui permet au rapport
    nocturne de détecter les changements sans requêter tout l'historique.

    `latest` attend les clés : methode_scan, ia_status, ia_score,
    ia_status_contact, ia_score_contact, ia_status_mobile, ia_score_mobile,
    wp_version, php_version, mysql_version, theme, theme_version, builder,
    builder_version, updates_count, licenses.
    """
    pb = get_client()
    current = pb.collection("projects").get_one(project_id)

    payload = {
        "previous_ia_status": current.latest_ia_status,
        "previous_updates_count": current.latest_updates_count,
        "previous_licenses": current.latest_licenses,

        "latest_methode_scan": latest["methode_scan"],
        "latest_ia_status": latest["ia_status"],
        "latest_ia_score": latest["ia_score"],
        "latest_ia_status_contact": latest["ia_status_contact"],
        "latest_ia_score_contact": latest["ia_score_contact"],
        "latest_ia_status_mobile": latest["ia_status_mobile"],
        "latest_ia_score_mobile": latest["ia_score_mobile"],
        "latest_wp_version": latest["wp_version"],
        "latest_php_version": latest["php_version"],
        "latest_mysql_version": latest["mysql_version"],
        "latest_theme": latest["theme"],
        "latest_theme_version": latest["theme_version"],
        "latest_builder": latest["builder"],
        "latest_builder_version": latest["builder_version"],
        "latest_updates_count": latest["updates_count"],
        "latest_mises_a_jour": latest.get("mises_a_jour", ""),
        "latest_licenses": latest["licenses"],
        "last_audit_at": latest.get("audited_at"),
    }
    return pb.collection("projects").update(project_id, payload)


def create_site_audit(project_id: str, entry: dict) -> dict:
    """Journal brut — une entrée par audit, pour la traçabilité."""
    pb = get_client()
    payload = {"project": project_id, **entry}
    return pb.collection("site_audits").create(payload)


def sync_site_plugins(project_id: str, plugins: list[dict]) -> None:
    """
    Remplace intégralement les site_plugins d'un site par la liste fraîche
    (suppression + recréation en un seul appel /api/batch) — plus simple et
    moins coûteux qu'un upsert plugin par plugin, et le coût reste
    proportionnel au nombre de SITES, pas au nombre de sites × plugins.

    `plugins` : liste de dicts avec plugin_slug, plugin_name, version,
    is_active, update_available, latest_available_version, risk_level.
    """
    pb = get_client()
    existing = pb.collection("site_plugins").get_full_list(
        query_params={"filter": pb.filter("project = {:project}", {"project": project_id})}
    )

    if not existing and not plugins:
        return

    # ⚠️ On n'utilise pas batch.send() du SDK : il encode toujours la requête
    # en multipart avec un champ "@jsonPayload" (prévu pour l'upload de
    # fichiers), même sans fichier — ce que l'API PocketBase rejette
    # (400 "Cannot be blank"). On construit donc le payload JSON attendu
    # nous-même et on l'envoie directement.
    requests_payload = [
        {"method": "DELETE", "url": f"/api/collections/site_plugins/records/{record.id}", "body": None, "headers": {}}
        for record in existing
    ] + [
        {"method": "POST", "url": "/api/collections/site_plugins/records", "body": {"project": project_id, **plugin}, "headers": {}}
        for plugin in plugins
    ]
    pb.send("/api/batch", {"method": "POST", "body": {"requests": requests_payload}})

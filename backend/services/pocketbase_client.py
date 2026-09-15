import os
from dotenv import load_dotenv
from pocketbase import PocketBase

load_dotenv()

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
    """
    pb = get_client()
    existing = get_project_by_url(url)

    data = {
        "client_name": client_name,
        "url": url,
        "date_mise_en_ligne": date_mise_en_ligne.isoformat() if date_mise_en_ligne else None,
        "is_active": True,
    }
    if notion_page_id:
        data["notion_page_id"] = notion_page_id

    if existing:
        return pb.collection("projects").update(existing.id, data)
    return pb.collection("projects").create(data)


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

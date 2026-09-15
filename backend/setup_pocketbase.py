#!/usr/bin/env python3
"""
Crée les collections PocketBase nécessaires à l'audit WeRocket (projects,
site_plugins, site_audits). Idempotent : relance sans risque, les
collections déjà présentes sont laissées telles quelles.

À exécuter une fois avant la première utilisation :
    python3 setup_pocketbase.py
"""
import os
from dotenv import load_dotenv
from pocketbase import PocketBase

load_dotenv()


def get_client() -> PocketBase:
    base = os.getenv("POCKETBASE_PUBLIC_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("POCKETBASE_PUBLIC_URL manquant dans backend/.env")

    pb = PocketBase(base)
    pb.collection("_superusers").auth_with_password(
        os.getenv("POCKETBASE_ADMIN_EMAIL"),
        os.getenv("POCKETBASE_ADMIN_PASSWORD"),
    )
    return pb


def existing_collection_names(pb: PocketBase) -> set[str]:
    return {c.name for c in pb.collections.get_full_list()}


def create_projects_collection(pb: PocketBase):
    body = {
        "name": "projects",
        "type": "base",
        "fields": [
            {"name": "client_name", "type": "text"},
            {"name": "url", "type": "text", "required": True},
            {"name": "is_active", "type": "bool"},
            {"name": "notion_page_id", "type": "text"},
            {"name": "date_mise_en_ligne", "type": "date"},

            # État courant (mis à jour à chaque audit)
            {"name": "latest_methode_scan", "type": "text"},
            {"name": "latest_ia_status", "type": "text"},
            {"name": "latest_ia_score", "type": "number"},
            {"name": "latest_ia_status_contact", "type": "text"},
            {"name": "latest_ia_score_contact", "type": "number"},
            {"name": "latest_ia_status_mobile", "type": "text"},
            {"name": "latest_ia_score_mobile", "type": "number"},
            {"name": "latest_wp_version", "type": "text"},
            {"name": "latest_php_version", "type": "text"},
            {"name": "latest_mysql_version", "type": "text"},
            {"name": "latest_theme", "type": "text"},
            {"name": "latest_theme_version", "type": "text"},
            {"name": "latest_builder", "type": "text"},
            {"name": "latest_builder_version", "type": "text"},
            {"name": "latest_updates_count", "type": "number"},
            {"name": "latest_licenses", "type": "json"},

            # État précédent — uniquement pour le diff du rapport nocturne
            {"name": "previous_ia_status", "type": "text"},
            {"name": "previous_updates_count", "type": "number"},
            {"name": "previous_licenses", "type": "json"},

            {"name": "last_audit_at", "type": "date"},
        ],
        "indexes": [
            "CREATE UNIQUE INDEX idx_projects_url ON projects (url)",
            "CREATE INDEX idx_projects_is_active ON projects (is_active)",
        ],
    }
    return pb.collections.create(body)


def create_site_plugins_collection(pb: PocketBase, projects_id: str):
    body = {
        "name": "site_plugins",
        "type": "base",
        "fields": [
            {
                "name": "project",
                "type": "relation",
                "required": True,
                "collectionId": projects_id,
                "cascadeDelete": True,
                "maxSelect": 1,
            },
            {"name": "plugin_slug", "type": "text"},
            {"name": "plugin_name", "type": "text"},
            {"name": "version", "type": "text"},
            {"name": "is_active", "type": "bool"},
            {"name": "update_available", "type": "bool"},
            {"name": "latest_available_version", "type": "text"},
            {"name": "risk_level", "type": "text"},
        ],
        "indexes": [
            "CREATE INDEX idx_site_plugins_project ON site_plugins (project)",
            "CREATE INDEX idx_site_plugins_slug ON site_plugins (plugin_slug)",
        ],
    }
    return pb.collections.create(body)


def create_site_audits_collection(pb: PocketBase, projects_id: str):
    body = {
        "name": "site_audits",
        "type": "base",
        "fields": [
            {
                "name": "project",
                "type": "relation",
                "required": True,
                "collectionId": projects_id,
                "cascadeDelete": True,
                "maxSelect": 1,
            },
            {"name": "ia_status", "type": "text"},
            {"name": "ia_score", "type": "number"},
            {"name": "ia_status_contact", "type": "text"},
            {"name": "ia_score_contact", "type": "number"},
            {"name": "ia_status_mobile", "type": "text"},
            {"name": "ia_score_mobile", "type": "number"},
            {"name": "wp_version", "type": "text"},
            {"name": "php_version", "type": "text"},
            {"name": "mysql_version", "type": "text"},
            {"name": "theme", "type": "text"},
            {"name": "theme_version", "type": "text"},
            {"name": "builder", "type": "text"},
            {"name": "builder_version", "type": "text"},
            {"name": "methode_scan", "type": "text"},
            {"name": "updates_count", "type": "number"},
            {"name": "licenses", "type": "json"},
        ],
        "indexes": [
            "CREATE INDEX idx_site_audits_project ON site_audits (project)",
        ],
    }
    return pb.collections.create(body)


def enable_batch_api(pb: PocketBase):
    """
    Désactivées par défaut (sécurité), les requêtes /api/batch sont
    nécessaires à sync_site_plugins() (suppression + recréation groupées).
    """
    settings = pb.settings.get_all()
    if settings.get("batch", {}).get("enabled"):
        print("   ⏭️  API batch déjà activée.")
        return
    pb.settings.update({"batch": {"enabled": True, "maxRequests": 50, "timeout": 3, "maxBodySize": 0}})
    print("   ✅ API batch activée")


def main():
    print("🔧 Configuration des collections PocketBase...\n")
    pb = get_client()
    existing = existing_collection_names(pb)

    enable_batch_api(pb)

    if "projects" in existing:
        print("   ⏭️  'projects' existe déjà, on la garde telle quelle.")
        projects = next(c for c in pb.collections.get_full_list() if c.name == "projects")
        projects_id = projects.id
    else:
        projects = create_projects_collection(pb)
        projects_id = projects.id
        print(f"   ✅ 'projects' créée (id={projects_id})")

    if "site_plugins" in existing:
        print("   ⏭️  'site_plugins' existe déjà, on la garde telle quelle.")
    else:
        create_site_plugins_collection(pb, projects_id)
        print("   ✅ 'site_plugins' créée")

    if "site_audits" in existing:
        print("   ⏭️  'site_audits' existe déjà, on la garde telle quelle.")
    else:
        create_site_audits_collection(pb, projects_id)
        print("   ✅ 'site_audits' créée")

    print("\n✅ Terminé.")


if __name__ == "__main__":
    main()

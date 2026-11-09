"""
Synchronisation Notion → PocketBase : détecte les sites passés à « Mis en ligne »
dans Notion et les ajoute à la collection `projects` (donc dans les audits).

Reprend la logique de scripts_entrainement/1_extraction_dataset.py (appel API
Notion) et 2_init_db.py (nettoyage + upsert), sans passer par un CSV.

Règles :
- un site déjà connu n'est jamais réactivé (is_active reste tel quel) ;
- un site retiré de Notion ou repassé à un autre état n'est PAS désactivé :
  on ne fait qu'ajouter / mettre à jour le nom et la date.

Doit tourner dans le sous-processus pb_worker (cf. commentaire en tête de
pb_worker.py), via la commande « sync_notion ».
"""
import os
from datetime import date
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from services import pocketbase_client as pbc

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

NOTION_VERSION = "2022-06-28"


def is_configured() -> bool:
    return bool(os.getenv("NOTION_TOKEN") and os.getenv("DATABASE_ID"))


def normalize_url(raw_url):
    """Force le schéma https, host en minuscules, retire le slash final."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    url = raw_url.strip().split(" ")[0]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def fetch_notion_sites() -> list[dict]:
    """Tous les sites « Mis en ligne » de la base Notion (avec pagination)."""
    headers = {
        "Authorization": "Bearer " + os.getenv("NOTION_TOKEN", ""),
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }
    url = f"https://api.notion.com/v1/databases/{os.getenv('DATABASE_ID')}/query"
    payload = {"filter": {"property": "État", "status": {"equals": "Mis en ligne"}}}

    sites, next_cursor = [], None
    while True:
        if next_cursor:
            payload["start_cursor"] = next_cursor
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"API Notion HTTP {resp.status_code} : {resp.text[:200]}")
        data = resp.json()

        for page in data["results"]:
            props = page["properties"]
            title = props.get("Client", {}).get("title") or []
            client = "".join(t.get("text", {}).get("content", "") for t in title).strip() or "Inconnu"
            site_url = normalize_url((props.get("Lien site") or {}).get("url"))
            if not site_url:
                continue
            d = (props.get("Mise en ligne") or {}).get("date")
            sites.append({
                "client_name": client,
                "url": site_url,
                "date_mise_en_ligne": d["start"][:10] if d and d.get("start") else None,
                "notion_page_id": page["id"],
            })

        if not data.get("has_more"):
            break
        next_cursor = data["next_cursor"]
    return sites


def sync() -> dict:
    """
    Ajoute les nouveaux sites, met à jour nom/date des existants.
    `created` contient, pour chaque nouveau site, de quoi le passer à run_audit
    (project_id, project_name, site_url).
    """
    if not is_configured():
        raise RuntimeError("NOTION_TOKEN ou DATABASE_ID manquant")

    # Doublons d'URL dans Notion : on garde la mise en ligne la plus récente.
    by_url: dict[str, dict] = {}
    for s in sorted(fetch_notion_sites(), key=lambda s: s["date_mise_en_ligne"] or ""):
        by_url[s["url"]] = s

    pb = pbc.get_client()
    existing = {r.url: r for r in pb.collection("projects").get_full_list()}

    created, updated = [], 0
    for url, s in by_url.items():
        d = date.fromisoformat(s["date_mise_en_ligne"]) if s["date_mise_en_ligne"] else None
        rec = existing.get(url)
        if rec is None:
            new = pbc.upsert_project_from_notion(s["client_name"], url, d, s["notion_page_id"])
            created.append({"project_id": new.id, "project_name": s["client_name"], "site_url": url})
            continue
        same = (
            rec.client_name == s["client_name"]
            and (str(rec.date_mise_en_ligne or "")[:10] or None) == s["date_mise_en_ligne"]
        )
        if not same:
            pbc.upsert_project_from_notion(s["client_name"], url, d, s["notion_page_id"])
            updated += 1

    return {"notion_total": len(by_url), "created": created, "updated": updated}

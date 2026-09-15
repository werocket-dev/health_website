import os
import sys
from urllib.parse import urlparse

import pandas as pd
from dotenv import load_dotenv

# Chargement des clés depuis le .env du backend (un seul fichier partagé)
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

# Réutilise le client PocketBase du backend plutôt que d'en dupliquer un ici
sys.path.insert(0, BACKEND_DIR)
from services.pocketbase_client import upsert_project_from_notion  # noqa: E402


def normalize_url(raw_url):
    """Force le schéma https, met le host en minuscules, retire le slash final."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None

    url = raw_url.strip().split(" ")[0]
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return None

    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def load_and_clean(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    df["client_name"] = df["Client"].astype(str).str.strip()
    df["url"] = df["URL"].apply(normalize_url)
    df["date_mise_en_ligne"] = pd.to_datetime(df["Date_Mise_en_ligne"], errors="coerce", utc=True).dt.date
    df["notion_page_id"] = df.get("Notion_Page_ID")

    total = len(df)

    # Lignes sans URL exploitable (vide, ou domaine non reconnaissable)
    invalid_mask = df["url"].isna()
    invalid = df[invalid_mask]
    if len(invalid):
        print(f"⚠️  {len(invalid)} ligne(s) ignorée(s) — URL invalide :")
        for _, row in invalid.iterrows():
            print(f"      • {row['client_name']!r} → {row['URL']!r}")
    df = df[~invalid_mask]

    # Doublons d'URL (même site entré plusieurs fois dans Notion) :
    # on garde la ligne la plus récente (date de mise en ligne la plus tardive,
    # les NaT comptent comme la plus ancienne).
    df = df.sort_values("date_mise_en_ligne", na_position="first")
    dup_mask = df.duplicated(subset="url", keep="last")
    dropped = df[dup_mask]
    if len(dropped):
        print(f"⚠️  {len(dropped)} doublon(s) d'URL — on garde la ligne la plus récente :")
        for _, row in dropped.iterrows():
            print(f"      • {row['client_name']!r} ({row['url']}) — écrasé par une entrée plus récente")
    df = df[~dup_mask]

    print(f"📊 {total} lignes lues → {len(df)} projets valides après nettoyage/dédoublonnage.")
    return df[["client_name", "url", "date_mise_en_ligne", "notion_page_id"]]


def import_projects(df: pd.DataFrame):
    rows = df.where(pd.notnull(df), None).to_dict(orient="records")

    for row in rows:
        upsert_project_from_notion(
            client_name=row["client_name"],
            url=row["url"],
            date_mise_en_ligne=row["date_mise_en_ligne"],
            notion_page_id=row.get("notion_page_id") or "",
        )

    print(f"✅ SUCCÈS ! {len(rows)} projets insérés/mis à jour dans PocketBase (collection 'projects').")


def init_database():
    csv_path = os.path.join(os.path.dirname(__file__), "data", "sites_werocket.csv")
    print("⏳ Lecture du CSV...")
    try:
        df = load_and_clean(csv_path)
    except FileNotFoundError:
        print(f"❌ Erreur : '{csv_path}' introuvable.")
        print("   Lance d'abord : python3 1_extraction_dataset.py")
        return

    if df.empty:
        print("❌ Aucun projet valide à importer.")
        return

    print("🚀 Import dans PocketBase (collection 'projects')...")
    import_projects(df)


if __name__ == "__main__":
    init_database()

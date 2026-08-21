import os
from urllib.parse import urlparse

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Chargement des clés depuis le .env du backend (un seul fichier partagé)
BACKEND_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
load_dotenv(BACKEND_ENV_PATH)


def build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL manquant dans backend/.env")
    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"
    return database_url


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
    engine = create_engine(build_database_url(), pool_pre_ping=True)

    upsert_sql = text("""
        INSERT INTO projects (client_name, url, date_mise_en_ligne, notion_page_id)
        VALUES (:client_name, :url, :date_mise_en_ligne, :notion_page_id)
        ON CONFLICT (url) DO UPDATE SET
            client_name = EXCLUDED.client_name,
            date_mise_en_ligne = EXCLUDED.date_mise_en_ligne,
            notion_page_id = COALESCE(EXCLUDED.notion_page_id, projects.notion_page_id)
    """)

    rows = df.where(pd.notnull(df), None).to_dict(orient="records")

    with engine.begin() as conn:
        for row in rows:
            conn.execute(upsert_sql, row)

    print(f"✅ SUCCÈS ! {len(rows)} projets insérés/mis à jour dans la table 'projects'.")


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

    print("🚀 Import dans Supabase (table 'projects')...")
    import_projects(df)


if __name__ == "__main__":
    init_database()

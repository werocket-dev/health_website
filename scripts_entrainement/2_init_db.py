import pandas as pd
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

# On charge les variables du .env
load_dotenv()

# --- CONFIGURATION SÉCURISÉE ---
user = os.getenv("POSTGRES_USER")
password = os.getenv("POSTGRES_PASSWORD")
host = "localhost"
port = os.getenv("DB_PORT", "5433") # 5433 par défaut si non trouvé
db_name = os.getenv("POSTGRES_DB")

# Construction dynamique de l'URI
DB_URI = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"

def clean_url(url):
    """Nettoie les URLs sales (ex: suppression des IPs à la fin)"""
    if not isinstance(url, str):
        return None
    # On garde juste la partie http(s)://domaine.com
    # On coupe s'il y a un espace suivi d'une IP ou autre
    clean = url.split(" ")[0].strip()
    return clean

def init_database():
    print("⏳ Lecture du CSV...")
    try:
        df = pd.read_csv('data/sites_werocket.csv')
    except FileNotFoundError:
        print("❌ Erreur : Le fichier 'data/sites_werocket.csv' est introuvable.")
        print("   Lance d'abord : python3 scripts/1_extraction_dataset.py")
        return

    print(f"📊 {len(df)} sites trouvés. Nettoyage en cours...")

    # 1. Nettoyage des données
    # On applique la fonction clean_url sur la colonne URL
    df['URL'] = df['URL'].apply(clean_url)
    
    # On s'assure que la date est au bon format (optionnel mais mieux)
    df['Date_Mise_en_ligne'] = pd.to_datetime(df['Date_Mise_en_ligne'], errors='coerce')

    # 2. Connexion BDD
    engine = create_engine(DB_URI)

    print("🚀 Insertion dans PostgreSQL...")
    
    # La magie de Pandas : to_sql crée la table automatiquement !
    # if_exists='replace' : Si on relance le script, on écrase et on recommence (pratique pour le dev)
    df.to_sql('sites', engine, if_exists='replace', index=False)

    print("✅ SUCCÈS ! Base de données initialisée.")
    print("   Table : 'sites'")
    print(f"   Lignes insérées : {len(df)}")

if __name__ == "__main__":
    init_database()
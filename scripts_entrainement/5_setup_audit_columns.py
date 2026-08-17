#!/usr/bin/env python3
"""
Script de configuration pour l'audit WeRocket
Ajoute toutes les colonnes nécessaires pour :
- L'analyse IA
- La détection des plugins et builders
- L'Agent WordPress (versions système, thème, etc.)

À exécuter AVANT la première utilisation de run_global_audit.py
"""
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Chargement de la config
load_dotenv()
engine = create_engine(f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@localhost:{os.getenv('DB_PORT')}/{os.getenv('POSTGRES_DB')}")

print("🔧 Configuration de la base de données pour l'audit WeRocket...\n")

sql_commands = [
    # ===== COLONNES DE BASE (Audit IA) =====
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Statut_Scan" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Diagnostic_IA" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Score_IA" INTEGER;',
    
    # ===== COLONNES BUILDERS & PLUGINS =====
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Builder_Detecte" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Version_Builder" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Plugins_Detectes" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Mises_A_Jour" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Nb_Updates" INTEGER DEFAULT 0;',
    
    # ===== COLONNES AGENT WORDPRESS (Mode Hybride) =====
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Methode_Scan" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Version_WP" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Version_PHP" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Version_MySQL" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Theme_Actif" TEXT;',
    'ALTER TABLE sites ADD COLUMN IF NOT EXISTS "Version_Theme" TEXT;'
]

print("📊 Ajout des colonnes :\n")

with engine.begin() as conn:
    for sql in sql_commands:
        try:
            conn.execute(text(sql))
            # Extraction du nom de la colonne
            colonne = sql.split('"')[1]
            print(f"   ✅ {colonne}")
        except Exception as e:
            error = str(e)[:80]
            print(f"   ⚠️  Erreur : {error}")

print("\n" + "="*70)
print("✅ Configuration terminée !")
print("="*70)
print("\n📋 Récapitulatif des colonnes :\n")
print("   🤖 Diagnostic_IA     → Résultat de l'analyse visuelle")
print("   📊 Score_IA          → Score de confiance (0-100%)")
print("   🎨 Builder_Detecte   → Divi, Elementor, Breakdance...")
print("   🔌 Plugins_Detectes  → Liste des plugins trouvés")
print("   🔄 Mises_A_Jour      → Plugins nécessitant une MAJ")
print("\n   🔐 Methode_Scan      → 'Agent' ou 'Scraping (Externe)'")
print("   🌐 Version_WP        → Version de WordPress")
print("   🐘 Version_PHP       → Version de PHP")
print("   💾 Version_MySQL     → Version de MySQL/MariaDB")
print("   🎨 Theme_Actif       → Nom du thème actif")
print("   📦 Version_Theme     → Version du thème")
print("\n💡 Tu peux maintenant lancer : python3 run_global_audit.py\n")

import requests
import json
import csv
import os
from dotenv import load_dotenv
from datetime import datetime

# Chargement des clés
load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")

headers = {
    "Authorization": "Bearer " + NOTION_TOKEN,
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_all_sites():
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    
    # --- FILTRE CORRIGÉ ---
    # On remplace "select" par "status" car ta colonne est une propriété de type Statut
    payload = {
        "filter": {
            "property": "État",
            "status": {
                "equals": "Mis en ligne"
            }
        }
    }
    
    all_sites = []
    has_more = True
    next_cursor = None

    print("🚀 Démarrage de l'extraction des sites 'Mis en ligne'...")

    while has_more:
        if next_cursor:
            payload["start_cursor"] = next_cursor

        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            print(f"❌ Erreur API : {response.text}")
            break

        data = response.json()
        
        for page in data["results"]:
            props = page["properties"]
            
            # --- EXTRACTION DES CHAMPS ---
            try:
                # 1. Nom du Client (Titre) - On cherche la propriété 'Client'
                client_name = "Inconnu"
                # Notion retourne une liste, on prend le premier élément
                if props.get("Client", {}).get("title"):
                    client_name = "".join([t["text"]["content"] for t in props["Client"]["title"]])
                
                # 2. URL (Lien site)
                site_url = props.get("Lien site", {}).get("url")
                
                # 3. Date de mise en ligne
                date_online = "Non renseignée"
                if props.get("Mise en ligne", {}).get("date"):
                    date_online = props["Mise en ligne"]["date"]["start"]

                # On garde seulement si on a une URL valide
                if site_url:
                    all_sites.append({
                        "Client": client_name,
                        "URL": site_url,
                        "Date_Mise_en_ligne": date_online,
                        "Statut_Scan": "Pending"
                    })
                    print(f"✅ Trouvé : {client_name}")
                
            except Exception as e:
                # On ignore les erreurs mineures pour ne pas bloquer le script
                print(f"⚠️ Info manquante sur une ligne (ignoré) : {e}")

        has_more = data["has_more"]
        next_cursor = data["next_cursor"]

    return all_sites

def save_to_csv(sites, filename="data/sites_werocket.csv"):
    if not sites:
        print("Aucun site à sauvegarder.")
        return

    # Création du CSV
    keys = sites[0].keys()
    with open(filename, 'w', newline='', encoding='utf-8') as output_file:
        dict_writer = csv.DictWriter(output_file, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(sites)
    
    print(f"\n🎉 SUCCÈS ! {len(sites)} sites sauvegardés dans '{filename}'")

# --- EXÉCUTION ---
if __name__ == "__main__":
    sites = get_all_sites()
    save_to_csv(sites)
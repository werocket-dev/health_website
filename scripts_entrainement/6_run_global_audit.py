import os
import asyncio
import random
import numpy as np
import pandas as pd
import tensorflow as tf
from playwright.async_api import async_playwright
from sqlalchemy import create_engine, text
from tensorflow.keras.models import load_model
import requests
import re
import hmac
import hashlib
import time
import json
from dotenv import load_dotenv

# --- INITIALISATION ---
load_dotenv()

# Chargement des secrets (OBLIGATOIRE pour l'Agent)
WEROCKET_AGENT_TOKEN = os.getenv("WEROCKET_AGENT_TOKEN")
WEROCKET_AGENT_HEADER_KEY = os.getenv("WEROCKET_AGENT_HEADER_KEY")

# Vérification de sécurité
if not WEROCKET_AGENT_TOKEN or not WEROCKET_AGENT_HEADER_KEY:
    print("\n⚠️  AVERTISSEMENT : Les clés de l'Agent WeRocket ne sont pas configurées dans le .env")
    print("   Le script fonctionnera en mode SCRAPING uniquement (méthode dégradée)")
    print("   Pour activer l'Agent, ajoutez dans votre .env :")
    print("   WEROCKET_AGENT_TOKEN=votre_token_secret")
    print("   WEROCKET_AGENT_HEADER_KEY=votre_header_secret\n")
    AGENT_AVAILABLE = False
else:
    AGENT_AVAILABLE = True
    print("✅ Agent WeRocket configuré - Mode hybride activé\n")

engine = create_engine(f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@localhost:{os.getenv('DB_PORT')}/{os.getenv('POSTGRES_DB')}")
model = load_model("werocket_vision_model.h5")

# Dossier pour les preuves visuelles
SCREENSHOT_DIR = "screenshots_audit"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# --- FONCTIONS TECHNIQUES ---

def predict_visual(img_path):
    """Analyse visuelle par IA avec score de confiance"""
    try:
        img = tf.keras.utils.load_img(img_path, target_size=(224, 224))
        img_array = tf.keras.utils.img_to_array(img) / 255.0
        img_array = np.expand_dims(img_array, axis=0)
        score = model.predict(img_array, verbose=0)[0][0]
        
        # Score en pourcentage
        confidence = int(score * 100)
        
        if score > 0.7:
            return {"status": "OK", "score": confidence, "message": "Site sain"}
        elif score > 0.5:
            return {"status": "ATTENTION", "score": confidence, "message": "Légers problèmes détectés"}
        else:
            return {"status": "ALERTE", "score": confidence, "message": "Problèmes visuels majeurs"}
    except Exception as e:
        print(f"      ⚠️ Erreur analyse IA : {str(e)[:50]}")
        return {"status": "ERREUR", "score": 0, "message": "Analyse impossible"}

def connect_to_agent(url):
    """
    Connexion sécurisée à l'Agent WeRocket via API REST WordPress.
    
    Sécurité en 3 couches :
    1. Header secret (X-WeRocket-Key)
    2. Token dans le body
    3. Signature HMAC avec timestamp (anti-replay)
    
    Retourne :
    - dict avec toutes les données si succès
    - None si échec (plugin absent, mauvaise auth, etc.)
    """
    if not AGENT_AVAILABLE:
        return None
    
    try:
        # 1. Nettoyage de l'URL (enlever les / finaux)
        clean_url = url.rstrip('/')
        
        # 2. Génération du timestamp actuel (Unix timestamp)
        timestamp = int(time.time())
        
        # 3. Construction du message à signer : URL|timestamp
        message = f"{clean_url}|{timestamp}"
        
        # 4. Création de la signature HMAC-SHA256
        signature = hmac.new(
            WEROCKET_AGENT_TOKEN.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # 5. Préparation des données POST
        payload = {
            'token': WEROCKET_AGENT_TOKEN,
            'timestamp': timestamp,
            'signature': signature
        }
        
        # 6. Préparation du header secret
        headers = {
            'X-WeRocket-Key': WEROCKET_AGENT_HEADER_KEY,
            'Content-Type': 'application/json',
            'User-Agent': 'WeRocket-Audit/2.0'
        }
        
        # 7. Envoi de la requête POST à l'endpoint
        endpoint = f"{clean_url}/wp-json/werocket/v1/status"
        
        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=10,
            verify=True  # Vérifie le certificat SSL
        )
        
        # 8. Analyse de la réponse
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return {
                    'success': True,
                    'method': 'Agent',
                    'data': data
                }
        
        # Échecs spécifiques
        elif response.status_code == 404:
            return {'success': False, 'method': 'Agent', 'error': 'Plugin non installé'}
        elif response.status_code == 403:
            return {'success': False, 'method': 'Agent', 'error': 'Authentification refusée'}
        elif response.status_code == 429:
            return {'success': False, 'method': 'Agent', 'error': 'Rate limit dépassé'}
        else:
            return {'success': False, 'method': 'Agent', 'error': f'HTTP {response.status_code}'}
            
    except requests.exceptions.Timeout:
        return {'success': False, 'method': 'Agent', 'error': 'Timeout'}
    except requests.exceptions.SSLError:
        return {'success': False, 'method': 'Agent', 'error': 'Erreur SSL'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'method': 'Agent', 'error': 'Connexion impossible'}
    except Exception as e:
        return {'success': False, 'method': 'Agent', 'error': str(e)[:50]}

def scan_tech(html_content):
    """Scan technique à partir du HTML déjà récupéré par Playwright (avec JS exécuté)"""
    results = {"wp": "Non", "builder": "Inconnu", "version": "N/A", "html": html_content}
    
    html = html_content.lower()
    
    # Détection WordPress
    if "wp-content" in html: 
        results["wp"] = "Oui"
    
    # Détection des builders
    if "divi" in html or "et-core" in html: 
        results["builder"] = "Divi"
        v = re.search(r'divi/style\.css\?ver=([\d.]+)', html)
        if v: results["version"] = v.group(1)
    elif "breakdance" in html: 
        results["builder"] = "Breakdance"
        v = re.search(r'breakdance/.*?ver=([\d.]+)', html)
        if v: results["version"] = v.group(1)
    elif "elementor" in html: 
        results["builder"] = "Elementor"
        v = re.search(r'elementor/.*?ver=([\d.]+)', html)
        if v: results["version"] = v.group(1)
    
    return results

def scan_plugins_versions(html_content):
    """
    DÉTECTION UNIVERSELLE : Scan automatique de TOUS les plugins WordPress présents.
    
    Méthode :
    1. Cherche tous les chemins wp-content/plugins/nom-plugin/
    2. Extrait le slug du plugin
    3. Cherche la version associée (?ver=X.Y.Z)
    4. Nettoie et formate les noms
    """
    plugins_detected = {}
    
    # Regex pour capturer : wp-content/plugins/[slug]/[fichier]?ver=[version]
    # Exemple : wp-content/plugins/wordfence/css/main.css?ver=8.1.4
    pattern = r'wp-content/plugins/([a-z0-9_-]+)/[^\'"\s]*(?:\?ver=([\d.]+))?'
    
    # On trouve toutes les occurrences
    matches = re.finditer(pattern, html_content)
    
    for match in matches:
        slug = match.group(1)  # Le nom du plugin (ex: "wordfence")
        version = match.group(2) if match.group(2) else None
        
        # Filtrage : on ignore les slugs invalides ou trop courts
        if len(slug) < 2 or slug.startswith('*') or ',' in slug:
            continue
        
        # Normalisation du nom (slug → Nom propre)
        plugin_name = normalize_plugin_name(slug)
        
        # Si on a déjà ce plugin avec une version, on garde la plus précise
        if plugin_name in plugins_detected:
            # Si on trouve une version alors qu'on avait "Détecté", on met à jour
            if version and plugins_detected[plugin_name] == "Détecté":
                plugins_detected[plugin_name] = version
        else:
            # Première détection de ce plugin
            plugins_detected[plugin_name] = version if version else "Détecté"
    
    return plugins_detected

def normalize_plugin_name(slug):
    """
    Convertit un slug de plugin en nom lisible.
    Ex: "wordpress-seo" → "Yoast SEO"
    """
    # Mapping des slugs connus vers leur vrai nom
    KNOWN_PLUGINS = {
        "wordpress-seo": "Yoast SEO",
        "woocommerce": "WooCommerce",
        "contact-form-7": "Contact Form 7",
        "advanced-custom-fields": "ACF",
        "seo-by-rank-math": "Rank Math SEO",
        "elementor": "Elementor",
        "wordfence": "Wordfence",
        "jetpack": "Jetpack",
        "wpforms-lite": "WPForms",
        "all-in-one-seo-pack": "All in One SEO",
        "updraftplus": "UpdraftPlus",
        "wp-smushit": "Smush",
        "w3-total-cache": "W3 Total Cache",
        "wp-super-cache": "WP Super Cache",
        "akismet": "Akismet",
        "duplicate-post": "Duplicate Post",
        "redirection": "Redirection",
        "classic-editor": "Classic Editor",
        "wps-hide-login": "WPS Hide Login",
        "really-simple-ssl": "Really Simple SSL",
        "mailchimp-for-wp": "Mailchimp for WordPress",
        "google-analytics-for-wordpress": "MonsterInsights"
    }
    
    # Si le plugin est dans notre liste, on retourne le nom officiel
    if slug in KNOWN_PLUGINS:
        return KNOWN_PLUGINS[slug]
    
    # Sinon, on formate le slug : "my-plugin" → "My Plugin"
    return slug.replace("-", " ").title()

# --- CACHE GLOBAL POUR L'API WORDPRESS (évite les requêtes répétées) ---
_api_cache = {}
_cache_timestamp = 0
CACHE_DURATION = 86400  # 24 heures en secondes

def get_plugin_slug(plugin_name):
    """
    Convertit le nom lisible d'un plugin en slug pour l'API WordPress.org
    """
    PLUGIN_SLUGS = {
        "Yoast SEO": "wordpress-seo",
        "WooCommerce": "woocommerce",
        "Contact Form 7": "contact-form-7",
        "ACF": "advanced-custom-fields",
        "Rank Math SEO": "seo-by-rank-math",
        "Elementor": "elementor",
        "Wordfence": "wordfence",
        "Jetpack": "jetpack",
        "WPForms": "wpforms-lite",
        "All in One SEO": "all-in-one-seo-pack",
        "UpdraftPlus": "updraftplus",
        "Smush": "wp-smushit",
        "W3 Total Cache": "w3-total-cache",
        "WP Super Cache": "wp-super-cache",
        "Akismet": "akismet",
        "Duplicate Post": "duplicate-post",
        "Redirection": "redirection",
        "Classic Editor": "classic-editor",
        "WPS Hide Login": "wps-hide-login",
        "Really Simple SSL": "really-simple-ssl",
        "Mailchimp for WordPress": "mailchimp-for-wp",
        "MonsterInsights": "google-analytics-for-wordpress"
    }
    
    # Si on connaît le slug, on le retourne
    if plugin_name in PLUGIN_SLUGS:
        return PLUGIN_SLUGS[plugin_name]
    
    # Sinon, on tente de le reconstruire : "My Plugin" → "my-plugin"
    return plugin_name.lower().replace(" ", "-")

def check_updates_api(detected_versions):
    """
    Compare les versions détectées avec l'API officielle WordPress.org + références premium.
    Système de cache 24h pour optimiser les performances.
    UNIVERSEL : Fonctionne avec n'importe quel plugin détecté.
    """
    global _api_cache, _cache_timestamp
    current_time = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0
    
    # Reset du cache si expiré
    if current_time - _cache_timestamp > CACHE_DURATION:
        _api_cache = {}
        _cache_timestamp = current_time
    
    
    PREMIUM_VERSIONS = {
        "Divi": "4.27.0",        
        "Breakdance": "1.9.0"    
    }
    
    updates_needed = []
    up_to_date = []
    unknown_version = []  
    not_found = []       
    
    for plugin, version in detected_versions.items():
        # 1. Plugins sans version détectée → on ne peut pas vérifier
        if version == "Détecté":
            unknown_version.append(plugin)
            continue
            
        latest = None
        
        # 2. Vérification via l'API WordPress pour plugins gratuits
        slug = get_plugin_slug(plugin)
        
        # Check cache d'abord
        if slug in _api_cache:
            latest = _api_cache[slug]
        else:
            try:
                # Requête à l'API officielle WordPress.org
                res = requests.get(
                    f"https://api.wordpress.org/plugins/info/1.2/?action=plugin_information&slug={slug}",
                    timeout=5
                )
                if res.status_code == 200:
                    data = res.json()
                    # L'API retourne False pour les plugins inexistants
                    if data and isinstance(data, dict):
                        latest = data.get("version")
                        if latest:
                            _api_cache[slug] = latest  # Mise en cache
            except Exception as e:
                # Erreur silencieuse, on continue
                pass
                
        # 3. Vérification manuelle pour les Premium (fallback)
        if not latest and plugin in PREMIUM_VERSIONS:
            latest = PREMIUM_VERSIONS[plugin]
            
        # 4. Si toujours pas de version de référence, on ignore ce plugin
        if not latest:
            not_found.append(plugin)
            continue
            
        # 5. Comparaison des numéros (ex: 7.9.0 vs 8.1.4)
        try:
            current_parts = [int(x) for x in version.split('.')]
            latest_parts = [int(x) for x in latest.split('.')]
            
            # Normalisation des longueurs
            while len(current_parts) < len(latest_parts): 
                current_parts.append(0)
            while len(latest_parts) < len(current_parts): 
                latest_parts.append(0)
            
            if current_parts < latest_parts:
                updates_needed.append(f"{plugin} ({version} 🔴 → {latest})")
            else:
                up_to_date.append(f"{plugin} (v{version} 🟢)")
        except:
            # Version non parsable (ex: "1.0-beta")
            unknown_version.append(f"{plugin} ({version})")
    
    # Résumé AMÉLIORÉ
    total_plugins = len(detected_versions)
    verified_plugins = len(updates_needed) + len(up_to_date)
    
    if updates_needed:
        status = "MISES À JOUR DISPONIBLES"
    elif up_to_date and not updates_needed:
        status = "À JOUR"
    else:
        status = "AUCUN PLUGIN VÉRIFIABLE"
    
    return {
        "status": status,
        "updates_needed": updates_needed,
        "up_to_date": up_to_date,
        "unknown_version": unknown_version,
        "not_found": not_found,
        "count_updates": len(updates_needed),
        "count_verified": verified_plugins,
        "count_total": total_plugins
    }

# --- COEUR DU SCANNER ---

async def run_audit():
    # 1. On récupère la liste des sites (limité à 100 pour ne pas surcharger)
    with engine.connect() as conn:
        query = text("SELECT \"Client\", \"URL\" FROM sites LIMIT 100")
        sites = conn.execute(query).mappings().all()

    print(f"🚀 Lancement de l'audit global sur {len(sites)} sites...")
    print(f"⏱️  Mode GENTIL: pause de 2-4s entre chaque site pour éviter les blocages\n")
    
    # Statistiques globales
    stats = {
        "total": len(sites),
        "success": 0,
        "errors": 0,
        "ia_ok": 0,
        "ia_attention": 0,
        "ia_alerte": 0,
        "wp_detecte": 0,
        "builders": {},
        "plugins_total": 0,
        "plugins_verified": 0,
        "plugins_unknown": 0,
        "plugins_not_found": 0,
        "sites_avec_plugins": 0,
        "updates_disponibles": 0,
        "sites_a_jour": 0,
        "methode_agent": 0,
        "methode_scraping": 0,
        "agent_erreurs": {}
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for idx, site in enumerate(sites, 1):
            client = site['Client']
            url = site['URL'].strip().split(" ")[0]
            if not url.startswith("http"): url = "https://" + url
            
            clean_name = client.replace(" ", "_").replace("/", "-")
            img_path = f"{SCREENSHOT_DIR}/{clean_name}.png"

            print(f"🔎 [{idx}/{len(sites)}] Audit : {client}...")

            # A. Capture d'écran
            try:
                # Viewport aléatoire pour imiter un vrai utilisateur
                viewport_width = random.randint(1280, 1920)
                viewport_height = random.randint(720, 1080)
                
                context = await browser.new_context(
                    viewport={'width': viewport_width, 'height': viewport_height},
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = await context.new_page()
                await page.goto(url, wait_until="load", timeout=25000)
                
                # ⚡ CRUCIAL : On attend 4 secondes pour que le JavaScript se termine
                # (Divi, Elementor et autres builders ont besoin de ce délai)
                await page.wait_for_timeout(4000)
                
                # Récupération du HTML COMPLET (avec JS exécuté)
                html_content = await page.content()

                await page.screenshot(path=img_path)
                await page.close()
                await context.close()
                
                # B. Diagnostic IA (avec score)
                diag_ia = predict_visual(img_path)
                
                # ============================================
                # C. MODE HYBRIDE : Agent WordPress en priorité
                # ============================================
                
                # 🔥 ÉTAPE 1 : Tentative de connexion à l'Agent (méthode "Luxe")
                agent_result = connect_to_agent(url)
                
                # Variables pour le stockage BDD (initialisation avec valeurs par défaut)
                methode_scan = "Inconnu"
                tech_info = {"builder": "Inconnu", "version": "N/A"}
                plugins_dict = {}
                updates_info = {"count_updates": 0, "updates_needed": []}
                version_wp = "N/A"
                version_php = "N/A"
                version_mysql = "N/A"
                theme_name = "N/A"
                theme_version = "N/A"
                wp_detecte = False  # Flag pour savoir si WordPress a été détecté
                
                # ✅ CAS 1 : L'Agent a répondu avec succès
                if agent_result and agent_result.get('success'):
                    methode_scan = "Agent"
                    stats["methode_agent"] += 1
                    wp_detecte = True  # L'agent répond = WordPress détecté
                    
                    data = agent_result['data']
                    
                    # Extraction des versions
                    versions = data.get('versions', {})
                    version_wp = versions.get('wordpress', 'N/A')
                    version_php = versions.get('php', 'N/A')
                    version_mysql = versions.get('mysql', 'N/A')
                    
                    # Extraction du thème
                    theme = data.get('theme', {})
                    theme_name = theme.get('name', 'N/A')
                    theme_version = theme.get('version', 'N/A')
                    
                    # Extraction des plugins
                    plugins_data = data.get('plugins', [])
                    plugins_dict = {}
                    updates_needed = []
                    
                    for plugin in plugins_data:
                        plugin_name = plugin.get('name', 'Inconnu')
                        plugin_version = plugin.get('version', 'N/A')
                        is_active = plugin.get('is_active', False)
                        update_available = plugin.get('update_available', {})
                        
                        # Stockage avec statut actif/inactif
                        status = "Actif" if is_active else "Inactif"
                        plugins_dict[plugin_name] = f"{plugin_version} ({status})"
                        
                        # Vérification des mises à jour
                        if update_available.get('available'):
                            new_version = update_available.get('new_version')
                            updates_needed.append(f"{plugin_name} ({plugin_version} 🔴 → {new_version})")
                    
                    # Détection du builder (via les plugins)
                    builder = "Inconnu"
                    builder_version = "N/A"
                    if any('Divi' in p for p in plugins_dict.keys()):
                        builder = "Divi"
                        builder_version = next((v.split('(')[0].strip() for k, v in plugins_dict.items() if 'Divi' in k), 'N/A')
                    elif any('Elementor' in p for p in plugins_dict.keys()):
                        builder = "Elementor"
                        builder_version = next((v.split('(')[0].strip() for k, v in plugins_dict.items() if 'Elementor' in k), 'N/A')
                    elif any('Breakdance' in p for p in plugins_dict.keys()):
                        builder = "Breakdance"
                        builder_version = next((v.split('(')[0].strip() for k, v in plugins_dict.items() if 'Breakdance' in k), 'N/A')
                    
                    tech_info = {"builder": builder, "version": builder_version}
                    updates_info = {
                        "count_updates": len(updates_needed),
                        "updates_needed": updates_needed
                    }
                    
                    print(f"   ✅ [AGENT] IA: {diag_ia['status']} ({diag_ia['score']}%) | WP: {version_wp} | PHP: {version_php} | Builder: {builder} | Plugins: {len(plugins_dict)} | MAJ: {len(updates_needed)}")
                
                # ⚠️ CAS 2 : L'Agent n'a pas répondu - Fallback sur le SCRAPING
                else:
                    methode_scan = "Scraping (Externe)"
                    stats["methode_scraping"] += 1
                    
                    # Log de l'erreur de l'agent
                    if agent_result:
                        error_type = agent_result.get('error', 'Inconnu')
                        stats["agent_erreurs"][error_type] = stats["agent_erreurs"].get(error_type, 0) + 1
                        print(f"   ⚠️  Agent KO ({error_type}) - Fallback scraping HTML...")
                    else:
                        print(f"   ⚠️  Agent non disponible - Fallback scraping HTML...")
                    
                    # Méthode dégradée : scraping HTML (comme avant)
                    tech = scan_tech(html_content)
                    plugins_dict = scan_plugins_versions(tech["html"])
                    updates = check_updates_api(plugins_dict)
                    
                    # Détection WordPress via scraping
                    wp_detecte = (tech["wp"] == "Oui")
                    
                    tech_info = {"builder": tech["builder"], "version": tech["version"]}
                    updates_info = updates
                    
                    # Les versions systèmes ne sont pas disponibles en scraping
                    version_wp = "N/A (Scraping)"
                    version_php = "N/A (Scraping)"
                    version_mysql = "N/A (Scraping)"
                    theme_name = "N/A (Scraping)"
                    theme_version = "N/A (Scraping)"
                    
                    print(f"   ✅ [SCRAPING] IA: {diag_ia['status']} ({diag_ia['score']}%) | Builder: {tech_info['builder']} | Plugins: {len(plugins_dict)} | MAJ: {updates_info['count_updates']}")
                
                # ============================================
                # D. Préparation des données pour BDD
                # ============================================
                plugins_list = ", ".join([f"{k} ({v})" for k, v in plugins_dict.items()]) or "Aucun détecté"
                updates_list = ", ".join(updates_info.get("updates_needed", [])) or "Aucune"
                
                # E. Mise à jour BDD (avec les nouvelles colonnes)
                with engine.begin() as conn:
                    update_sql = text("""
                        UPDATE sites 
                        SET "Statut_Scan" = :statut,
                            "Methode_Scan" = :methode,
                            "Diagnostic_IA" = :ia,
                            "Score_IA" = :score,
                            "Builder_Detecte" = :build,
                            "Version_Builder" = :ver_build,
                            "Version_WP" = :ver_wp,
                            "Version_PHP" = :ver_php,
                            "Version_MySQL" = :ver_mysql,
                            "Theme_Actif" = :theme_name,
                            "Version_Theme" = :theme_ver,
                            "Plugins_Detectes" = :plugins,
                            "Mises_A_Jour" = :updates,
                            "Nb_Updates" = :nb_updates
                        WHERE "Client" = :client
                    """)
                    conn.execute(update_sql, {
                        "statut": "Scanned",
                        "methode": methode_scan,
                        "ia": diag_ia["status"],
                        "score": diag_ia["score"],
                        "build": tech_info["builder"],
                        "ver_build": tech_info["version"],
                        "ver_wp": version_wp,
                        "ver_php": version_php,
                        "ver_mysql": version_mysql,
                        "theme_name": theme_name,
                        "theme_ver": theme_version,
                        "plugins": plugins_list,
                        "updates": updates_list,
                        "nb_updates": updates_info["count_updates"],
                        "client": client
                    })
                
                # Mise à jour des statistiques
                stats["success"] += 1
                if diag_ia["status"] == "OK": stats["ia_ok"] += 1
                elif diag_ia["status"] == "ATTENTION": stats["ia_attention"] += 1
                elif diag_ia["status"] == "ALERTE": stats["ia_alerte"] += 1
                
                # WordPress détecté (via le flag wp_detecte)
                if wp_detecte:
                    stats["wp_detecte"] += 1
                
                if tech_info["builder"] != "Inconnu":
                    stats["builders"][tech_info["builder"]] = stats["builders"].get(tech_info["builder"], 0) + 1
                
                stats["plugins_total"] += len(plugins_dict)
                
                # Gestion des statistiques selon la méthode
                if methode_scan == "Agent":
                    stats["plugins_verified"] += len(plugins_dict)  # Toutes les versions sont vérifiées avec l'agent
                else:
                    stats["plugins_verified"] += updates_info.get("count_verified", 0)
                    stats["plugins_unknown"] += len(updates_info.get("unknown_version", []))
                    stats["plugins_not_found"] += len(updates_info.get("not_found", []))
                
                if len(plugins_dict) > 0: stats["sites_avec_plugins"] += 1
                if updates_info["count_updates"] > 0: 
                    stats["updates_disponibles"] += 1
                elif methode_scan == "Agent" and updates_info["count_updates"] == 0 and len(plugins_dict) > 0:
                    stats["sites_a_jour"] += 1
                elif methode_scan == "Scraping (Externe)" and updates_info.get("count_verified", 0) > 0 and updates_info["count_updates"] == 0:
                    stats["sites_a_jour"] += 1

            except Exception as e:
                error_msg = str(e).splitlines()[0]
                stats["errors"] += 1
                if "ERR_NAME_NOT_RESOLVED" in error_msg or "net::" in error_msg:
                    print(f"   ⚠️  Site inaccessible - ignoré")
                else:
                    print(f"   ❌ Échec : {error_msg}")
            
            # 🍵 PAUSE CAFÉ : On attend 2 à 4 secondes avant le prochain site
            if idx < len(sites):  # Pas de pause après le dernier
                pause = random.uniform(2, 4)
                await asyncio.sleep(pause)

        await browser.close()
        
        # 📊 RAPPORT FINAL
        print("\n" + "="*70)
        print("📊 RAPPORT D'AUDIT FINAL")
        print("="*70)
        print(f"\n🎯 Sites analysés : {stats['total']}")
        print(f"   ✅ Succès : {stats['success']}")
        print(f"   ❌ Erreurs : {stats['errors']}")
        
        print(f"\n🤖 Diagnostic IA :")
        print(f"   🟢 OK : {stats['ia_ok']} ({int(stats['ia_ok']/stats['success']*100)}%)")
        print(f"   🟡 ATTENTION : {stats['ia_attention']} ({int(stats['ia_attention']/stats['success']*100) if stats['success'] > 0 else 0}%)")
        print(f"   🔴 ALERTE : {stats['ia_alerte']} ({int(stats['ia_alerte']/stats['success']*100) if stats['success'] > 0 else 0}%)")
        
        print(f"\n🔐 Méthode de scan :")
        print(f"   🚀 Agent WordPress : {stats['methode_agent']} sites ({int(stats['methode_agent']/stats['success']*100) if stats['success'] > 0 else 0}%)")
        print(f"   🌐 Scraping HTML : {stats['methode_scraping']} sites ({int(stats['methode_scraping']/stats['success']*100) if stats['success'] > 0 else 0}%)")
        
        if stats["agent_erreurs"]:
            print(f"\n   ⚠️  Raisons d'échec de l'Agent :")
            for error, count in sorted(stats["agent_erreurs"].items(), key=lambda x: x[1], reverse=True):
                print(f"      • {error} : {count} fois")
        
        print(f"\n🌐 Technologies WordPress :")
        print(f"   WordPress détecté : {stats['wp_detecte']}/{stats['success']} sites")
        if stats["builders"]:
            print(f"\n   🎨 Builders utilisés :")
            for builder, count in sorted(stats["builders"].items(), key=lambda x: x[1], reverse=True):
                print(f"      • {builder} : {count} site(s)")
        
        print(f"\n🔌 Plugins détectés :")
        print(f"   Total de plugins : {stats['plugins_total']}")
        print(f"   Sites avec plugins : {stats['sites_avec_plugins']}/{stats['success']}")
        
        if stats['sites_avec_plugins'] > 0:
            avg_plugins = stats['plugins_total'] / stats['sites_avec_plugins']
            print(f"   Moyenne par site : {avg_plugins:.1f} plugins")
        
        print(f"\n   📊 Analyse des versions :")
        print(f"      ✅ Vérifiés (avec version) : {stats['plugins_verified']}")
        print(f"      ⚠️  Version inconnue : {stats['plugins_unknown']}")
        print(f"      ❓ Non trouvés (API) : {stats['plugins_not_found']}")
        
        print(f"\n🔄 Mises à jour :")
        print(f"   🔴 Sites nécessitant des MAJ : {stats['updates_disponibles']}")
        print(f"   🟢 Sites à jour : {stats['sites_a_jour']}")
        
        non_verifiable = stats['sites_avec_plugins'] - stats['updates_disponibles'] - stats['sites_a_jour']
        if non_verifiable > 0:
            print(f"   ⚪ Sites non vérifiables : {non_verifiable}")
        
        print("\n" + "="*70)
        print(f"✅ Audit terminé !")
        print("="*70 + "\n")

if __name__ == "__main__":
    """
    🎯 CONFIGURATION INITIALE (Première utilisation uniquement)
    
    1. Configurer les secrets dans le fichier .env :
       WEROCKET_AGENT_TOKEN=votre_token_secret
       WEROCKET_AGENT_HEADER_KEY=votre_header_secret
       
    2. Créer les colonnes dans PostgreSQL :
       python3 setup_audit_columns.py
       
    3. Installer le plugin WeRocket Agent sur les sites WordPress :
       - Télécharger werocket-agent.php
       - Installer sur WordPress (/wp-content/plugins/)
       - Configurer wp-config.php avec les mêmes secrets
       - Activer le plugin
    
    📚 MODE HYBRIDE :
    - Si l'Agent répond : Données fiables (versions exactes, statut plugins)
    - Sinon : Fallback sur scraping HTML (méthode dégradée)
    """
    asyncio.run(run_audit())
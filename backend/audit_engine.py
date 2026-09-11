import os
import sys
import asyncio
import random
import sentry_sdk
from playwright.async_api import async_playwright
from sqlalchemy import create_engine, text
import requests
import re
import hmac
import hashlib
import time
import json
from dotenv import load_dotenv

# --- INITIALISATION ---
load_dotenv()

# ... (Garde la partie vérification des clés WEROCKET_AGENT_TOKEN comme avant) ...
WEROCKET_AGENT_TOKEN = os.getenv("WEROCKET_AGENT_TOKEN")
WEROCKET_AGENT_HEADER_KEY = os.getenv("WEROCKET_AGENT_HEADER_KEY")

if not WEROCKET_AGENT_TOKEN or not WEROCKET_AGENT_HEADER_KEY:
    AGENT_AVAILABLE = False
else:
    AGENT_AVAILABLE = True

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.json")

def build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL manquant. Configure une URL Supabase avec sslmode=require.")
    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"
    if "connect_timeout=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}connect_timeout=5"
    return database_url

# Connexion BDD (Supabase)
engine = create_engine(build_database_url(), pool_pre_ping=True, pool_recycle=300)

# Dossier pour les preuves visuelles
SCREENSHOT_DIR = "screenshots_audit"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

_TF_INFER_SCRIPT = os.path.join(os.path.dirname(__file__), "tf_infer.py")
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "werocket_vision_model.h5")

def predict_visual(img_path):
    """Inférence TF dans un subprocess isolé — évite le conflit mutex avec Playwright sur macOS ARM."""
    import subprocess
    if not img_path or not os.path.exists(img_path):
        return {"status": "N/A", "score": 0}
    if not os.path.exists(_MODEL_PATH):
        print("      ⚠️ Modèle IA introuvable")
        return {"status": "ERREUR", "score": 0}
    try:
        result = subprocess.run(
            [sys.executable, _TF_INFER_SCRIPT, img_path, _MODEL_PATH],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        err = result.stderr.strip()[:120] if result.stderr else "exit code non-zéro"
        print(f"      ⚠️ tf_infer erreur: {err}")
        sentry_sdk.capture_message(f"tf_infer échoué pour {img_path}: {err}", level="warning")
        return {"status": "ERREUR", "score": 0}
    except Exception as e:
        print(f"      ⚠️ Subprocess IA échoué : {str(e)[:80]}")
        sentry_sdk.capture_exception(e)
        return {"status": "ERREUR", "score": 0}


# --- FONCTIONS TECHNIQUES ---

def connect_to_agent(url):
    """
    Connexion sécurisée à l'Agent WeRocket via API REST WordPress.
    CORRECTIF: Gère les redirections (www vs non-www) pour ne pas perdre le POST.
    """
    if not AGENT_AVAILABLE:
        return None
    
    try:
        # ⚡ ÉTAPE 0 : Résolution de la vraie URL finale (pour éviter le 301 POST->GET)
        # On fait une requête HEAD simple pour voir où le site nous emmène
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        try:
            # On teste la racine du site pour avoir la bonne version (http/https/www)
            # allow_redirects=True va suivre jusqu'à la destination finale
            pre_check = session.head(url, timeout=5, allow_redirects=True)
            final_base_url = pre_check.url.rstrip('/') # Ex: https://www.stedi.fr
        except:
            # Si le HEAD échoue, on reste sur l'URL d'origine
            final_base_url = url.rstrip('/')

        # 1. Génération du timestamp
        timestamp = int(time.time())
        
        # 2. Construction du message à signer : URL_ORIGINE|timestamp|route
        # ⚠️ IMPORTANT : On signe avec l'URL que le plugin connait (souvent celle de la DB WP)
        # Mais pour être sûr, on utilise l'URL finale trouvée
        message = f"{final_base_url}|{timestamp}|/werocket/v1/status"
        
        # 3. Signature HMAC-SHA256
        signature = hmac.new(
            WEROCKET_AGENT_TOKEN.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # 4. Données POST
        payload = {
            'token': WEROCKET_AGENT_TOKEN,
            'timestamp': timestamp,
            'signature': signature
        }
        
        # 5. Headers (Avec User-Agent "Navigateur" pour passer Wordfence)
        headers = {
            'X-WeRocket-Key': WEROCKET_AGENT_HEADER_KEY,
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        # 6. Envoi POST sur la VRAIE URL finale
        endpoint = f"{final_base_url}/wp-json/werocket/v1/status"
        
        print(f"      📡 Appel API sur : {endpoint}") # Debug visuel
        
        response = session.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=15,
            verify=True
        )
        
        # 7. Analyse
        if response.status_code == 200:
            try:
                data = response.json()
                if data.get('success'):
                    return {'success': True, 'method': 'Agent', 'data': data}
            except json.JSONDecodeError:
                return {'success': False, 'method': 'Agent', 'error': 'Réponse non-JSON'}
        
        # Gestion fine des erreurs
        elif response.status_code == 404:
            # Check si c'est du HTML (page 404 standard) ou JSON
            if "code" in response.text and "rest_no_route" in response.text:
                return {'success': False, 'method': 'Agent', 'error': '404 - Route API non déclarée (Flush Permaliens)'}
            return {'success': False, 'method': 'Agent', 'error': '404 - URL incorrecte ou Plugin inactif'}
            
        elif response.status_code == 403:
            return {'success': False, 'method': 'Agent', 'error': '403 - Accès refusé (Clé/Signature)'}
            
        return {'success': False, 'method': 'Agent', 'error': f'HTTP {response.status_code}'}
            
    except Exception as e:
        return {'success': False, 'method': 'Agent', 'error': str(e)[:50]}

def update_plugin_via_agent(url: str, plugin_slug: str) -> dict:
    """
    Déclenche la mise à jour d'un plugin via l'Agent WeRocket WordPress.
    Timeout élevé (45s) pour laisser WP télécharger et décompresser le .zip.
    """
    if not AGENT_AVAILABLE:
        return {'success': False, 'error': 'Agent non configuré (variables .env manquantes)'}

    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

        try:
            pre_check = session.head(url, timeout=5, allow_redirects=True)
            final_base_url = pre_check.url.rstrip('/')
        except Exception:
            final_base_url = url.rstrip('/')

        timestamp = int(time.time())
        message = f"{final_base_url}|{timestamp}|/werocket/v1/update-plugin"
        signature = hmac.new(
            WEROCKET_AGENT_TOKEN.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        payload = {
            'token': WEROCKET_AGENT_TOKEN,
            'timestamp': timestamp,
            'signature': signature,
            'plugin_slug': plugin_slug,
        }

        headers = {
            'X-WeRocket-Key': WEROCKET_AGENT_HEADER_KEY,
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        endpoint = f"{final_base_url}/wp-json/werocket/v1/update-plugin"
        print(f"      🔧 Mise à jour plugin '{plugin_slug}' sur : {endpoint}")

        response = session.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=45,
            verify=True
        )

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get('success'):
                    return {'success': True, 'message': data.get('message', f'{plugin_slug} mis à jour')}
                return {'success': False, 'error': data.get('message', 'Erreur inconnue depuis WordPress')}
            except json.JSONDecodeError:
                return {'success': False, 'error': 'Réponse non-JSON depuis WordPress'}

        elif response.status_code == 404:
            return {'success': False, 'error': '404 - Route update non disponible (plugin WP à mettre à jour)'}
        elif response.status_code == 403:
            return {'success': False, 'error': '403 - Accès refusé (clé invalide)'}

        return {'success': False, 'error': f'HTTP {response.status_code}'}

    except Exception as e:
        return {'success': False, 'error': str(e)[:80]}

def update_core_via_agent(url: str) -> dict:
    """
    Déclenche la mise à jour du core WordPress via l'Agent WeRocket.
    Timeout élevé (60s) pour laisser WP télécharger et installer le core.
    """
    if not AGENT_AVAILABLE:
        return {'success': False, 'error': 'Agent non configuré (variables .env manquantes)'}

    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

        try:
            pre_check = session.head(url, timeout=5, allow_redirects=True)
            final_base_url = pre_check.url.rstrip('/')
        except Exception:
            final_base_url = url.rstrip('/')

        timestamp = int(time.time())
        message = f"{final_base_url}|{timestamp}|/werocket/v1/update-core"
        signature = hmac.new(
            WEROCKET_AGENT_TOKEN.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        payload = {
            'token': WEROCKET_AGENT_TOKEN,
            'timestamp': timestamp,
            'signature': signature,
        }

        headers = {
            'X-WeRocket-Key': WEROCKET_AGENT_HEADER_KEY,
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        endpoint = f"{final_base_url}/wp-json/werocket/v1/update-core"
        print(f"      🔧 Mise à jour du core WordPress sur : {endpoint}")

        response = session.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=60,
            verify=True
        )

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get('success'):
                    return {'success': True, 'message': data.get('message', 'WordPress mis à jour'), 'version': data.get('version')}
                return {'success': False, 'error': data.get('message', 'Erreur inconnue depuis WordPress')}
            except json.JSONDecodeError:
                return {'success': False, 'error': 'Réponse non-JSON depuis WordPress'}

        elif response.status_code == 404:
            return {'success': False, 'error': "404 - Route update-core non disponible (plugin WeRocket Agent à mettre à jour)"}
        elif response.status_code == 403:
            return {'success': False, 'error': '403 - Accès refusé (clé invalide)'}

        return {'success': False, 'error': f'HTTP {response.status_code}'}

    except Exception as e:
        return {'success': False, 'error': str(e)[:80]}


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

def analyze_version_gap(current_version, latest_version):
    """
    Analyse l'écart entre deux versions selon le versioning sémantique (X.Y.Z).
    
    Retourne :
    - 'major' : Changement majeur (X) → Risque élevé, sauvegarde obligatoire
    - 'minor' : Changement mineur (Y) → Écart significatif, nouvelles fonctionnalités
    - 'patch' : Correctif (Z) → Correctifs mineurs, risque faible
    - 'unknown' : Impossible de comparer (format non standard)
    """
    try:
        # Parsing des versions (ex: "2.0.3" → [2, 0, 3])
        current_parts = [int(x) for x in current_version.split('.')]
        latest_parts = [int(x) for x in latest_version.split('.')]
        
        # Normalisation (on s'assure d'avoir au moins 3 chiffres)
        while len(current_parts) < 3:
            current_parts.append(0)
        while len(latest_parts) < 3:
            latest_parts.append(0)
        
        # Analyse de l'écart
        if current_parts[0] < latest_parts[0]:
            # Changement MAJEUR (ex: 2.x.x → 3.x.x)
            gap = latest_parts[0] - current_parts[0]
            return {
                'level': 'major',
                'emoji': '🔴',
                'risk': 'CRITIQUE',
                'message': f'Mise à jour MAJEURE (+{gap} version{"s" if gap > 1 else ""}) - Sauvegarde obligatoire',
                'priority': 3
            }
        
        elif current_parts[1] < latest_parts[1]:
            # Changement MINEUR (ex: 2.0.x → 2.1.x)
            gap = latest_parts[1] - current_parts[1]
            if gap >= 5:
                return {
                    'level': 'minor',
                    'emoji': '🟠',
                    'risk': 'ÉLEVÉ',
                    'message': f'Retard important ({gap} versions mineures) - Tester en staging',
                    'priority': 2
                }
            else:
                return {
                    'level': 'minor',
                    'emoji': '🟡',
                    'risk': 'MOYEN',
                    'message': f'Nouvelles fonctionnalités disponibles (+{gap} version{"s" if gap > 1 else ""})',
                    'priority': 2
                }
        
        elif current_parts[2] < latest_parts[2]:
            # Changement PATCH (ex: 2.0.3 → 2.0.5)
            gap = latest_parts[2] - current_parts[2]
            return {
                'level': 'patch',
                'emoji': '🟢',
                'risk': 'FAIBLE',
                'message': f'Correctifs de sécurité/bugs ({gap} patch{"s" if gap > 1 else ""})',
                'priority': 1
            }
        
        else:
            # Version à jour ou supérieure
            return {
                'level': 'none',
                'emoji': '✅',
                'risk': 'AUCUN',
                'message': 'À jour',
                'priority': 0
            }
    
    except (ValueError, IndexError):
        # Format de version non standard (ex: "1.0-beta")
        return {
            'level': 'unknown',
            'emoji': '⚪',
            'risk': 'INCONNU',
            'message': 'Format de version non standard',
            'priority': 0
        }

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
    current_time = time.time()
    
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
            
        # 5. Comparaison avec ANALYSE DE SÉVÉRITÉ (nouveau)
        try:
            gap_analysis = analyze_version_gap(version, latest)
            
            if gap_analysis['level'] == 'none':
                # Version à jour
                up_to_date.append(f"{plugin} (v{version} ✅)")
            elif gap_analysis['level'] == 'unknown':
                # Format non standard
                unknown_version.append(f"{plugin} ({version})")
            else:
                # Mise à jour disponible avec indicateur de risque
                update_msg = f"{gap_analysis['emoji']} {plugin} ({version} → {latest}) - {gap_analysis['risk']}: {gap_analysis['message']}"
                # On stocke aussi la priorité pour trier plus tard
                updates_needed.append({
                    'message': update_msg,
                    'priority': gap_analysis['priority'],
                    'plugin': plugin,
                    'current': version,
                    'latest': latest,
                    'risk': gap_analysis['risk']
                })
        except:
            # Version non parsable (ex: "1.0-beta")
            unknown_version.append(f"{plugin} ({version})")
    
    # 🔥 TRI PAR PRIORITÉ : Les mises à jour critiques en premier
    updates_needed.sort(key=lambda x: x['priority'], reverse=True)
    
    # Conversion en liste de strings pour compatibilité
    updates_needed_formatted = [u['message'] for u in updates_needed]
    
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
        "updates_needed": updates_needed_formatted,  # Liste formatée pour affichage
        "updates_needed_raw": updates_needed,  # Données complètes avec priorités
        "up_to_date": up_to_date,
        "unknown_version": unknown_version,
        "not_found": not_found,
        "count_updates": len(updates_needed),
        "count_verified": verified_plugins,
        "count_total": total_plugins
    }

# --- COEUR DU SCANNER ---

async def run_audit(sites_list=None, progress_callback=None):
    def _progress(percent: int, label: str, status: str = "running"):
        if progress_callback:
            progress_callback({"status": status, "percent": percent, "label": label})
    """
    Lance un audit sur une liste de sites.
    
    Args:
        sites_list: Liste de dictionnaires avec les clés 'Client' et 'URL'
                   Ex: [{'Client': 'Mon Client', 'URL': 'https://example.com'}]
                   Si None, récupère les sites de test depuis la BDD
    
    Returns:
        dict: Statistiques de l'audit avec détails par site
    """
    # 1. Récupération des sites à auditer
    if sites_list is None:
        # Mode par défaut : liste statique des sites clients
        sites = [
            {"Client": "laminaudiere", "URL": "https://www.institutlaminaudiere.fr/"},
            {"Client": "orchestreufo", "URL": "https://www.ufo-orchestre.com"},
            {"Client": "provenceassurancecourt", "URL": "https://www.provence-assurance.fr"},
            {"Client": "jolimome", "URL": "https://www.joli-mome.fr"},
            {"Client": "orijinbtp", "URL": "https://www.orijinbtp.fr/"},
            {"Client": "isoreve", "URL": "https://www.isoreve.com/"},
            {"Client": "medsenger", "URL": "https://www.medsenger.fr"},
            {"Client": "ancienafcom", "URL": "https://www.afcommunication.com/"},
            {"Client": "ancienaft", "URL": "https://www.aft-pompiers.fr/"},
            {"Client": "lbphotographies", "URL": "https://www.lbphotographies.fr"},
            {"Client": "maitredoeuvre", "URL": "https://lemaitredoeuvre.fr/"},
            {"Client": "peugeotlaffitte", "URL": "https://www.peugeotlaffitte.fr/"},
            {"Client": "corindustries", "URL": "https://www.cor-industries.com/"},
        ]
    else:
        # Mode API : utilise la liste fournie en paramètre
        sites = sites_list

    if not sites:
        print("⚠️  Aucun projet avec url_maquette. Audit annulé.")
        return stats

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
        "agent_erreurs": {},
        "site_details": []  # 📋 Nouveau : stockage détaillé par site
    }

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1 : Playwright — screenshots + scraping + appels agent
    # Aucun appel TF ici : TF et Playwright partagent les GCD queues macOS,
    # ce qui provoque un deadlock mutex si les deux tournent simultanément.
    # ─────────────────────────────────────────────────────────────────────────
    collected_sites = []  # données brutes collectées pendant la phase Playwright

    total_sites = len(sites)
    _progress(5, f"Lancement — {total_sites} site(s) à auditer")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for idx, site in enumerate(sites, 1):
            project_name = site.get('project_name') or site.get('Client') or 'Projet sans nom'
            # Phase Playwright : 5% → 65%
            pct = 5 + int((idx - 1) / total_sites * 60)
            _progress(pct, f"📸 Capture {idx}/{total_sites} — {project_name}")
            project_id = site.get('project_id')
            raw_url = site.get('site_url') or site.get('URL') or ''
            url = raw_url.strip().split(" ")[0]
            if not url.startswith("http"): url = "https://" + url

            clean_name = project_name.replace(" ", "_").replace("/", "-")
            img_path = f"{SCREENSHOT_DIR}/{clean_name}.png"

            print(f"🔎 [{idx}/{len(sites)}] Audit : {project_name}...")

            # A. Capture d'écran + scraping + agent
            try:
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

                # Contact page capture — on stocke le chemin, TF sera appelé plus tard
                from urllib.parse import urljoin
                contact_img_path = None
                for slug in ["/contact", "/contactez-nous"]:
                    try:
                        contact_url = urljoin(url, slug)
                        response = await page.goto(contact_url, wait_until="load", timeout=15000)
                        if response and response.status == 200:
                            await page.wait_for_timeout(2000)
                            _contact_path = f"{SCREENSHOT_DIR}/{clean_name}_contact.png"
                            await page.screenshot(path=_contact_path)
                            contact_img_path = _contact_path
                            break
                    except Exception:
                        continue

                await page.close()
                await context.close()

                # ============================================
                # B. MODE HYBRIDE : Agent WordPress en priorité
                # ============================================
                agent_result = await asyncio.to_thread(connect_to_agent, url)

                methode_scan = "Inconnu"
                tech_info = {"builder": "Inconnu", "version": "N/A"}
                plugins_dict = {}
                updates_info = {"count_updates": 0, "updates_needed": []}
                version_wp = "N/A"
                version_php = "N/A"
                version_mysql = "N/A"
                theme_name = "N/A"
                theme_version = "N/A"
                wp_detecte = False

                if agent_result and agent_result.get('success'):
                    methode_scan = "Agent"
                    wp_detecte = True

                    data = agent_result['data']

                    versions = data.get('versions', {})
                    version_wp = versions.get('wordpress', 'N/A')
                    version_php = versions.get('php', 'N/A')
                    version_mysql = versions.get('mysql', 'N/A')

                    theme = data.get('theme', {})
                    theme_name = theme.get('name', 'N/A')
                    theme_version = theme.get('version', 'N/A')

                    plugins_data = data.get('plugins', [])
                    plugins_dict = {}
                    updates_needed = []
                    updates_needed_raw = []

                    for plugin in plugins_data:
                        plugin_name = plugin.get('name', 'Inconnu')
                        plugin_version = plugin.get('version', 'N/A')
                        is_active = plugin.get('is_active', False)
                        update_available = plugin.get('update_available', {})

                        status = "Actif" if is_active else "Inactif"
                        plugins_dict[plugin_name] = f"{plugin_version} ({status})"

                        if update_available.get('available'):
                            new_version = update_available.get('new_version')
                            gap_analysis = analyze_version_gap(plugin_version, new_version)
                            update_msg = f"{gap_analysis['emoji']} {plugin_name} ({plugin_version} → {new_version}) - {gap_analysis['risk']}: {gap_analysis['message']}"
                            plugin_slug_real = plugin.get('slug', '')
                            stored_msg = f"{plugin_slug_real}||{update_msg}" if plugin_slug_real else update_msg
                            updates_needed.append(stored_msg)
                            updates_needed_raw.append({
                                'message': update_msg,
                                'priority': gap_analysis['priority'],
                                'plugin': plugin_name,
                                'current': plugin_version,
                                'latest': new_version,
                                'risk': gap_analysis['risk']
                            })

                    updates_needed_raw.sort(key=lambda x: x['priority'], reverse=True)

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
                        "updates_needed": updates_needed,
                        "updates_needed_raw": updates_needed_raw
                    }

                    print(f"   ✅ [AGENT] WP: {version_wp} | PHP: {version_php} | Builder: {builder} | Plugins: {len(plugins_dict)} | MAJ: {len(updates_needed)}")

                else:
                    methode_scan = "Scraping (Externe)"

                    if agent_result:
                        error_type = agent_result.get('error', 'Inconnu')
                        stats["agent_erreurs"][error_type] = stats["agent_erreurs"].get(error_type, 0) + 1
                        print(f"   ⚠️  Agent KO ({error_type}) - Fallback scraping HTML...")
                    else:
                        print(f"   ⚠️  Agent non disponible - Fallback scraping HTML...")

                    tech = scan_tech(html_content)
                    plugins_dict = scan_plugins_versions(tech["html"])
                    updates = await asyncio.to_thread(check_updates_api, plugins_dict)

                    wp_detecte = (tech["wp"] == "Oui")
                    tech_info = {"builder": tech["builder"], "version": tech["version"]}
                    updates_info = updates
                    version_wp = "N/A (Scraping)"
                    version_php = "N/A (Scraping)"
                    version_mysql = "N/A (Scraping)"
                    theme_name = "N/A (Scraping)"
                    theme_version = "N/A (Scraping)"

                    print(f"   ✅ [SCRAPING] Builder: {tech_info['builder']} | Plugins: {len(plugins_dict)} | MAJ: {updates_info['count_updates']}")

                # Stocker toutes les données pour la phase TF (après fermeture Playwright)
                collected_sites.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "url": url,
                    "img_path": img_path,
                    "contact_img_path": contact_img_path,
                    "methode_scan": methode_scan,
                    "tech_info": tech_info,
                    "plugins_dict": plugins_dict,
                    "updates_info": updates_info,
                    "version_wp": version_wp,
                    "version_php": version_php,
                    "version_mysql": version_mysql,
                    "theme_name": theme_name,
                    "theme_version": theme_version,
                    "wp_detecte": wp_detecte,
                    "agent_error": agent_result.get('error') if agent_result and not agent_result.get('success') else None,
                    "success": True,
                })

            except Exception as e:
                error_msg = str(e).splitlines()[0]
                if "ERR_NAME_NOT_RESOLVED" in error_msg or "net::" in error_msg:
                    print(f"   ⚠️  Site inaccessible - ignoré")
                else:
                    print(f"   ❌ Échec : {error_msg}")
                    sentry_sdk.capture_exception(e)
                collected_sites.append({
                    "project_name": project_name,
                    "url": url if url else raw_url,
                    "success": False,
                    "error": error_msg,
                })

            # 🍵 PAUSE CAFÉ : On attend 2 à 4 secondes avant le prochain site
            if idx < len(sites):
                pause = random.uniform(2, 4)
                await asyncio.sleep(pause)

        await browser.close()

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2 : TF — analyse visuelle (Playwright est complètement fermé ici)
    # ─────────────────────────────────────────────────────────────────────────
    _progress(65, "🧠 Analyse IA des screenshots...")
    print("\n🧠 Analyse IA des screenshots...")
    successful_entries = [e for e in collected_sites if e.get("success")]
    for ia_idx, entry in enumerate(collected_sites):
        if not entry.get("success"):
            stats["errors"] += 1
            continue

        # Phase TF : 65% → 90%
        pct = 65 + int(ia_idx / max(len(successful_entries), 1) * 25)
        _progress(pct, f"🧠 Analyse IA {ia_idx + 1}/{len(successful_entries)} — {entry['project_name']}")

        diag_ia = predict_visual(entry["img_path"])
        diag_ia_contact = predict_visual(entry["contact_img_path"]) if entry.get("contact_img_path") else {"status": "N/A", "score": 0}
        entry["diag_ia"] = diag_ia
        entry["diag_ia_contact"] = diag_ia_contact

        methode_scan = entry["methode_scan"]
        if methode_scan == "Agent":
            stats["methode_agent"] += 1
        else:
            stats["methode_scraping"] += 1

        print(f"   🧠 {entry['project_name']} → IA: {diag_ia['status']} ({diag_ia['score']}%)")

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3 : DB inserts + stats + rapport (aucun I/O bloquant ici)
    # ─────────────────────────────────────────────────────────────────────────
    _progress(90, "💾 Enregistrement des résultats...")
    json_results = []
    for entry in collected_sites:
        if not entry.get("success"):
            continue

        diag_ia = entry["diag_ia"]
        diag_ia_contact = entry["diag_ia_contact"]
        project_id = entry["project_id"]
        project_name = entry["project_name"]
        url = entry["url"]
        methode_scan = entry["methode_scan"]
        tech_info = entry["tech_info"]
        plugins_dict = entry["plugins_dict"]
        updates_info = entry["updates_info"]
        version_wp = entry["version_wp"]
        version_php = entry["version_php"]
        version_mysql = entry["version_mysql"]
        theme_name = entry["theme_name"]
        theme_version = entry["theme_version"]
        wp_detecte = entry["wp_detecte"]

        plugins_list = ", ".join([f"{k} ({v})" for k, v in plugins_dict.items()]) or "Aucun détecté"
        updates_list = ", ".join(updates_info.get("updates_needed", [])) or "Aucune"

        # Accumulation résultats JSON (indépendant de la DB)
        json_results.append({
            "client": project_name,
            "url": url,
            "ia_status": diag_ia["status"],
            "ia_score": diag_ia["score"],
            "ia_status_contact": diag_ia_contact["status"],
            "ia_score_contact": diag_ia_contact["score"],
            "wp_version": version_wp,
            "php_version": version_php,
            "theme": theme_name,
            "builder": tech_info["builder"],
            "updates_count": updates_info["count_updates"],
            "mises_a_jour": updates_list,
            "methode": methode_scan,
        })

        try:
            with engine.begin() as conn:
                insert_sql = text("""
                    INSERT INTO site_audits (
                        project_id, project_name, site_url, statut_scan, methode_scan,
                        diagnostic_ia, score_ia, builder_detecte, version_builder,
                        version_wp, version_php, version_mysql, theme_actif, version_theme,
                        plugins_detectes, mises_a_jour, nb_updates,
                        diagnostic_ia_contact, score_ia_contact
                    ) VALUES (
                        :project_id, :project_name, :site_url, :statut, :methode,
                        :ia, :score, :build, :ver_build,
                        :ver_wp, :ver_php, :ver_mysql, :theme_name, :theme_ver,
                        :plugins, :updates, :nb_updates,
                        :ia_contact, :score_contact
                    )
                """)
                conn.execute(insert_sql, {
                    "project_id": project_id,
                    "project_name": project_name,
                    "site_url": url,
                    "statut": "Scanned",
                    "methode": methode_scan,
                    "ia": diag_ia["status"],
                    "score": diag_ia["score"],
                    "ia_contact": diag_ia_contact["status"],
                    "score_contact": diag_ia_contact["score"],
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
                })
        except Exception as db_err:
            print(f"   ⚠️  DB insert ignoré ({project_name}): {db_err}")
            sentry_sdk.capture_exception(db_err)

        # Mise à jour des statistiques
        stats["success"] += 1
        if diag_ia["status"] == "OK": stats["ia_ok"] += 1
        elif diag_ia["status"] == "ATTENTION": stats["ia_attention"] += 1
        elif diag_ia["status"] == "ALERTE": stats["ia_alerte"] += 1

        if wp_detecte:
            stats["wp_detecte"] += 1

        if tech_info["builder"] != "Inconnu":
            stats["builders"][tech_info["builder"]] = stats["builders"].get(tech_info["builder"], 0) + 1

        stats["plugins_total"] += len(plugins_dict)

        if methode_scan == "Agent":
            stats["plugins_verified"] += len(plugins_dict)
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

        stats["site_details"].append({
            "client": project_name,
            "url": url,
            "methode": methode_scan,
            "ia_status": diag_ia["status"],
            "ia_score": diag_ia["score"],
            "wp_version": version_wp,
            "php_version": version_php,
            "mysql_version": version_mysql,
            "builder": tech_info["builder"],
            "builder_version": tech_info["version"],
            "theme": theme_name,
            "theme_version": theme_version,
            "plugins_count": len(plugins_dict),
            "plugins_list": plugins_dict,
            "updates_count": updates_info["count_updates"],
            "updates_list": updates_info.get("updates_needed", []),
            "updates_raw": updates_info.get("updates_needed_raw", [])
        })

    # Sauvegarde résultats JSON local
    if json_results:
        try:
            with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                json.dump(json_results, f, ensure_ascii=False, indent=2)
            print(f"✅ Résultats sauvegardés dans {RESULTS_FILE} ({len(json_results)} sites)")
        except Exception as e:
            print(f"⚠️  Erreur écriture results.json: {e}")

    # ─── Rapport final ────────────────────────────────────────────────────────
    print("\n" + "="*80)
    print("📊 RAPPORT D'AUDIT DÉTAILLÉ")
    print("="*80)

    print("\n" + "="*80)
    print("📋 ANALYSE DÉTAILLÉE PAR SITE")
    print("="*80)

    for site_detail in stats["site_details"]:
        print(f"\n🌐 {site_detail['client']}")
        print(f"   URL: {site_detail['url']}")
        print(f"   Méthode: {site_detail['methode']}")

        ia_emoji = {"OK": "🟢", "ATTENTION": "🟡", "ALERTE": "🔴", "ERREUR": "⚫"}
        print(f"   IA: {ia_emoji.get(site_detail['ia_status'], '⚪')} {site_detail['ia_status']} ({site_detail['ia_score']}%)")

        if site_detail['methode'] == 'Agent':
            print(f"   💻 Versions: WP {site_detail['wp_version']} | PHP {site_detail['php_version']} | MySQL {site_detail['mysql_version']}")
            print(f"   🎨 Thème: {site_detail['theme']} (v{site_detail['theme_version']})")

        if site_detail['builder'] != "Inconnu":
            print(f"   🎨 Builder: {site_detail['builder']} (v{site_detail['builder_version']})")

        print(f"   🔌 Plugins: {site_detail['plugins_count']} détecté(s)")

        if site_detail['updates_count'] > 0:
            print(f"   🔄 Mises à jour: {site_detail['updates_count']} disponible(s)")
            if site_detail.get('updates_raw'):
                critical = [u for u in site_detail['updates_raw'] if u.get('priority', 0) == 3]
                high = [u for u in site_detail['updates_raw'] if u.get('priority', 0) == 2]
                low = [u for u in site_detail['updates_raw'] if u.get('priority', 0) == 1]
                if critical:
                    print(f"      🔴 CRITIQUES ({len(critical)}):")
                    for u in critical: print(f"         • {u['message']}")
                if high:
                    print(f"      🟡 MOYENNES ({len(high)}):")
                    for u in high: print(f"         • {u['message']}")
                if low:
                    print(f"      🟢 MINEURES ({len(low)}):")
                    for u in low: print(f"         • {u['message']}")
            else:
                for update in site_detail['updates_list'][:5]:
                    print(f"      • {update}")
                if len(site_detail['updates_list']) > 5:
                    print(f"      ... et {len(site_detail['updates_list']) - 5} autre(s)")
        else:
            print(f"   ✅ Aucune mise à jour nécessaire")

    print("\n" + "="*80)
    print("📈 STATISTIQUES GLOBALES")
    print("="*80)
    print(f"\n🎯 Sites analysés : {stats['total']}")
    print(f"   ✅ Succès : {stats['success']}")
    print(f"   ❌ Erreurs : {stats['errors']}")

    success_count = stats['success']
    ok_pct = int(stats['ia_ok'] / success_count * 100) if success_count > 0 else 0
    attention_pct = int(stats['ia_attention'] / success_count * 100) if success_count > 0 else 0
    alerte_pct = int(stats['ia_alerte'] / success_count * 100) if success_count > 0 else 0
    print(f"\n🤖 Diagnostic IA :")
    print(f"   🟢 OK : {stats['ia_ok']} ({ok_pct}%)")
    print(f"   🟡 ATTENTION : {stats['ia_attention']} ({attention_pct}%)")
    print(f"   🔴 ALERTE : {stats['ia_alerte']} ({alerte_pct}%)")

    print(f"\n🔐 Méthode de scan :")
    print(f"   🚀 Agent WordPress : {stats['methode_agent']} sites ({int(stats['methode_agent']/success_count*100) if success_count > 0 else 0}%)")
    print(f"   🌐 Scraping HTML : {stats['methode_scraping']} sites ({int(stats['methode_scraping']/success_count*100) if success_count > 0 else 0}%)")

    if stats["agent_erreurs"]:
        print(f"\n   ⚠️  Raisons d'échec de l'Agent :")
        for error, count in sorted(stats["agent_erreurs"].items(), key=lambda x: x[1], reverse=True):
            print(f"      • {error} : {count} fois")

    print(f"\n🌐 Technologies WordPress :")
    print(f"   WordPress détecté : {stats['wp_detecte']}/{success_count} sites")
    if stats["builders"]:
        print(f"\n   🎨 Builders utilisés :")
        for builder, count in sorted(stats["builders"].items(), key=lambda x: x[1], reverse=True):
            print(f"      • {builder} : {count} site(s)")

    print(f"\n🔌 Plugins détectés :")
    print(f"   Total de plugins : {stats['plugins_total']}")
    print(f"   Sites avec plugins : {stats['sites_avec_plugins']}/{success_count}")
    if stats['sites_avec_plugins'] > 0:
        print(f"   Moyenne par site : {stats['plugins_total'] / stats['sites_avec_plugins']:.1f} plugins")

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

    critical_count = high_count = low_count = 0
    for site in stats["site_details"]:
        for update in site.get('updates_raw', []):
            p = update.get('priority', 0)
            if p == 3: critical_count += 1
            elif p == 2: high_count += 1
            elif p == 1: low_count += 1

    if critical_count + high_count + low_count > 0:
        print(f"\n   📊 Répartition par sévérité :")
        if critical_count > 0: print(f"      🔴 CRITIQUES : {critical_count} (Sauvegarde obligatoire)")
        if high_count > 0: print(f"      🟡 MOYENNES : {high_count} (Nouvelles fonctionnalités)")
        if low_count > 0: print(f"      🟢 MINEURES : {low_count} (Correctifs)")

    _progress(100, "✅ Audit terminé !", status="done")
    print("\n" + "="*80)
    print(f"✅ Audit terminé !")
    print("="*80 + "\n")

    return stats

# ============================================================================
# SECTION : Exécution en mode script (optionnel, pour tests manuels)
# ============================================================================
# Pour exécuter ce fichier directement avec : python audit_engine.py
# Décommentez les lignes ci-dessous :
#
# if __name__ == "__main__":
#     """
#     🎯 CONFIGURATION INITIALE (Première utilisation uniquement)
#     
#     1. Configurer les secrets dans le fichier .env :
#        WEROCKET_AGENT_TOKEN=votre_token_secret
#        WEROCKET_AGENT_HEADER_KEY=votre_header_secret
#        
#     2. Créer les colonnes dans PostgreSQL :
#        python3 setup_audit_columns.py
#        
#     3. Installer le plugin WeRocket Agent sur les sites WordPress :
#        - Télécharger werocket-agent.php
#        - Installer sur WordPress (/wp-content/plugins/)
#        - Configurer wp-config.php avec les mêmes secrets
#        - Activer le plugin
#     
#     📚 MODE HYBRIDE :
#     - Si l'Agent répond : Données fiables (versions exactes, statut plugins)
#     - Sinon : Fallback sur scraping HTML (méthode dégradée)
#     """
#     asyncio.run(run_audit())
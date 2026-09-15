import os
import time
import json
import requests
from nacl.signing import SigningKey
from dotenv import load_dotenv

from config import RESULTS_FILE
from plugin_updates import analyze_version_gap

# --- INITIALISATION ---
load_dotenv()

WEROCKET_PRIVATE_KEY_HEX = os.getenv("WEROCKET_PRIVATE_KEY_HEX")
WEROCKET_AGENT_HEADER_KEY = os.getenv("WEROCKET_AGENT_HEADER_KEY")  # inchangé, indépendant

if not WEROCKET_PRIVATE_KEY_HEX:
    AGENT_AVAILABLE = False
    _signing_key = None
else:
    AGENT_AVAILABLE = True
    # PyNaCl attend le "seed" 32 octets (64 caractères hex) — pas le format
    # libsodium 64 octets (128 hex) qui concatène seed+clé publique.
    _signing_key = SigningKey(bytes.fromhex(WEROCKET_PRIVATE_KEY_HEX[:64]))


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
        # ⚠️ Depuis la version 2.6.3 du plugin, la route est incluse dans le message signé
        # pour empêcher le replay d'une signature capturée sur une route vers une autre.
        route = "/werocket/v1/status"
        message = f"{final_base_url}|{timestamp}|{route}"

        # 3. Signature Ed25519 (clé privée jamais transmise, contrairement au HMAC partagé)
        signature = _signing_key.sign(message.encode('utf-8')).signature.hex()

        # 4. Données POST
        payload = {
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
        route = "/werocket/v1/update-plugin"
        message = f"{final_base_url}|{timestamp}|{route}"
        signature = _signing_key.sign(message.encode('utf-8')).signature.hex()

        payload = {
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
        route = "/werocket/v1/update-core"
        message = f"{final_base_url}|{timestamp}|{route}"
        signature = _signing_key.sign(message.encode('utf-8')).signature.hex()

        payload = {
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


def parse_agent_data(data):
    """
    Transforme la réponse brute de l'Agent WordPress (/status) en champs exploitables
    par le reste du pipeline (run_audit et le rafraîchissement post-update).
    """
    versions = data.get('versions', {})
    version_wp = versions.get('wordpress', 'N/A')
    version_php = versions.get('php', 'N/A')
    version_mysql = versions.get('mysql', 'N/A')

    theme = data.get('theme', {})
    theme_name = theme.get('name', 'N/A')
    theme_version = theme.get('version', 'N/A')

    licenses_info = data.get('licenses', {})

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

    return {
        "version_wp": version_wp,
        "version_php": version_php,
        "version_mysql": version_mysql,
        "theme_name": theme_name,
        "theme_version": theme_version,
        "licenses": licenses_info,
        "plugins_dict": plugins_dict,
        "tech_info": {"builder": builder, "version": builder_version},
        "updates_info": {
            "count_updates": len(updates_needed),
            "updates_needed": updates_needed,
            "updates_needed_raw": updates_needed_raw
        },
    }


def refresh_site_in_results(url: str) -> dict:
    """
    Après une mise à jour de plugin/core réussie, re-interroge l'Agent WordPress sur
    CE site pour rafraîchir son entrée dans results.json (versions, plugins, MAJ
    restantes) — sans relancer un audit complet (pas de nouveau screenshot/IA).
    """
    agent_result = connect_to_agent(url)
    if not agent_result or not agent_result.get('success'):
        return {'success': False, 'error': agent_result.get('error') if agent_result else 'Agent injoignable'}

    parsed = parse_agent_data(agent_result['data'])
    plugins_list = ", ".join([f"{k} ({v})" for k, v in parsed["plugins_dict"].items()]) or "Aucun détecté"
    updates_list = ", ".join(parsed["updates_info"]["updates_needed"]) or "Aucune"

    if not os.path.exists(RESULTS_FILE):
        return {'success': False, 'error': 'results.json introuvable'}

    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            results = json.load(f)
    except Exception as e:
        return {'success': False, 'error': f'Lecture results.json échouée: {e}'}

    updated = False
    for entry in results:
        if entry.get("url") == url:
            entry["wp_version"] = parsed["version_wp"]
            entry["php_version"] = parsed["version_php"]
            entry["builder"] = parsed["tech_info"]["builder"]
            entry["updates_count"] = parsed["updates_info"]["count_updates"]
            entry["mises_a_jour"] = updates_list
            entry["methode"] = "Agent"
            updated = True
            break

    if not updated:
        return {'success': False, 'error': "Site absent de results.json (pas encore audité)"}

    try:
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {'success': False, 'error': f'Écriture results.json échouée: {e}'}

    return {'success': True, 'updates_count': parsed["updates_info"]["count_updates"]}

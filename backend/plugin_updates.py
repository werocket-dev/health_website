import time
import requests

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

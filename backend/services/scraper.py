import re


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

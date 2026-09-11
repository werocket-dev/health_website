import os
import random
import asyncio
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
OUT_DIR = "dataset/broken"
os.makedirs(OUT_DIR, exist_ok=True)

DEVICES = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 390, "height": 844},
}

FONT_STACKS = [
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen-Sans,Ubuntu,Cantarell,'Helvetica Neue',sans-serif",
    "Arial, Helvetica, sans-serif",
    "Georgia, 'Times New Roman', serif",
    "Verdana, Geneva, sans-serif",
]

rng = random.Random(42)


def _wp_error_html(message, link_text=None, link_href="https://wordpress.org/documentation/article/faq-troubleshooting/"):
    """Reproduit la page d'erreur par défaut de WordPress (wp_die), avec variations
    réalistes de mise en page pour éviter que le modèle mémorise une image unique."""
    font = rng.choice(FONT_STACKS)
    max_width = rng.randint(600, 760)
    margin_top = rng.randint(20, 140)
    font_size = rng.randint(13, 16)
    text_color = rng.choice(["#444", "#333", "#555", "#23282d"])

    link_html = f'<p><a href="{link_href}">{link_text}</a></p>' if link_text else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width">
<title>WordPress &rsaquo; Erreur</title>
<style>
html {{ background-color: #f1f1f1; }}
body {{
    background: #fff;
    border: 1px solid #ccd0d4;
    color: {text_color};
    font-family: {font};
    margin: {margin_top}px auto;
    padding: 1em 2em;
    max-width: {max_width}px;
}}
h1 {{
    border-bottom: 1px solid #dadada;
    clear: both;
    color: #666;
    font-size: 24px;
    margin: 30px 0 0 0;
    padding: 0;
    padding-bottom: 7px;
}}
#error-page p {{ font-size: {font_size}px; line-height: 1.5; margin: 25px 0 20px; }}
</style>
</head>
<body id="error-page">
<p>{message}</p>
{link_html}
</body>
</html>"""


def _server_error_html(code, status_text, server_label):
    """Reproduit les pages d'erreur par défaut Nginx/Apache (500/502/503)."""
    font = rng.choice(FONT_STACKS)
    return f"""<!DOCTYPE html>
<html>
<head><title>{code} {status_text}</title></head>
<body style="width: 35em; margin: 0 auto; font-family: {font};">
<center><h1>{code} {status_text}</h1></center>
<hr><center>{server_label}</center>
</body>
</html>"""


def _blank_html():
    """Page blanche pure : crash JS avant tout rendu, ou proxy qui coupe la connexion."""
    bg = rng.choice(["#ffffff", "#fefefe", "#fdfdfd"])
    return f"<!DOCTYPE html><html><head></head><body style=\"background:{bg};margin:0;\"></body></html>"


def build_variants():
    """Retourne une liste de (name_prefix, html) pour chaque variante à capturer."""
    variants = []

    for i in range(30):
        html = _wp_error_html(
            "Il y a eu une erreur critique sur ce site.",
            "En apprendre plus sur le débogage de WordPress."
        )
        variants.append((f"synthetic_wpcritfr_{i:02d}", html))

    for i in range(20):
        html = _wp_error_html(
            "There has been a critical error on this website.",
            "Learn more about troubleshooting WordPress."
        )
        variants.append((f"synthetic_wpcriten_{i:02d}", html))

    for i in range(20):
        html = _wp_error_html("Erreur d'établissement d'une connexion à la base de données")
        variants.append((f"synthetic_wpdbfr_{i:02d}", html))

    for i in range(15):
        html = _wp_error_html("Error establishing a database connection")
        variants.append((f"synthetic_wpdben_{i:02d}", html))

    servers = ["nginx", "nginx/1.18.0 (Ubuntu)", "nginx/1.24.0", "Apache/2.4.41 (Ubuntu) Server"]
    for i in range(20):
        html = _server_error_html(500, "Internal Server Error", rng.choice(servers))
        variants.append((f"synthetic_err500_{i:02d}", html))

    for i in range(20):
        html = _server_error_html(502, "Bad Gateway", rng.choice(servers))
        variants.append((f"synthetic_err502_{i:02d}", html))

    for i in range(25):
        html = _server_error_html(503, "Service Temporarily Unavailable", rng.choice(servers))
        variants.append((f"synthetic_err503_{i:02d}", html))

    for i in range(15):
        variants.append((f"synthetic_blank_{i:02d}", _blank_html()))

    return variants


async def generate():
    variants = build_variants()
    print(f"🧪 Génération de {len(variants)} variantes d'erreurs x {len(DEVICES)} viewports = {len(variants) * len(DEVICES)} images...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for name_prefix, html in variants:
            for device_name, viewport in DEVICES.items():
                context = await browser.new_context(viewport=viewport)
                page = await context.new_page()
                await page.set_content(html, wait_until="load")
                await page.screenshot(path=f"{OUT_DIR}/{name_prefix}_{device_name}.png")
                await page.close()
                await context.close()
            print(f"   ✅ {name_prefix}")

        await browser.close()

    print(f"🎉 Terminé. Images ajoutées dans {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(generate())

import os
import asyncio
from playwright.async_api import async_playwright
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from urllib.parse import urlparse, urljoin

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
load_dotenv(ENV_PATH)
engine = create_engine(os.getenv('DATABASE_URL'))

DIRS = {"healthy": "dataset/healthy", "broken": "dataset/broken"}
for d in DIRS.values(): os.makedirs(d, exist_ok=True)

CONTACT_SLUGS = ["/contact", "/contactez-nous"]

# Deux viewports fixes, chacun hors de la zone grise des breakpoints responsive
# (800px pouvait déclencher tantôt un rendu desktop, tantôt mobile selon le site)
DEVICES = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 390, "height": 844},
}

def clean_url(raw_url):
    if not raw_url: return None
    url = raw_url.strip().split(" ")[0]
    return url if url.startswith("http") else "https://" + url

async def find_contact_url(page, base_url):
    """Tente /contact puis /contactez-nous. Retourne l'URL si trouvée, None sinon."""
    for slug in CONTACT_SLUGS:
        contact_url = urljoin(base_url, slug)
        try:
            response = await page.goto(contact_url, wait_until="load", timeout=15000)
            if response and response.status == 200:
                return contact_url
        except Exception:
            continue
    return None

async def capture_page(page, url, name_prefix):
    """Capture healthy + broken pour une URL donnée."""
    await page.goto(url, wait_until="load", timeout=30000)
    await asyncio.sleep(2)

    await page.screenshot(path=f"{DIRS['healthy']}/{name_prefix}.png")

    await page.evaluate("document.querySelectorAll('style, link[rel=\"stylesheet\"]').forEach(el => el.remove());")
    await asyncio.sleep(1)
    await page.screenshot(path=f"{DIRS['broken']}/{name_prefix}.png")

async def sabotaging_bot():
    with engine.connect() as conn:
        query = text("SELECT * FROM projects WHERE is_active = true ORDER BY RANDOM() LIMIT 350")
        sites = conn.execute(query).mappings().all()

    print(f"🧪 Génération du Dataset sur {len(sites)} sites...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for site in sites:
            url = clean_url(site['url'])
            name = site['client_name'].replace(" ", "_").replace("/", "-")
            print(f"👉 {site['client_name']}...")

            try:
                result = urlparse(url)
                if not result.scheme or not result.netloc:
                    print(f"   ❌ URL invalide : {url}")
                    continue
            except Exception as e:
                print(f"   ❌ Erreur de parsing : {str(e)}")
                continue

            context = await browser.new_context(viewport=DEVICES["desktop"])
            page = await context.new_page()

            try:
                # Détection de la page contact une seule fois (indépendante du viewport)
                contact_url = await find_contact_url(page, url)
                if contact_url:
                    print(f"   ℹ️  Page contact trouvée ({contact_url})")
                else:
                    print(f"   ⚠️  Pas de page contact trouvée")

                for device_name, viewport in DEVICES.items():
                    await page.set_viewport_size(viewport)

                    # 1. Page d'accueil
                    await capture_page(page, url, f"{name}_{device_name}")
                    print(f"   ✅ Accueil capturé ({device_name})")

                    # 2. Page contact (si trouvée)
                    if contact_url:
                        await capture_page(page, contact_url, f"{name}_contact_{device_name}")
                        print(f"   ✅ Contact capturé ({device_name})")

            except Exception as e:
                error_msg = str(e).splitlines()[0]
                if "ERR_NAME_NOT_RESOLVED" in error_msg:
                    print(f"   ⚠️  Site inaccessible (DNS) - ignoré")
                else:
                    print(f"   ❌ Erreur : {error_msg}")

            await page.close()
            await context.close()

        await browser.close()

if __name__ == "__main__":
    asyncio.run(sabotaging_bot())

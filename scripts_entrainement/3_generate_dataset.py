"""
3_generate_dataset.py — fabrique le jeu d'images (sain / cassé) pour entraîner le modèle.

Ce qui a changé par rapport à l'ancienne version (étape 3 du réentraînement) :

  1. MÊMES RÉGLAGES QUE L'AUDIT. Les captures utilisent backend/services/capture_settings.py
     (User-Agent, tailles d'écran, délais), la même source que l'audit. Avant, ce script
     utilisait le navigateur par défaut (User-Agent "HeadlessChrome", écran fixe 1440x900,
     mobile = fenêtre réduite avec un User-Agent d'ordinateur) : le modèle apprenait sur des
     pages que l'audit ne lui montre jamais, et certains sites servent même un contenu
     différent selon le User-Agent.

  2. LES PAGES EN ERREUR NE SONT PLUS "SAINES". Avant, une page 403/500 était enregistrée
     comme exemple sain. Maintenant, si le statut HTTP est >= 400, la page est rangée dans
     "cassé" : c'est une VRAIE panne, bien plus réaliste que notre simulation (CSS retiré).

  3. PLUS DE CAPTURE "contact mobile". L'audit ne capture la page contact qu'en bureau ;
     le modèle n'a donc jamais à juger un contact mobile.

  4. SITES DU JEU DE TEST EXCLUS (eval_sites.txt). Les sites relus à la main pour la mesure ne
     doivent jamais servir à entraîner, sinon la mesure ne vaut plus rien.

  5. POLITESSE ET SÉCURITÉ : pause de 2 à 4 s entre deux sites (comme l'audit) et arrêt
     après 5 sites en échec d'affilée (un blocage d'IP ne doit pas faire insister le script).

  6. DOSSIER DE SORTIE SÉPARÉ : par défaut "dataset_v2", pour ne pas mélanger de nouvelles
     images avec l'ancien jeu (dataset/), ni écraser quoi que ce soit.

Usage (depuis scripts_entrainement/) :
    ../backend/.venv/bin/python 3_generate_dataset.py --limit 20                 # petit essai
    ../backend/.venv/bin/python 3_generate_dataset.py --url https://exemple.fr   # un site précis (répétable)
"""
import os

# ⚠️ Voir backend/main.py pour l'explication complète : nécessaire avant tout
# subprocess.run() (pb_worker.py) pour éviter un crash macOS ("multi-threaded
# process forked"). Sans effet sur Linux/Dokploy.
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import re
import sys
import json
import random
import asyncio
import argparse
import subprocess
import unicodedata
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from urllib.parse import urlparse, urljoin

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, BACKEND_DIR)
from services import capture_settings as cs  # noqa: E402  (mêmes réglages que l'audit)

load_dotenv(os.path.join(BACKEND_DIR, ".env"))

_PB_WORKER_SCRIPT = os.path.join(BACKEND_DIR, "pb_worker.py")
EVAL_SITES_FILE = os.path.join(os.path.dirname(__file__), "eval_sites.txt")

PAUSE_BETWEEN_SITES = (2, 4)
MAX_CONSECUTIVE_SITE_FAILURES = 5


def normalize(text: str) -> str:
    """'Château de Roques' et 'CHATEAU_DE_ROQUES' doivent être reconnus comme le même site."""
    text = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def load_excluded_sites() -> set[str]:
    if not os.path.exists(EVAL_SITES_FILE):
        return set()
    with open(EVAL_SITES_FILE, encoding="utf-8") as f:
        return {normalize(line) for line in f if line.strip() and not line.startswith("#")}


def get_random_active_projects(limit: int) -> list[dict]:
    """
    Sous-processus isolé — comme dans audit_engine.py : authentifier le
    client PocketBase dans le même process que Playwright casse le
    lancement du driver Playwright sur macOS.
    """
    cmd = [sys.executable, _PB_WORKER_SCRIPT, "get_random_active_projects", json.dumps({"limit": limit})]
    # close_fds=False force posix_spawn() au lieu de fork()+exec() sur macOS —
    # voir audit_engine.py:call_pb_worker pour l'explication complète.
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, close_fds=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"pb_worker a échoué : {result.stderr.strip()[:300]}")
    return json.loads(result.stdout.strip())


def clean_url(raw_url):
    if not raw_url: return None
    url = raw_url.strip().split(" ")[0]
    return url if url.startswith("http") else "https://" + url


async def find_contact_url(page, base_url):
    """Tente /contact puis /contactez-nous. Retourne l'URL si trouvée, None sinon."""
    for slug in cs.CONTACT_SLUGS:
        contact_url = urljoin(base_url, slug)
        try:
            response = await page.goto(contact_url, wait_until="load", timeout=cs.CONTACT_TIMEOUT_MS)
            if response and response.status == 200:
                return contact_url
        except Exception:
            continue
    return None


async def capture_page(page, url, name_prefix, dirs, wait_ms):
    """Capture une page. Retourne le statut HTTP.

    - statut < 400 : une capture "healthy", puis la même page sans CSS en "broken"
      (la panne simulée, comme avant).
    - statut >= 400 : c'est déjà une vraie page d'erreur -> une seule capture, "broken".
    """
    response = await page.goto(url, wait_until="load", timeout=cs.HOME_TIMEOUT_MS)
    await page.wait_for_timeout(wait_ms)
    status = response.status if response else None

    if status is not None and status >= 400:
        await page.screenshot(path=f"{dirs['broken']}/{name_prefix}.png")
        return status

    await page.screenshot(path=f"{dirs['healthy']}/{name_prefix}.png")

    await page.evaluate("document.querySelectorAll('style, link[rel=\"stylesheet\"]').forEach(el => el.remove());")
    await asyncio.sleep(1)
    await page.screenshot(path=f"{dirs['broken']}/{name_prefix}.png")
    return status


async def capture_site(browser, site_name, url, dirs, rng):
    """Une visite par site, comme l'audit : un contexte bureau (taille aléatoire) et un
    contexte mobile séparé (User-Agent iPhone)."""
    desktop_ctx = await browser.new_context(viewport=cs.random_desktop_viewport(rng), user_agent=cs.DESKTOP_USER_AGENT)
    mobile_ctx = await browser.new_context(viewport=cs.MOBILE_VIEWPORT, user_agent=cs.MOBILE_USER_AGENT)
    desktop_page = await desktop_ctx.new_page()
    mobile_page = await mobile_ctx.new_page()
    try:
        contact_url = await find_contact_url(desktop_page, url)
        print(f"   ℹ️  Page contact : {contact_url or 'non trouvée'}")

        status = await capture_page(desktop_page, url, f"{site_name}_desktop", dirs, cs.WAIT_AFTER_LOAD_MS)
        print(f"   ✅ Accueil bureau (HTTP {status})")

        status = await capture_page(mobile_page, url, f"{site_name}_mobile", dirs, cs.WAIT_AFTER_LOAD_MS)
        print(f"   ✅ Accueil mobile (HTTP {status})")

        if contact_url:
            status = await capture_page(desktop_page, contact_url, f"{site_name}_contact_desktop", dirs,
                                        cs.CONTACT_WAIT_AFTER_LOAD_MS)
            print(f"   ✅ Contact bureau (HTTP {status})")
    finally:
        await desktop_ctx.close()
        await mobile_ctx.close()


def build_sites(args) -> list[dict]:
    if args.url:
        return [{"project_name": urlparse(clean_url(u)).netloc, "site_url": u} for u in args.url]
    excluded = load_excluded_sites()
    sites = get_random_active_projects(args.limit + len(excluded))
    kept = [s for s in sites if normalize(s["project_name"]) not in excluded]
    print(f"🧪 {len(sites)} sites tirés, {len(sites) - len(kept)} exclus (jeu de test), {len(kept[:args.limit])} gardés")
    return kept[:args.limit]


async def generate(args):
    dirs = {"healthy": os.path.join(args.out, "healthy"), "broken": os.path.join(args.out, "broken")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    sites = build_sites(args)
    rng = random.Random(args.seed)
    consecutive_failures = 0
    print(f"📁 Sortie : {args.out}/ | {len(sites)} site(s)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for idx, site in enumerate(sites, 1):
            url = clean_url(site["site_url"])
            name = site["project_name"].replace(" ", "_").replace("/", "-")
            print(f"👉 [{idx}/{len(sites)}] {site['project_name']}...")

            try:
                parsed = urlparse(url)
                if not parsed.scheme or not parsed.netloc:
                    print(f"   ❌ URL invalide : {url}")
                    continue
            except Exception as e:
                print(f"   ❌ Erreur de parsing : {e}")
                continue

            try:
                await capture_site(browser, name, url, dirs, rng)
                consecutive_failures = 0
            except Exception as e:
                msg = str(e).splitlines()[0]
                print(f"   ⚠️  Site inaccessible - ignoré ({msg[:80]})" if "ERR_NAME_NOT_RESOLVED" in msg
                      else f"   ❌ Erreur : {msg[:120]}")
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_SITE_FAILURES:
                    print(f"🛑 {consecutive_failures} échecs d'affilée : arrêt (blocage d'IP ou panne réseau ?)")
                    break

            if idx < len(sites):
                await asyncio.sleep(rng.uniform(*PAUSE_BETWEEN_SITES))

        await browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Génère le jeu d'images sain/cassé pour l'entraînement.")
    parser.add_argument("--limit", type=int, default=350, help="nombre de sites tirés au hasard dans PocketBase")
    parser.add_argument("--url", action="append", help="URL à capturer à la place de PocketBase (répétable)")
    parser.add_argument("--out", default="dataset_v2", help="dossier de sortie (défaut : dataset_v2)")
    parser.add_argument("--seed", type=int, default=None, help="graine aléatoire (tailles d'écran, pauses)")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(generate(parse_args()))

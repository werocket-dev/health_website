import os

# ⚠️ Voir main.py pour l'explication complète : nécessaire avant tout
# subprocess.run() (tf_infer.py, pb_worker.py) pour éviter un crash macOS
# ("multi-threaded process forked"). Sans effet sur Linux/Dokploy.
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import sys
import asyncio
import random
import sentry_sdk
from datetime import datetime, timezone
from playwright.async_api import async_playwright
import json
from dotenv import load_dotenv

from services.config import RESULTS_FILE
from services.agent_client import (
    connect_to_agent,
    update_plugin_via_agent,
    update_core_via_agent,
    parse_agent_data,
    refresh_site_in_results,
)
from services.scraper import scan_tech, scan_plugins_versions
from services.plugin_updates import check_updates_api, build_plugins_payload

# --- INITIALISATION ---
load_dotenv()

# Dossier pour les preuves visuelles
SCREENSHOT_DIR = "screenshots_audit"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

_TF_INFER_SCRIPT = os.path.join(os.path.dirname(__file__), "tf_infer.py")
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "werocket_vision_model.h5")
_PB_WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "pb_worker.py")

def call_pb_worker(command: str, args: dict | None = None):
    """
    Exécute une opération PocketBase dans un sous-processus isolé — le
    client PocketBase (httpx) authentifié dans ce process casse le
    lancement du driver Playwright sur macOS (même famille de conflit que
    celui déjà contourné pour TensorFlow, cf. predict_visual()).
    """
    import subprocess
    import time as time_module
    cmd = [sys.executable, _PB_WORKER_SCRIPT, command]
    if args is not None:
        cmd.append(json.dumps(args, ensure_ascii=False))

    # L'instance PocketBase a montré des timeouts réseau ponctuels en test —
    # un seul essai suffit à les absorber sans bloquer tout l'audit.
    last_error = None
    for attempt in range(2):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"pb_worker '{command}' : {data['error']}")
            return data
        last_error = result.stderr.strip()[:300]
        if attempt == 0:
            time_module.sleep(2)
    raise RuntimeError(f"pb_worker '{command}' a échoué : {last_error}")

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


# --- COEUR DU SCANNER ---

async def run_audit(sites_list=None, progress_callback=None, limit=None):
    def _progress(percent: int, label: str, status: str = "running"):
        if progress_callback:
            progress_callback({"status": status, "percent": percent, "label": label})
    """
    Lance un audit sur une liste de sites.

    Args:
        sites_list: Liste de dictionnaires avec les clés 'Client' et 'URL'
                   Ex: [{'Client': 'Mon Client', 'URL': 'https://example.com'}]
                   Si None, récupère les sites actifs depuis PocketBase
        limit: Borne le nombre de sites récupérés depuis PocketBase quand
               sites_list est None (utile pour un audit de test) — sans
               effet si sites_list est fourni explicitement.

    Returns:
        dict: Statistiques de l'audit avec détails par site
    """
    # 1. Récupération des sites à auditer
    if sites_list is None:
        # Mode par défaut : sites actifs depuis PocketBase (tous, ou `limit` premiers)
        sites = call_pb_worker("get_active_projects", {"limit": limit} if limit else {})
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

                # Capture mobile — contexte séparé avec un viewport fixe (390x844) qui
                # matche celui utilisé pour générer le dataset d'entraînement, pour que
                # l'IA évalue un rendu mobile réaliste plutôt qu'un desktop redimensionné.
                mobile_img_path = f"{SCREENSHOT_DIR}/{clean_name}_mobile.png"
                try:
                    mobile_context = await browser.new_context(
                        viewport={'width': 390, 'height': 844},
                        user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
                    )
                    mobile_page = await mobile_context.new_page()
                    await mobile_page.goto(url, wait_until="load", timeout=25000)
                    await mobile_page.wait_for_timeout(4000)
                    await mobile_page.screenshot(path=mobile_img_path)
                    await mobile_page.close()
                    await mobile_context.close()
                except Exception:
                    mobile_img_path = None

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

                    parsed = parse_agent_data(agent_result['data'])
                    version_wp = parsed["version_wp"]
                    version_php = parsed["version_php"]
                    version_mysql = parsed["version_mysql"]
                    theme_name = parsed["theme_name"]
                    theme_version = parsed["theme_version"]
                    licenses_info = parsed["licenses"]
                    plugins_dict = parsed["plugins_dict"]
                    tech_info = parsed["tech_info"]
                    updates_info = parsed["updates_info"]

                    print(f"   ✅ [AGENT] WP: {version_wp} | PHP: {version_php} | Builder: {tech_info['builder']} | Plugins: {len(plugins_dict)} | MAJ: {updates_info['count_updates']}")

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
                    licenses_info = {}

                    print(f"   ✅ [SCRAPING] Builder: {tech_info['builder']} | Plugins: {len(plugins_dict)} | MAJ: {updates_info['count_updates']}")

                # Stocker toutes les données pour la phase TF (après fermeture Playwright)
                collected_sites.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "url": url,
                    "img_path": img_path,
                    "mobile_img_path": mobile_img_path,
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
                    "licenses": licenses_info,
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
        diag_ia_mobile = predict_visual(entry["mobile_img_path"]) if entry.get("mobile_img_path") else {"status": "N/A", "score": 0}
        entry["diag_ia"] = diag_ia
        entry["diag_ia_contact"] = diag_ia_contact
        entry["diag_ia_mobile"] = diag_ia_mobile

        methode_scan = entry["methode_scan"]
        if methode_scan == "Agent":
            stats["methode_agent"] += 1
        else:
            stats["methode_scraping"] += 1

        print(f"   🧠 {entry['project_name']} → IA: {diag_ia['status']} ({diag_ia['score']}%) | Mobile: {diag_ia_mobile['status']} ({diag_ia_mobile['score']}%)")

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
        diag_ia_mobile = entry["diag_ia_mobile"]
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
        licenses_info = entry.get("licenses", {})

        updates_list = ", ".join(updates_info.get("updates_needed", [])) or "Aucune"

        # Accumulation résultats JSON (indépendant de la DB)
        json_results.append({
            "client": project_name,
            "url": url,
            "ia_status": diag_ia["status"],
            "ia_score": diag_ia["score"],
            "ia_status_contact": diag_ia_contact["status"],
            "ia_score_contact": diag_ia_contact["score"],
            "ia_status_mobile": diag_ia_mobile["status"],
            "ia_score_mobile": diag_ia_mobile["score"],
            "wp_version": version_wp,
            "php_version": version_php,
            "theme": theme_name,
            "builder": tech_info["builder"],
            "updates_count": updates_info["count_updates"],
            "mises_a_jour": updates_list,
            "methode": methode_scan,
            "licenses": licenses_info,
        })

        if not project_id:
            print(f"   ⚠️  Pas de project_id pour {project_name} — écriture PocketBase ignorée (results.json reste à jour)")
        else:
            try:
                pb_payload = {
                    "methode_scan": methode_scan,
                    "ia_status": diag_ia["status"],
                    "ia_score": diag_ia["score"],
                    "ia_status_contact": diag_ia_contact["status"],
                    "ia_score_contact": diag_ia_contact["score"],
                    "ia_status_mobile": diag_ia_mobile["status"],
                    "ia_score_mobile": diag_ia_mobile["score"],
                    "wp_version": version_wp,
                    "php_version": version_php,
                    "mysql_version": version_mysql,
                    "theme": theme_name,
                    "theme_version": theme_version,
                    "builder": tech_info["builder"],
                    "builder_version": tech_info["version"],
                    "updates_count": updates_info["count_updates"],
                    "licenses": licenses_info,
                }
                call_pb_worker("update_project_status", {
                    "project_id": project_id,
                    "latest": {
                        **pb_payload,
                        "mises_a_jour": updates_list,
                        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    },
                })
                call_pb_worker("create_site_audit", {"project_id": project_id, "entry": pb_payload})
                call_pb_worker("sync_site_plugins", {
                    "project_id": project_id,
                    "plugins": build_plugins_payload(plugins_dict, updates_info),
                })
            except Exception as db_err:
                print(f"   ⚠️  Écriture PocketBase ignorée ({project_name}): {db_err}")
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
            "ia_status_mobile": diag_ia_mobile["status"],
            "ia_score_mobile": diag_ia_mobile["score"],
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
#        WEROCKET_PRIVATE_KEY_HEX=votre_cle_privee_ed25519
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

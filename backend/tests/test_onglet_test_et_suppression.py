"""Tests de l'onglet Test (scope), de l'actualisation, de la suppression de plugin et de leurs garde-fous.

python -m unittest discover -s tests   (depuis backend/)
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKEND_API_KEY", "test-key")

from services import agent_client, config, job_guard, json_store, url_guard


def agent_payload(plugins, wp="6.8", php="8.3.0"):
    """Réponse de /status d'un site, au format de l'agent."""
    return {
        "versions": {"wordpress": wp, "php": php, "mysql": "8.0"},
        "theme": {"name": "Zero", "version": "1"},
        "themes": [],
        "licenses": {},
        "plugins": [
            {"name": n, "version": "1.0", "slug": p.split("/")[0], "path": p, "is_active": True,
             "update_available": {"available": False}}
            for n, p in plugins
        ],
    }


class TempFiles(unittest.TestCase):
    """Deux fichiers de résultats temporaires (parc et test) branchés partout où ils sont lus."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parc = os.path.join(self.tmp.name, "results.json")
        self.test = os.path.join(self.tmp.name, "test_results.json")
        self.parc_last = os.path.join(self.tmp.name, "last_audit.json")
        self.test_last = os.path.join(self.tmp.name, "test_last_audit.json")

    def write(self, path, entries):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)


class ScopeFilesTests(unittest.TestCase):
    def test_parc_et_test_ont_des_fichiers_distincts(self):
        parc, parc_last = config.scope_files("parc")
        test, test_last = config.scope_files("test")
        self.assertEqual(len({parc, parc_last, test, test_last}), 4)
        self.assertTrue(test.endswith("test_results.json"))

    def test_scope_inconnu_refuse(self):
        for scope in ("", "autre", "TEST", "../parc"):
            with self.assertRaises(ValueError):
                config.scope_files(scope)


class MergeTests(unittest.TestCase):
    def test_un_test_remplace_le_precedent(self):
        old = [{"url": "https://a.fr", "client": "A"}, {"url": "https://b.fr", "client": "B"}]
        new = [{"url": "https://c.fr", "client": "C"}]
        merged = json_store.merge_audit_results(old, new, "2000-01-01T00:00:00+00:00", False)
        self.assertEqual([e["url"] for e in merged], ["https://c.fr"])

    def test_un_audit_partiel_du_parc_conserve_les_autres_sites(self):
        old = [{"url": "https://a.fr", "client": "A"}, {"url": "https://b.fr", "client": "B"}]
        new = [{"url": "https://c.fr", "client": "C"}]
        merged = json_store.merge_audit_results(old, new, "2000-01-01T00:00:00+00:00", True)
        self.assertEqual({e["url"] for e in merged}, {"https://a.fr", "https://b.fr", "https://c.fr"})


class PartialAuditTests(unittest.TestCase):
    def test_regle_de_fusion_selon_le_scope(self):
        from audit_engine import is_partial_audit
        sites = [{"Client": "A", "URL": "https://a.fr"}]
        # Parc : audit d'un ou quelques sites, ou limité -> on garde les autres sites
        self.assertTrue(is_partial_audit("parc", sites, None))
        self.assertTrue(is_partial_audit("parc", None, 10))
        # Parc : audit complet -> remplace tout
        self.assertFalse(is_partial_audit("parc", None, None))
        # Test : toujours remplacé, quelle que soit la forme du tirage
        for args in ((sites, None), (None, 5), (None, None)):
            self.assertFalse(is_partial_audit("test", *args))


class UrlGuardTests(TempFiles):
    def setUp(self):
        super().setUp()
        self.write(self.parc, [{"url": "https://a.fr"}])
        self.write(self.test, [{"url": "https://b.fr"}])
        self.patch(url_guard, "RESULTS_FILE", self.parc)
        self.patch(url_guard, "TEST_RESULTS_FILE", self.test)

    def test_un_site_du_test_est_connu(self):
        self.assertEqual(url_guard.known_hosts(), {"a.fr", "b.fr"})

    def test_validation_en_lot_avec_liste_fournie(self):
        known = {"a.fr"}
        with mock.patch.object(url_guard, "_check_public", lambda url: None):
            url_guard.validate_site_url("https://a.fr", known=known)
            with self.assertRaises(url_guard.UrlRejected):
                url_guard.validate_site_url("https://inconnu.fr", known=known)

    def test_fichier_absent_ou_corrompu_refuse_tout(self):
        os.remove(self.test)
        with open(self.parc, "w") as f:
            f.write("{pas du json")
        self.assertEqual(url_guard.known_hosts(), set())


class RefreshTests(TempFiles):
    URL = "https://a.fr"

    def setUp(self):
        super().setUp()
        self.patch(agent_client, "RESULTS_FILE", self.parc)
        self.patch(agent_client, "TEST_RESULTS_FILE", self.test)
        self.agent = {"success": True, "data": agent_payload([("Akismet", "akismet/akismet.php")])}
        self.patch(agent_client, "connect_to_agent", lambda url: self.agent)

    def entry(self, **kw):
        return {"url": self.URL, "client": "A", "plugins_installed": [], **kw}

    def test_le_site_est_rafraichi_dans_les_deux_fichiers(self):
        self.write(self.parc, [self.entry()])
        self.write(self.test, [self.entry()])
        res = agent_client.refresh_site_in_results(self.URL)
        self.assertTrue(res["success"])
        for path in (self.parc, self.test):
            e = self.read(path)[0]
            self.assertEqual([p["path"] for p in e["plugins_installed"]], ["akismet/akismet.php"])
            self.assertEqual(e["wp_version"], "6.8")
            self.assertIn("refreshed_at", e)

    def test_site_seulement_dans_le_test(self):
        self.write(self.test, [self.entry()])
        self.assertTrue(agent_client.refresh_site_in_results(self.URL)["success"])
        self.assertEqual(len(self.read(self.test)[0]["plugins_installed"]), 1)
        self.assertFalse(os.path.exists(self.parc))

    def test_les_autres_sites_ne_sont_pas_touches(self):
        self.write(self.parc, [self.entry(), {"url": "https://b.fr", "client": "B", "wp_version": "1.0"}])
        agent_client.refresh_site_in_results(self.URL)
        self.assertEqual(self.read(self.parc)[1]["wp_version"], "1.0")

    def test_site_absent_des_deux_fichiers(self):
        self.write(self.parc, [{"url": "https://autre.fr"}])
        res = agent_client.refresh_site_in_results(self.URL)
        self.assertFalse(res["success"])
        self.assertIn("absent", res["error"])

    def test_aucun_fichier(self):
        self.assertFalse(agent_client.refresh_site_in_results(self.URL)["success"])

    def test_un_fichier_illisible_ne_bloque_pas_l_autre(self):
        with open(self.parc, "w") as f:
            f.write("{corrompu")
        self.write(self.test, [self.entry()])
        res = agent_client.refresh_site_in_results(self.URL)
        self.assertTrue(res["success"])
        self.assertEqual(len(self.read(self.test)[0]["plugins_installed"]), 1)

    def test_seul_fichier_illisible_remonte_l_erreur(self):
        with open(self.parc, "w") as f:
            f.write("{corrompu")
        res = agent_client.refresh_site_in_results(self.URL)
        self.assertFalse(res["success"])
        self.assertIn("results.json", res["error"])

    def test_agent_injoignable_ne_modifie_rien(self):
        self.write(self.parc, [self.entry(wp_version="1.0")])
        self.agent = {"success": False, "error": "Délai de connexion dépassé"}
        res = agent_client.refresh_site_in_results(self.URL)
        self.assertFalse(res["success"])
        self.assertEqual(self.read(self.parc)[0]["wp_version"], "1.0")

    def test_la_liste_des_plugins_est_remplacee_pas_cumulee(self):
        self.write(self.parc, [self.entry(plugins_installed=[{"name": "Ancien", "path": "ancien/ancien.php"}])])
        agent_client.refresh_site_in_results(self.URL)
        self.assertEqual([p["path"] for p in self.read(self.parc)[0]["plugins_installed"]], ["akismet/akismet.php"])


class ParseAgentDataTests(unittest.TestCase):
    def test_liste_complete_avec_chemins(self):
        parsed = agent_client.parse_agent_data(agent_payload([("Akismet", "akismet/akismet.php"), ("Hello", "hello.php")]))
        self.assertEqual(
            [(p["name"], p["path"], p["slug"], p["is_active"]) for p in parsed["plugins_installed"]],
            [("Akismet", "akismet/akismet.php", "akismet", True), ("Hello", "hello.php", "hello.php", True)],
        )

    def test_site_sans_plugin(self):
        self.assertEqual(agent_client.parse_agent_data(agent_payload([]))["plugins_installed"], [])


class DeletePluginViaAgentTests(unittest.TestCase):
    """Signature et lecture des réponses de /delete-plugin, sans réseau."""

    class FakeKey:
        def __init__(self):
            self.messages = []

        def sign(self, message):
            self.messages.append(message.decode())
            return mock.Mock(signature=b"\x01" * 64)

    def run_delete(self, response):
        key = self.FakeKey()
        session = mock.Mock()
        session.post.return_value = response
        with mock.patch.object(agent_client, "AGENT_AVAILABLE", True), \
             mock.patch.object(agent_client, "_signing_key", key), \
             mock.patch.object(agent_client.requests, "Session", return_value=session), \
             mock.patch.object(agent_client, "_resolve_final_base_url", lambda s, u: "https://a.fr"), \
             mock.patch.object(agent_client.time, "time", lambda: 1700000000):
            result = agent_client.delete_plugin_via_agent("https://a.fr", "akismet/akismet.php")
        return result, key, session

    def reply(self, status, body):
        r = mock.Mock(status_code=status)
        r.json.return_value = body
        return r

    def test_le_chemin_du_plugin_est_signe(self):
        result, key, session = self.run_delete(self.reply(200, {"success": True, "message": "Plugin supprimé"}))
        self.assertTrue(result["success"])
        self.assertEqual(key.messages, ["https://a.fr|1700000000|/werocket/v1/delete-plugin|akismet/akismet.php"])
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["plugin_path"], "akismet/akismet.php")
        self.assertTrue(session.post.call_args.args[0].endswith("/wp-json/werocket/v1/delete-plugin"))

    def test_erreur_metier_de_l_agent(self):
        result, *_ = self.run_delete(self.reply(404, {"code": "plugin_not_found", "message": "Le plugin spécifié est introuvable."}))
        self.assertFalse(result["success"])
        self.assertIn("introuvable", result["error"])

    def test_agent_trop_ancien_sans_la_route(self):
        result, *_ = self.run_delete(self.reply(404, {"code": "rest_no_route", "message": "No route"}))
        self.assertIn("agent WP à mettre à jour", result["error"])

    def test_succes_false_en_200(self):
        result, *_ = self.run_delete(self.reply(200, {"success": False, "message": "La suppression du plugin a échoué."}))
        self.assertFalse(result["success"])

    def test_sans_cle_agent(self):
        with mock.patch.object(agent_client, "AGENT_AVAILABLE", False):
            self.assertFalse(agent_client.delete_plugin_via_agent("https://a.fr", "a/a.php")["success"])


class ApiTests(TempFiles):
    def setUp(self):
        super().setUp()
        import main
        from fastapi.testclient import TestClient
        self.main = main
        main._BACKEND_API_KEY = "test-key"
        self.h = {"X-API-Key": "test-key"}
        self.client = TestClient(main.app)
        main._pz_jobs.clear()
        main._bd_jobs.clear()
        main._audit_progress.update({"status": "idle", "percent": 0, "label": "", "_locked": False})
        self.patch(main, "_BD_PACE_SECONDS", (0, 0))
        # Garde-fou : aucun test ne doit pouvoir lancer un vrai audit (Playwright, agents, PocketBase),
        # même si une validation régresse. Les tests qui veulent observer l'appel le remplacent.
        self.audit_calls = []

        async def no_audit(*args, **kwargs):
            self.audit_calls.append(args)

        self.patch(main, "run_audit", no_audit)
        self.patch(main, "call_pb_worker", lambda *a, **k: (_ for _ in ()).throw(AssertionError("PocketBase appelé par un test")))
        self.patch(main, "RESULTS_FILE", self.parc)
        self.patch(main, "LAST_AUDIT_FILE", self.parc_last)
        self.patch(main, "scope_files", lambda scope: {
            "parc": (self.parc, self.parc_last), "test": (self.test, self.test_last)}[scope] if scope in ("parc", "test")
            else (_ for _ in ()).throw(ValueError(scope)))
        for module in (url_guard, agent_client):
            self.patch(module, "RESULTS_FILE", self.parc)
            self.patch(module, "TEST_RESULTS_FILE", self.test)
        self.patch(url_guard, "_check_public", lambda url: None)
        self.write(self.parc, [
            {"client": "Site A", "url": "https://a.fr", "plugins_installed": [
                {"name": "Akismet", "path": "akismet/akismet.php", "version": "5", "is_active": True},
                {"name": "Hello Dolly", "path": "hello.php", "version": "1", "is_active": False},
                {"name": "Agent", "path": "werocket-agent/werocket-agent.php", "version": "3", "is_active": True},
            ]},
            {"client": "Site B", "url": "https://b.fr", "plugins_installed": [
                {"name": "Akismet", "path": "akismet/akismet.php", "version": "4", "is_active": True},
            ]},
        ])

    def get(self, path, **params):
        return self.client.get(path, headers=self.h, params=params)

    def post(self, path, body):
        return self.client.post(path, json=body, headers=self.h)

    # --- scope : récap, sites, liste installée -------------------------------------------------
    def test_recap_du_test_est_vide_sans_fichier_et_ne_reprend_pas_le_parc(self):
        data = self.get("/api/recap", scope="test").json()
        self.assertEqual(data["total_sites"], 0)
        self.assertIsNone(data["last_audit_finished_at"])
        self.assertEqual(self.get("/api/recap", scope="parc").json()["total_sites"], 2)

    def test_recap_du_test_ne_retombe_pas_sur_le_parc_si_le_fichier_est_corrompu(self):
        with open(self.test, "w") as f:
            f.write("{corrompu")
        self.assertEqual(self.get("/api/recap", scope="test").json()["total_sites"], 0)

    def test_recap_du_test_lit_son_propre_fichier(self):
        self.write(self.test, [{"client": "T", "url": "https://t.fr", "mises_a_jour": "Aucune"}])
        self.write(self.test_last, {"finished_at": "2026-10-22 10:00:00"})
        data = self.get("/api/recap", scope="test").json()
        self.assertEqual(data["total_sites"], 1)
        self.assertEqual(data["last_audit_finished_at"], "2026-10-22 10:00:00")

    def test_le_recap_n_embarque_plus_la_liste_des_plugins(self):
        self.assertNotIn("installed_plugins", self.get("/api/recap").json())

    def test_scope_invalide_refuse_partout(self):
        for path in ("/api/recap", "/api/sites", "/api/plugins/installed"):
            self.assertEqual(self.get(path, scope="nimporte").status_code, 400, path)

    def test_sites_par_scope(self):
        self.write(self.test, [{"client": "T", "url": "https://t.fr"}])
        self.assertEqual([s["url"] for s in self.get("/api/sites", scope="test").json()["sites"]], ["https://t.fr"])
        self.assertEqual(len(self.get("/api/sites").json()["sites"]), 2)

    def test_liste_installee_groupee_triee_et_filtree(self):
        plugins = self.get("/api/plugins/installed").json()["plugins"]
        self.assertEqual([p["plugin_path"] for p in plugins], ["akismet/akismet.php"])  # ni l'agent ni hello.php
        self.assertEqual(plugins[0]["sites_count"], 2)
        self.assertEqual({s["client"]: s["version"] for s in plugins[0]["sites"]}, {"Site A": "5", "Site B": "4"})

    def test_liste_installee_du_test_est_independante(self):
        self.assertEqual(self.get("/api/plugins/installed", scope="test").json()["plugins"], [])

    def test_liste_installee_sans_donnees_anciennes(self):
        self.write(self.parc, [{"client": "Vieux", "url": "https://v.fr"}])  # ancien format, sans plugins_installed
        self.assertEqual(self.get("/api/plugins/installed").json()["plugins"], [])

    def test_authentification_requise(self):
        for method, path, body in (
            ("get", "/api/plugins/installed", None),
            ("get", "/api/jobs/current", None),
            ("post", "/api/plugins/delete", {"plugin_path": "a/a.php", "urls": ["https://a.fr"]}),
            ("post", "/api/plugins/refresh", {"scope": "parc"}),
            ("post", "/api/scan", {"scope": "test", "limit": 3}),
        ):
            r = getattr(self.client, method)(path, **({"json": body} if body else {}))
            self.assertEqual(r.status_code, 401, path)

    # --- /api/scan -------------------------------------------------------------------------
    def test_scan_selection_vide_refusee(self):
        r = self.post("/api/scan", {"scope": "test", "urls": []})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.main._audit_progress.get("_locked"))
        self.assertEqual(self.audit_calls, [])  # aucun audit n'a été lancé

    def test_scan_choix_de_sites_reserve_au_test(self):
        self.assertEqual(self.post("/api/scan", {"scope": "parc", "urls": ["https://a.fr"]}).status_code, 400)

    def test_scan_site_inconnu_refuse(self):
        self.assertEqual(self.post("/api/scan", {"scope": "test", "urls": ["https://inconnu.fr"]}).status_code, 400)

    def test_scan_scope_invalide(self):
        self.assertEqual(self.post("/api/scan", {"scope": "x"}).status_code, 400)

    def test_scan_deja_en_cours(self):
        self.main._audit_progress["_locked"] = True
        self.assertEqual(self.post("/api/scan", {"scope": "test", "limit": 3}).status_code, 409)

    def test_scan_envoie_le_scope_et_les_noms_au_moteur(self):
        calls = []

        async def fake_audit(*args, **kwargs):
            calls.append(args)

        with mock.patch.object(self.main, "run_audit", fake_audit):
            r = self.post("/api/scan", {"scope": "test", "urls": ["https://a.fr", "https://a.fr", "https://b.fr"]})
        self.assertEqual(r.status_code, 200, r.text)
        sites_list, _progress, limit, random_sample, scope = calls[0]
        self.assertEqual(scope, "test")
        self.assertEqual([s["URL"] for s in sites_list], ["https://a.fr", "https://b.fr"])  # dédoublonné
        self.assertEqual([s["Client"] for s in sites_list], ["Site A", "Site B"])
        self.assertIsNone(limit)

    def test_scan_tirage_au_hasard(self):
        calls = []

        async def fake_audit(*args, **kwargs):
            calls.append(args)

        with mock.patch.object(self.main, "run_audit", fake_audit):
            r = self.post("/api/scan", {"scope": "test", "limit": 5, "random_sample": True})
        self.assertEqual(r.status_code, 200, r.text)
        sites_list, _progress, limit, random_sample, scope = calls[0]
        self.assertEqual((sites_list, limit, random_sample, scope), (None, 5, True, "test"))

    def test_un_audit_qui_plante_passe_en_erreur_et_libere_le_verrou(self):
        async def boom(*args, **kwargs):
            raise RuntimeError("Executable doesn't exist\nautre ligne")

        with mock.patch.object(self.main, "run_audit", boom):
            asyncio.run(self.main._run_audit_locked(None, self.main._on_progress, None, False, "test"))
        progress = self.get("/api/progress").json()
        self.assertEqual(progress["status"], "error")
        self.assertIn("Executable doesn't exist", progress["label"])
        self.assertNotIn("autre ligne", progress["label"])
        self.assertFalse(self.main._audit_progress["_locked"])

    # --- suppression ---------------------------------------------------------------------
    def test_suppression_validations(self):
        ok = {"plugin_path": "akismet/akismet.php", "urls": ["https://a.fr"]}
        for body, expected in (
            ({**ok, "plugin_path": "werocket-agent/werocket-agent.php"}, 400),
            ({**ok, "plugin_path": "../etc/passwd"}, 400),
            ({**ok, "plugin_path": "hello.php"}, 400),
            ({**ok, "plugin_path": "a/b/c.php"}, 400),
            ({**ok, "urls": []}, 400),
            ({**ok, "urls": ["https://inconnu.fr"]}, 400),
        ):
            self.assertEqual(self.post("/api/plugins/delete", body).status_code, expected, body)

    def test_suppression_demarre_un_job(self):
        r = self.post("/api/plugins/delete", {"plugin_path": "akismet/akismet.php", "urls": ["https://a.fr", "https://a.fr"]})
        self.assertEqual(r.status_code, 200, r.text)
        job = self.main._pz_jobs[r.json()["job_id"]]
        self.assertEqual(list(job["sites"]), ["https://a.fr"])  # dédoublonné

    # --- actualisation ---------------------------------------------------------------------
    def test_actualisation_sans_site(self):
        os.remove(self.parc)
        self.assertEqual(self.post("/api/plugins/refresh", {"scope": "parc"}).status_code, 400)
        self.assertEqual(self.post("/api/plugins/refresh", {"scope": "test"}).status_code, 400)

    def test_actualisation_scope_invalide(self):
        self.assertEqual(self.post("/api/plugins/refresh", {"scope": "x"}).status_code, 400)

    def test_actualisation_cible_les_sites_du_bon_scope(self):
        self.write(self.test, [{"client": "T", "url": "https://t.fr"}])
        r = self.post("/api/plugins/refresh", {"scope": "test"})
        self.assertEqual(list(self.main._pz_jobs[r.json()["job_id"]]["sites"]), ["https://t.fr"])

    # --- un seul job à la fois, et jamais pendant un audit --------------------------------------
    def test_pas_de_job_pendant_un_audit(self):
        self.main._audit_progress["_locked"] = True
        for path, body in (
            ("/api/plugins/delete", {"plugin_path": "akismet/akismet.php", "urls": ["https://a.fr"]}),
            ("/api/plugins/refresh", {"scope": "parc"}),
        ):
            r = self.post(path, body)
            self.assertEqual(r.status_code, 409, path)
            self.assertIn("audit", r.json()["detail"])

    def test_pas_de_deuxieme_job(self):
        self.main._pz_jobs["en-cours"] = {"finished": False, "aborted_reason": None, "slug": "x", "sites": {}}
        r = self.post("/api/plugins/refresh", {"scope": "parc"})
        self.assertEqual(r.status_code, 409)
        self.main._pz_jobs["en-cours"]["finished"] = True
        self.assertEqual(self.post("/api/plugins/refresh", {"scope": "parc"}).status_code, 200)

    def test_pas_de_job_pendant_une_installation_breakdance(self):
        self.main._bd_jobs["bd"] = {"finished": False, "aborted_reason": None, "sites": {}}
        self.assertEqual(self.post("/api/plugins/refresh", {"scope": "parc"}).status_code, 409)

    # --- reprise du suivi après un changement de page ----------------------------------------
    def test_aucun_job_en_cours(self):
        self.assertEqual(self.get("/api/jobs/current").json(), {"job_id": None, "kind": None})

    def test_chaque_job_porte_son_type(self):
        # (Le client de test ferme sa boucle après chaque requête et annule la tâche : on lit donc
        # le job créé, pas /api/jobs/current, qui le verrait déjà terminé.)
        self.write(self.parc, [{"client": "A", "url": "https://a.fr"}])
        with mock.patch.object(self.main.plugin_zip, "get_meta", lambda slug: {"sha256": "a" * 64, "version": "1"}), \
             mock.patch.object(self.main.plugin_zip, "build_download_url", lambda slug, sha: "https://backend.test/dl/x"):
            for path, body, kind in (
                ("/api/plugins/refresh", {"scope": "parc"}, "refresh"),
                ("/api/plugins/delete", {"plugin_path": "akismet/akismet.php", "urls": ["https://a.fr"]}, "delete"),
                ("/api/plugins/install", {"slug": "mon-plugin", "urls": ["https://a.fr"]}, "install"),
            ):
                self.main._pz_jobs.clear()
                job_id = self.post(path, body).json()["job_id"]
                self.assertEqual(self.main._pz_jobs[job_id]["kind"], kind, path)

    def test_le_job_en_cours_est_retrouve_avec_son_type(self):
        for kind in ("install", "delete", "refresh"):
            self.main._pz_jobs.clear()
            self.main._pz_jobs["j"] = {"finished": False, "aborted_reason": None, "kind": kind, "slug": "x", "sites": {}}
            self.assertEqual(self.get("/api/jobs/current").json(), {"job_id": "j", "kind": kind})

    def test_un_job_termine_n_est_plus_propose(self):
        self.main._pz_jobs["j"] = {"finished": True, "aborted_reason": None, "kind": "refresh", "sites": {}}
        self.assertIsNone(self.get("/api/jobs/current").json()["job_id"])

    def test_job_breakdance_retrouve(self):
        self.main._bd_jobs["bd"] = {"finished": False, "aborted_reason": None, "kind": "breakdance", "sites": {}}
        self.assertEqual(self.get("/api/jobs/current").json(), {"job_id": "bd", "kind": "breakdance"})

    def test_le_plus_recent_des_jobs_en_cours_est_choisi(self):
        self.main._pz_jobs["ancien"] = {"finished": False, "aborted_reason": None, "kind": "refresh", "sites": {}}
        self.main._pz_jobs["recent"] = {"finished": False, "aborted_reason": None, "kind": "delete", "sites": {}}
        self.assertEqual(self.get("/api/jobs/current").json()["job_id"], "recent")

    def test_route_job_courant_distincte_de_celle_d_un_job(self):
        # /api/jobs/current ne doit jamais être lue comme un job_id de /api/plugins/jobs/{job_id}
        self.assertEqual(self.get("/api/plugins/jobs/current").status_code, 404)

    def test_suivi_d_un_job_inconnu(self):
        self.assertEqual(self.get("/api/plugins/jobs/nimporte").status_code, 404)


class SiteJobRunnerTests(TempFiles):
    """Le runner commun : succès, erreurs isolées par site, garde-fous, licence Breakdance."""

    def setUp(self):
        super().setUp()
        import main
        self.main = main
        main._pz_jobs.clear()
        self.patch(main, "_BD_PACE_SECONDS", (0, 0))
        self.patch(main.job_guard, "resolve_ip", lambda url: "10." + str(abs(hash(url)) % 250) + ".0.1")
        self.refreshed = []
        self.patch(main, "refresh_site_in_results", lambda url: self.refreshed.append(url) or {"success": True})

    def run_job(self, urls, action, refresh_after=True):
        self.main._pz_jobs["j"] = {"finished": False, "aborted_reason": None, "slug": "x",
                                   "sites": {u: {"status": "pending"} for u in urls}}
        asyncio.run(self.main._run_site_job("j", urls, action, "test", refresh_after))
        return self.main._pz_jobs["j"]

    def test_succes_et_champs_publies(self):
        job = self.run_job(["https://a.fr"], lambda url, state: {"success": True, "message": "ok", "fields": {"deleted": True}})
        self.assertTrue(job["finished"])
        self.assertEqual(job["sites"]["https://a.fr"], {"status": "done", "message": "ok", "deleted": True})
        self.assertEqual(self.refreshed, ["https://a.fr"])

    def test_pas_de_relecture_quand_l_action_la_fait_deja(self):
        self.run_job(["https://a.fr"], lambda url, state: {"success": True, "message": "ok"}, refresh_after=False)
        self.assertEqual(self.refreshed, [])

    def test_un_echec_est_isole(self):
        def action(url, state):
            return {"success": url != "https://b.fr", "error": "boom", "message": "ok"}

        job = self.run_job(["https://a.fr", "https://b.fr", "https://c.fr"], action)
        self.assertEqual({u: s["status"] for u, s in job["sites"].items()},
                         {"https://a.fr": "done", "https://b.fr": "error", "https://c.fr": "done"})
        self.assertEqual(job["sites"]["https://b.fr"]["message"], "boom")
        self.assertNotIn("https://b.fr", self.refreshed)  # pas de relecture après un échec

    def test_une_exception_ne_fait_pas_tomber_le_job(self):
        def action(url, state):
            if url == "https://b.fr":
                raise RuntimeError("zip supprimé")
            return {"success": True, "message": "ok"}

        job = self.run_job(["https://a.fr", "https://b.fr"], action)
        self.assertTrue(job["finished"])
        self.assertEqual(job["sites"]["https://b.fr"]["status"], "error")
        self.assertIn("zip supprimé", job["sites"]["https://b.fr"]["message"])
        self.assertEqual(job["sites"]["https://a.fr"]["status"], "done")

    def test_statut_final_error_avec_champs_conserves(self):
        job = self.run_job(["https://a.fr"], lambda url, state: {
            "success": True, "status": "error", "message": "Installé, mais licence non activée", "fields": {"installed": True}})
        site = job["sites"]["https://a.fr"]
        self.assertEqual((site["status"], site["installed"]), ("error", True))
        self.assertEqual(self.refreshed, ["https://a.fr"])  # le plugin est bien installé : on relit le site

    def test_le_job_s_arrete_apres_des_refus_403_consecutifs(self):
        # Une IP par site, pour ne pas déclencher le blocage par hébergeur : seul le streak 403 joue.
        urls = [f"https://s{i}.fr" for i in range(job_guard.BLOCKING_STREAK_THRESHOLD + 3)]
        job = self.run_job(urls, lambda url, state: {"success": False, "error": "403 - Accès refusé"})
        self.assertTrue(job["finished"])
        self.assertIsNotNone(job["aborted_reason"])
        self.assertTrue(any(s["status"] == "skipped" for s in job["sites"].values()))

    def test_les_sites_d_un_meme_hebergeur_injoignable_sont_ignores(self):
        self.patch(self.main.job_guard, "resolve_ip", lambda url: "1.2.3.4")
        urls = [f"https://s{i}.fr" for i in range(6)]
        job = self.run_job(urls, lambda url, state: {"success": False, "error": "Délai de connexion dépassé"})
        self.assertGreaterEqual(sum(s["status"] == "skipped" for s in job["sites"].values()), 1)


class InstallLicenseTests(unittest.TestCase):
    """Clé de licence Breakdance appliquée après l'installation, et seulement pour Breakdance."""

    def setUp(self):
        import main
        self.main = main
        self.meta = {"sha256": "a" * 64, "version": "2.8.3"}
        self.activations = []
        patches = [
            mock.patch.object(main.plugin_zip, "get_meta", lambda slug: self.meta),
            mock.patch.object(main.plugin_zip, "build_download_url", lambda slug, sha: "https://backend.test/dl/x"),
            mock.patch.object(main, "install_plugin_zip", lambda url, zip_url, sha: {
                "success": True, "message": "Plugin installé", "installed": True,
                "old_version": None, "new_version": "2.8.3", "is_active": True}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def activate(self, result):
        def fake(url, key):
            self.activations.append((url, key))
            return result
        p = mock.patch.object(self.main, "activate_license_via_agent", fake)
        p.start()
        self.addCleanup(p.stop)

    def run_action(self, slug, key, state=None):
        return self.main._install_action(slug, key)("https://a.fr", state if state is not None else {})

    def test_breakdance_avec_cle_valide(self):
        self.activate({"success": True, "message": "Licence activée"})
        res = self.run_action("breakdance", "  ma-cle  ")
        self.assertEqual((res["status"], res["message"]), ("done", "Licence activée"))
        self.assertEqual(self.activations, [("https://a.fr", "ma-cle")])  # clé nettoyée
        self.assertEqual(res["fields"]["new_version"], "2.8.3")

    def test_breakdance_cle_refusee_laisse_le_plugin_installe(self):
        self.activate({"success": False, "error": "Clé invalide"})
        res = self.run_action("breakdance", "mauvaise")
        self.assertEqual(res["status"], "error")
        self.assertIn("Clé invalide", res["message"])
        self.assertTrue(res["success"])  # l'installation, elle, a réussi
        self.assertTrue(res["fields"]["installed"])

    def test_breakdance_sans_cle(self):
        self.activate({"success": True})
        for key in (None, "", "   "):
            res = self.run_action("breakdance", key)
            self.assertIn("aucune clé", res["message"])
        self.assertEqual(self.activations, [])

    def test_un_autre_plugin_ignore_la_cle(self):
        self.activate({"success": True})
        res = self.run_action("mon-plugin", "ma-cle")
        self.assertEqual((res["status"], res["message"]), ("done", "Plugin installé"))
        self.assertEqual(self.activations, [])

    def test_zip_supprime_entre_temps(self):
        self.meta = None
        with self.assertRaises(RuntimeError):
            self.run_action("mon-plugin", None)

    def test_echec_d_installation_remonte_tel_quel(self):
        with mock.patch.object(self.main, "install_plugin_zip", lambda *a: {"success": False, "error": "502 zip injoignable"}):
            res = self.run_action("breakdance", "cle")
        self.assertEqual(res, {"success": False, "error": "502 zip injoignable"})


if __name__ == "__main__":
    unittest.main()

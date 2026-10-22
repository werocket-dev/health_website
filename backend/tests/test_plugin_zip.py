"""Tests de l'ajout de plugin à distance : python -m unittest discover -s tests (depuis backend/)."""
import asyncio
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKEND_API_KEY", "test-key")

from services import plugin_zip, url_guard

PLUGIN_PHP = "<?php\n/*\nPlugin Name: Mon Plugin\nVersion: 1.4.2\n*/\n"


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return path


class PluginZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(plugin_zip, "PLUGINS_DIR", os.path.join(self.tmp.name, "plugins"))
        patcher.start()
        self.addCleanup(patcher.stop)
        os.makedirs(plugin_zip.PLUGINS_DIR)
        patcher = mock.patch.dict(os.environ, {"PUBLIC_BACKEND_URL": "https://backend.test"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _store(self, files=None):
        path = make_zip(os.path.join(self.tmp.name, "up.zip"), files or {"mon-plugin/mon-plugin.php": PLUGIN_PHP})
        return plugin_zip.store_zip(path, "mon-plugin.zip")

    def test_lit_slug_nom_et_version(self):
        meta = self._store()
        self.assertEqual((meta["slug"], meta["name"], meta["version"]), ("mon-plugin", "Mon Plugin", "1.4.2"))
        self.assertEqual(plugin_zip.get_meta("mon-plugin")["sha256"], meta["sha256"])
        self.assertEqual([m["slug"] for m in plugin_zip.list_zips()], ["mon-plugin"])

    def test_refuse_les_zips_invalides(self):
        cas = {
            "deux racines": {"a/a.php": PLUGIN_PHP, "b/b.php": PLUGIN_PHP},
            "sans en-tête": {"a/a.php": "<?php echo 1;"},
            "traversal": {"a/a.php": PLUGIN_PHP, "a/../x.php": "x"},
            "fichier à la racine": {"a.php": PLUGIN_PHP},
        }
        for nom, files in cas.items():
            path = make_zip(os.path.join(self.tmp.name, "bad.zip"), files)
            with self.assertRaises(plugin_zip.ZipError, msg=nom):
                plugin_zip.validate_zip(path)

    def test_ignore_macosx(self):
        path = make_zip(os.path.join(self.tmp.name, "mac.zip"),
                        {"p/p.php": PLUGIN_PHP, "__MACOSX/p/._p.php": "x"})
        self.assertEqual(plugin_zip.validate_zip(path)["slug"], "p")

    def test_token_lie_au_slug_au_sha_et_a_la_duree(self):
        meta = self._store()
        token = plugin_zip.make_token("mon-plugin", meta["sha256"])
        self.assertTrue(plugin_zip.verify_token("mon-plugin", token))
        self.assertFalse(plugin_zip.verify_token("autre", token))
        self.assertFalse(plugin_zip.verify_token("mon-plugin", plugin_zip.make_token("mon-plugin", meta["sha256"], ttl=-1)))
        self.assertFalse(plugin_zip.verify_token("mon-plugin", token + "x"))

    def test_https_exige_pour_les_liens(self):
        with mock.patch.dict(os.environ, {"PUBLIC_BACKEND_URL": "http://backend.test"}):
            with self.assertRaises(plugin_zip.ZipError):
                plugin_zip.build_download_url("mon-plugin", "0" * 64)
            with mock.patch.dict(os.environ, {"PLUGIN_ZIP_ALLOW_HTTP": "1"}):
                self.assertTrue(plugin_zip.build_download_url("mon-plugin", "0" * 64).startswith("http://backend.test/dl/plugin/mon-plugin/"))

    def test_slug_invalide_refuse(self):
        for slug in ("../etc", "a/b", ""):
            self.assertIsNone(plugin_zip.get_meta(slug))


class PluginApiTests(unittest.TestCase):
    """Parcours complet via l'API, avec un faux agent : upload → lien → installation → suivi."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        results = os.path.join(self.tmp.name, "results.json")
        with open(results, "w") as f:
            json.dump([{"client": "Site A", "url": "https://a.fr"}, {"client": "Site B", "url": "https://b.fr"}], f)
        for target, value in (
            (plugin_zip, ("PLUGINS_DIR", os.path.join(self.tmp.name, "plugins"))),
            (url_guard, ("RESULTS_FILE", results)),
        ):
            patcher = mock.patch.object(target, *value)
            patcher.start()
            self.addCleanup(patcher.stop)
        os.makedirs(plugin_zip.PLUGINS_DIR)
        patcher = mock.patch.dict(os.environ, {"PUBLIC_BACKEND_URL": "https://backend.test", "BACKEND_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)

        import main
        from fastapi.testclient import TestClient
        main._BACKEND_API_KEY = "test-key"
        for name, value in (("RESULTS_FILE", results), ("scope_files", lambda scope: (results, results))):
            patcher = mock.patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.main = main
        self.client = TestClient(main.app)
        self.h = {"X-API-Key": "test-key"}
        # Garde-fou : aucun test de cette classe ne doit pouvoir lancer un vrai audit.
        patcher = mock.patch.object(main, "run_audit", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        main._pz_jobs.clear()
        main._BD_PACE_SECONDS = (0, 0)

    def _upload(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mon-plugin/mon-plugin.php", PLUGIN_PHP)
        return self.client.post("/api/plugins/zip", content=buf.getvalue(),
                                headers={**self.h, "X-Filename": "mon-plugin.zip"})

    def test_parcours_complet(self):
        r = self._upload()
        self.assertEqual(r.status_code, 200, r.text)
        meta = r.json()
        self.assertEqual(meta["slug"], "mon-plugin")
        self.assertEqual(self.client.get("/api/plugins/zips", headers=self.h).json()["zips"][0]["version"], "1.4.2")
        self.assertEqual(len(self.client.get("/api/sites", headers=self.h).json()["sites"]), 2)

        # Le lien de téléchargement fonctionne avec le token, et pas sans
        token = plugin_zip.make_token("mon-plugin", meta["sha256"])
        self.assertEqual(self.client.get(f"/dl/plugin/mon-plugin/{token}").status_code, 200)
        self.assertEqual(self.client.get("/dl/plugin/mon-plugin/faux").status_code, 403)

        calls = []

        def fake_install(url, zip_url, sha256):
            calls.append((url, zip_url, sha256))
            return {"success": True, "message": "Plugin installé depuis le zip", "installed": True,
                    "old_version": None, "new_version": "1.4.2", "is_active": True}

        urls = ["https://a.fr", "https://b.fr"]
        with mock.patch.object(self.main, "install_plugin_zip", fake_install), \
             mock.patch.object(self.main, "refresh_site_in_results", lambda url: {"success": True}):
            r = self.client.post("/api/plugins/install", json={"slug": "mon-plugin", "urls": urls}, headers=self.h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn(r.json()["job_id"], self.main._pz_jobs)
            # Le client de test ferme sa boucle après chaque requête (la tâche d'arrière-plan est
            # annulée) : on exécute le job nous-mêmes, comme le fait uvicorn avec sa boucle persistante.
            self.main._pz_jobs.clear()
            self.main._pz_jobs["j"] = {"finished": False, "aborted_reason": None, "slug": "mon-plugin",
                                       "sites": {u: {"status": "pending"} for u in urls}}
            asyncio.run(self.main._run_plugin_install_job("j", "mon-plugin", urls))
            job = self.client.get("/api/plugins/jobs/j", headers=self.h).json()

        self.assertTrue(job["finished"])
        self.assertEqual({s["status"] for s in job["sites"].values()}, {"done"})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[1].startswith("https://backend.test/dl/plugin/mon-plugin/") and c[2] == meta["sha256"] for c in calls))

    def test_suppression_et_actualisation(self):
        urls = ["https://a.fr", "https://b.fr"]
        deleted = []

        def fake_delete(url, path):
            deleted.append((url, path))
            return {"success": True, "message": "Plugin supprimé"}

        def run(job_coro_factory):
            self.main._pz_jobs.clear()
            self.main._pz_jobs["j"] = {"finished": False, "aborted_reason": None, "slug": "x",
                                       "sites": {u: {"status": "pending"} for u in urls}}
            asyncio.run(job_coro_factory())
            return self.client.get("/api/plugins/jobs/j", headers=self.h).json()

        with mock.patch.object(self.main, "delete_plugin_via_agent", fake_delete), \
             mock.patch.object(self.main, "refresh_site_in_results", lambda url: {"success": True}):
            job = run(lambda: self.main._run_plugin_delete_job("j", "akismet/akismet.php", urls))
        self.assertTrue(job["finished"])
        self.assertEqual({s["status"] for s in job["sites"].values()}, {"done"})
        self.assertEqual(sorted(deleted), [(u, "akismet/akismet.php") for u in urls])

        # Un échec de l'agent est remonté sur le site concerné, sans bloquer les autres
        def fake_refresh(url):
            return {"success": url == "https://a.fr", "error": "Agent injoignable"}

        with mock.patch.object(self.main, "refresh_site_in_results", fake_refresh):
            job = run(lambda: self.main._run_site_job("j", urls, self.main._refresh_action, "actualisation", False))
        self.assertEqual(job["sites"]["https://a.fr"]["status"], "done")
        self.assertEqual(job["sites"]["https://b.fr"]["status"], "error")

    def test_liste_installee_ne_propose_que_des_plugins_supprimables(self):
        plugin = lambda name, path: {"name": name, "path": path, "version": "1", "is_active": True}
        data = [{"client": "A", "url": "https://a.fr", "plugins_installed": [
            plugin("Akismet", "akismet/akismet.php"),
            plugin("Hello Dolly", "hello.php"),  # fichier seul : l'agent le refuserait
            plugin("Agent", "werocket-agent/werocket-agent.php"),
        ]}]
        self.assertEqual([p["plugin_path"] for p in self.main._compute_installed_plugins(data)], ["akismet/akismet.php"])

    def test_suppression_refuse_l_agent_et_un_chemin_invalide(self):
        for path in ("werocket-agent/werocket-agent.php", "../etc/passwd", "akismet.php"):
            r = self.client.post("/api/plugins/delete", json={"plugin_path": path, "urls": ["https://a.fr"]}, headers=self.h)
            self.assertEqual(r.status_code, 400, path)

    def test_refuse_site_inconnu_et_slug_inconnu(self):
        self._upload()
        r = self.client.post("/api/plugins/install", json={"slug": "mon-plugin", "urls": ["https://inconnu.fr"]}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/plugins/install", json={"slug": "absent", "urls": ["https://a.fr"]}, headers=self.h)
        self.assertEqual(r.status_code, 404)

    def test_supprimer_un_zip(self):
        self._upload()
        self.assertEqual(self.client.post("/api/plugins/zip/mon-plugin/delete", headers=self.h).status_code, 200)
        self.assertEqual(self.client.get("/api/plugins/zips", headers=self.h).json()["zips"], [])
        self.assertEqual(self.client.post("/api/plugins/zip/mon-plugin/delete", headers=self.h).status_code, 404)

    def test_refuse_sans_cle_api(self):
        self.assertEqual(self.client.get("/api/plugins/zips").status_code, 401)


if __name__ == "__main__":
    unittest.main()

"""Tests des garde-fous : python -m unittest discover -s tests (depuis backend/)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import breakdance_zip, json_store, url_guard


class UrlGuardTests(unittest.TestCase):
    def test_refuse_adresses_internes_et_schemas_non_http(self):
        for url in ("http://169.254.169.254/x", "http://localhost:8000", "http://127.0.0.1",
                    "http://10.0.0.5", "file:///etc/passwd", "https://user:pw@example.com"):
            with self.assertRaises(url_guard.UrlRejected, msg=url):
                url_guard.validate_site_url(url, require_known=False)

    def test_host_of_ignore_www_et_casse(self):
        self.assertEqual(url_guard.host_of("https://www.Coelmo.com/"), "coelmo.com")
        self.assertEqual(url_guard.host_of("coelmo.com"), "coelmo.com")


class JsonStoreTests(unittest.TestCase):
    def test_ecritures_concurrentes_sans_perte(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.json")
            json_store.write_json_atomic(path, [{"n": 0} for _ in range(4)])

            def worker(i):
                for _ in range(25):
                    with json_store.results_lock:
                        with open(path, encoding="utf-8") as f:
                            data = json.load(f)
                        data[i]["n"] += 1
                        json_store.write_json_atomic(path, data)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            with open(path, encoding="utf-8") as f:
                self.assertEqual([e["n"] for e in json.load(f)], [25] * 4)
            self.assertEqual([f for f in os.listdir(d) if f.startswith(".tmp")], [])


class MergeAuditTests(unittest.TestCase):
    def _site(self, url, **extra):
        return {"url": url, "client": url, **extra}

    def test_audit_partiel_conserve_les_autres_sites(self):
        current = [self._site("https://a.fr"), self._site("https://www.b.fr")]
        out = json_store.merge_audit_results(current, [self._site("https://b.fr", ia="neuf")], "2026-01-01", partial=True)
        self.assertEqual(sorted(e["url"] for e in out), ["https://a.fr", "https://b.fr"])
        self.assertEqual([e for e in out if e["url"] == "https://b.fr"][0]["ia"], "neuf")

    def test_audit_complet_remplace_tout(self):
        current = [self._site("https://a.fr"), self._site("https://b.fr")]
        out = json_store.merge_audit_results(current, [self._site("https://b.fr")], "2026-01-01", partial=False)
        self.assertEqual([e["url"] for e in out], ["https://b.fr"])

    def test_site_rafraichi_pendant_laudit_est_conserve(self):
        current = [self._site("https://a.fr", refreshed_at="2026-01-02", mise="recente")]
        out = json_store.merge_audit_results(current, [self._site("https://a.fr", mise="ancienne")], "2026-01-01", partial=False)
        self.assertEqual(out[0]["mise"], "recente")


class KnownHostsTests(unittest.TestCase):
    def test_refus_si_results_illisible_ou_vide(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "results.json")
            old = url_guard.RESULTS_FILE
            url_guard.RESULTS_FILE = path
            try:
                with self.assertRaises(url_guard.UrlRejected):  # absent
                    url_guard.validate_site_url("https://example.com")
                with open(path, "w") as f:
                    f.write("[]")
                with self.assertRaises(url_guard.UrlRejected):  # vide
                    url_guard.validate_site_url("https://example.com")
                with open(path, "w") as f:
                    json.dump([{"url": "https://www.example.com"}], f)
                url_guard.validate_site_url("https://example.com")  # connu, www ignoré
            finally:
                url_guard.RESULTS_FILE = old


class ZipValidationTests(unittest.TestCase):
    def _zip(self, d, files):
        path = os.path.join(d, "t.zip")
        with zipfile.ZipFile(path, "w") as z:
            for name, content in files.items():
                z.writestr(name, content)
        return path

    def test_zip_valide_lit_la_version(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._zip(d, {"breakdance/plugin.php": "<?php\n/* Version: 2.8.3 */"})
            self.assertEqual(breakdance_zip._validate_and_read_version(path), "2.8.3")

    def test_refuse_fichier_hors_dossier_et_taille_excessive(self):
        with tempfile.TemporaryDirectory() as d:
            hors = self._zip(d, {"breakdance/plugin.php": "x", "autre/x.php": "x"})
            with self.assertRaises(breakdance_zip.ZipError):
                breakdance_zip._validate_and_read_version(hors)
            ok = self._zip(d, {"breakdance/plugin.php": "<?php\n/* Version: 1.0 */" + "a" * 100})
            old = breakdance_zip.MAX_UNCOMPRESSED_BYTES
            breakdance_zip.MAX_UNCOMPRESSED_BYTES = 10
            try:
                with self.assertRaises(breakdance_zip.ZipError):
                    breakdance_zip._validate_and_read_version(ok)
            finally:
                breakdance_zip.MAX_UNCOMPRESSED_BYTES = old


if __name__ == "__main__":
    unittest.main()

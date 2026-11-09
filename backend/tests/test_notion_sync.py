"""Tests de la synchro Notion → PocketBase (services/notion_sync.py), sans réseau.

python -m unittest discover -s tests   (depuis backend/)
"""
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKEND_API_KEY", "test-key")
try:
    import pocketbase  # noqa: F401
except ImportError:  # pas installé en local : le client est de toute façon remplacé par un faux
    sys.modules["pocketbase"] = types.SimpleNamespace(PocketBase=object)

from services import notion_sync, pocketbase_client as pbc


class FakeCollection:
    def __init__(self, records):
        self.records = records  # {id: SimpleNamespace}
        self.created, self.updated = [], []

    def get_full_list(self, query_params=None):
        return list(self.records.values())

    def get_first_list_item(self, flt):
        for r in self.records.values():
            if r.url == flt:
                return r
        raise KeyError(flt)

    def create(self, data):
        rec = SimpleNamespace(**data)
        self.records[data["id"]] = rec
        self.created.append(data)
        return rec

    def update(self, rid, data):
        self.records[rid].__dict__.update(data)
        self.updated.append((rid, data))
        return self.records[rid]


class FakePB:
    def __init__(self, projects):
        self.projects = FakeCollection({p.id: p for p in projects})

    def collection(self, name):
        assert name == "projects"
        return self.projects

    def filter(self, _template, params):  # le faux get_first_list_item compare l'URL brute
        return params["url"]


def project(pid, name, url, date_str=None, active=True):
    return SimpleNamespace(id=pid, client_name=name, url=url, date_mise_en_ligne=date_str, is_active=active)


def notion(name, url, d="2026-01-10"):
    return {"client_name": name, "url": url, "date_mise_en_ligne": d, "notion_page_id": "page-" + name}


class SyncTests(unittest.TestCase):
    def run_sync(self, projects, notion_sites):
        pb = FakePB(projects)
        with mock.patch.object(notion_sync, "is_configured", return_value=True), \
             mock.patch.object(notion_sync, "fetch_notion_sites", return_value=notion_sites), \
             mock.patch.object(pbc, "get_client", return_value=pb):
            return notion_sync.sync(), pb.projects

    def test_nouveau_site_cree_actif_et_renvoye_pour_audit(self):
        result, col = self.run_sync([], [notion("Dupont", "https://dupont.fr")])
        self.assertEqual(len(col.created), 1)
        self.assertTrue(col.created[0]["is_active"])
        self.assertEqual(col.created[0]["notion_page_id"], "page-Dupont")
        c = result["created"][0]
        self.assertEqual((c["project_name"], c["site_url"]), ("Dupont", "https://dupont.fr"))
        self.assertEqual(c["project_id"], col.created[0]["id"])  # sans project_id l'audit n'écrit pas dans PocketBase

    def test_site_deja_connu_inchange_rien_ecrit(self):
        existing = project("a" * 15, "Dupont", "https://dupont.fr", "2026-01-10 00:00:00.000Z")
        result, col = self.run_sync([existing], [notion("Dupont", "https://dupont.fr")])
        self.assertEqual(result["created"], [])
        self.assertEqual(result["updated"], 0)
        self.assertEqual((col.created, col.updated), ([], []))

    def test_site_connu_nom_modifie_est_mis_a_jour_sans_audit(self):
        existing = project("a" * 15, "Ancien nom", "https://dupont.fr", "2026-01-10 00:00:00.000Z")
        result, col = self.run_sync([existing], [notion("Nouveau nom", "https://dupont.fr")])
        self.assertEqual(result["created"], [])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(col.records["a" * 15].client_name, "Nouveau nom")

    def test_site_desactive_a_la_main_nest_pas_reactive(self):
        existing = project("a" * 15, "Ancien", "https://dupont.fr", "2026-01-10 00:00:00.000Z", active=False)
        self.run_sync([existing], [notion("Nouveau", "https://dupont.fr")])
        self.assertFalse(col_active(existing))

    def test_site_absent_de_notion_nest_pas_desactive(self):
        existing = project("a" * 15, "Dupont", "https://dupont.fr")
        result, col = self.run_sync([existing], [])
        self.assertTrue(existing.is_active)
        self.assertEqual((result["created"], result["updated"], col.updated), ([], 0, []))

    def test_doublon_url_notion_garde_la_mise_en_ligne_la_plus_recente(self):
        sites = [notion("Recent", "https://dupont.fr", "2026-05-01"), notion("Vieux", "https://dupont.fr", "2025-01-01")]
        result, col = self.run_sync([], sites)
        self.assertEqual(len(col.created), 1)
        self.assertEqual(col.created[0]["client_name"], "Recent")
        self.assertEqual(result["notion_total"], 1)

    def test_deuxieme_passage_ne_cree_plus_rien(self):
        pb = FakePB([])
        with mock.patch.object(notion_sync, "is_configured", return_value=True), \
             mock.patch.object(notion_sync, "fetch_notion_sites", return_value=[notion("Dupont", "https://dupont.fr")]), \
             mock.patch.object(pbc, "get_client", return_value=pb):
            first = notion_sync.sync()
            second = notion_sync.sync()
        self.assertEqual(len(first["created"]), 1)
        self.assertEqual((second["created"], second["updated"]), ([], 0))

    def test_sans_token_notion_erreur_claire(self):
        with mock.patch.object(notion_sync, "is_configured", return_value=False):
            with self.assertRaises(RuntimeError):
                notion_sync.sync()


def col_active(rec):
    return rec.is_active


class NormalizeUrlTests(unittest.TestCase):
    def test_normalisation(self):
        n = notion_sync.normalize_url
        self.assertEqual(n("Dupont.FR/"), "https://dupont.fr")
        self.assertEqual(n(" http://a.fr/x/ autre"), "http://a.fr/x")
        self.assertIsNone(n(""))
        self.assertIsNone(n(None))
        self.assertIsNone(n("pasdepoint"))


class FetchTests(unittest.TestCase):
    def page(self, title, url, start):
        return {"id": "id-" + title, "properties": {
            "Client": {"title": [{"text": {"content": title}}]},
            "Lien site": {"url": url},
            "Mise en ligne": {"date": {"start": start} if start else None},
        }}

    def test_pagination_et_pages_sans_url_ignorees(self):
        pages = [
            {"results": [self.page("A", "a.fr", "2026-01-01T10:00:00Z"), self.page("B", None, None)],
             "has_more": True, "next_cursor": "c2"},
            {"results": [self.page("C", "https://c.fr", None)], "has_more": False, "next_cursor": None},
        ]
        responses = [mock.Mock(status_code=200, json=mock.Mock(return_value=p)) for p in pages]
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t", "DATABASE_ID": "d"}), \
             mock.patch.object(notion_sync.requests, "post", side_effect=responses) as post:
            sites = notion_sync.fetch_notion_sites()
        self.assertEqual([s["client_name"] for s in sites], ["A", "C"])
        self.assertEqual(sites[0]["url"], "https://a.fr")
        self.assertEqual(sites[0]["date_mise_en_ligne"], "2026-01-01")
        self.assertIsNone(sites[1]["date_mise_en_ligne"])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["start_cursor"], "c2")

    def test_erreur_api_notion_leve_une_exception(self):
        resp = mock.Mock(status_code=401, text="unauthorized")
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t", "DATABASE_ID": "d"}), \
             mock.patch.object(notion_sync.requests, "post", return_value=resp):
            with self.assertRaises(RuntimeError):
                notion_sync.fetch_notion_sites()


if __name__ == "__main__":
    unittest.main()

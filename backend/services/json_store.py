import json
import os
import tempfile
import threading

from .url_guard import host_of

# Un seul processus uvicorn : un verrou threading suffit à sérialiser les
# lecture-modification-écriture de results.json (job Breakdance à 2 workers,
# rafraîchissements après mise à jour, fin d'audit).
results_lock = threading.RLock()


def write_json_atomic(path: str, data) -> None:
    """Écrit dans un fichier temporaire du même dossier puis os.replace : un lecteur
    voit l'ancien ou le nouveau contenu, jamais un fichier tronqué."""
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def merge_audit_results(current: list, new: list, audit_started_at: str, partial: bool) -> list:
    """Résultats à écrire en fin d'audit.

    - Un site rafraîchi (`refreshed_at`) après le début de l'audit est plus récent que ce
      que l'audit a mesuré : on garde l'entrée actuelle.
    - Audit partiel (une URL ou `limit`) : les autres sites sont conservés. Un audit complet
      remplace tout, pour que les sites supprimés de la base disparaissent.
    Les sites sont rapprochés par hôte (sans « www. »)."""
    by_host = {host_of(e.get("url", "")): e for e in current if isinstance(e, dict)}
    merged = [
        by_host[host_of(r["url"])]
        if (by_host.get(host_of(r["url"])) or {}).get("refreshed_at", "") > audit_started_at
        else r
        for r in new
    ]
    if partial:
        audited = {host_of(r["url"]) for r in merged}
        merged = [e for h, e in by_host.items() if h not in audited] + merged
    return merged

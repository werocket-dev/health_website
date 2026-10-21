import json
import os
import tempfile
import threading

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

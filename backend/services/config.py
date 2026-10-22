import os

# backend/ (un niveau au-dessus de services/), pour rester au même endroit
# qu'avant le découpage en modules et ne pas perdre le fichier existant.
_BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))

# Isolés dans un sous-dossier dédié pour ne monter qu'un seul volume Docker
# dessus (voir backend/Dockerfile) — sans ça, un redéploiement recrée le
# conteneur depuis l'image et efface ces fichiers à chaque fois.
DATA_DIR = os.path.join(_BACKEND_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
LAST_AUDIT_FILE = os.path.join(DATA_DIR, "last_audit.json")

# Onglet Test : audits courts sur un échantillon, dans leurs propres fichiers pour ne jamais
# toucher aux résultats du parc.
TEST_RESULTS_FILE = os.path.join(DATA_DIR, "test_results.json")
TEST_LAST_AUDIT_FILE = os.path.join(DATA_DIR, "test_last_audit.json")


def scope_files(scope: str) -> tuple[str, str]:
    """(fichier de résultats, fichier du dernier audit) du parc ou de l'onglet Test."""
    if scope == "parc":
        return RESULTS_FILE, LAST_AUDIT_FILE
    if scope == "test":
        return TEST_RESULTS_FILE, TEST_LAST_AUDIT_FILE
    raise ValueError(f"scope invalide : {scope!r} (parc ou test)")

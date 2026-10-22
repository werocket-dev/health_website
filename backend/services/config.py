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

import os

# backend/ (un niveau au-dessus de services/), pour rester au même endroit
# qu'avant le découpage en modules et ne pas perdre le fichier existant.
_BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
RESULTS_FILE = os.path.join(_BACKEND_DIR, "results.json")
LAST_AUDIT_FILE = os.path.join(_BACKEND_DIR, "last_audit.json")

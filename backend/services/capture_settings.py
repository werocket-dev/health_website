"""
Réglages de capture d'écran — UNE source pour l'audit (audit_engine.py) ET la
génération du jeu d'entraînement (scripts_entrainement/3_generate_dataset.py).

Pourquoi : le modèle doit apprendre sur des captures prises comme en production.
Si l'audit change un réglage (User-Agent, taille d'écran, délai d'attente) sans que
l'entraînement le suive, le modèle voit en production des images qu'il n'a jamais
vues. En important les mêmes constantes des deux côtés, ça ne peut plus arriver.
"""
import random

DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# Le mobile est un contexte séparé, taille fixe (comme un iPhone).
MOBILE_VIEWPORT = {"width": 390, "height": 844}

# Le bureau : taille tirée au hasard pour chaque site (écrans variés).
DESKTOP_WIDTH_RANGE = (1280, 1920)
DESKTOP_HEIGHT_RANGE = (720, 1080)

# Délais : laisser le JavaScript (Divi, Elementor, animations) se terminer.
HOME_TIMEOUT_MS = 25000
CONTACT_TIMEOUT_MS = 15000
WAIT_AFTER_LOAD_MS = 4000
CONTACT_WAIT_AFTER_LOAD_MS = 2000

CONTACT_SLUGS = ["/contact", "/contactez-nous"]


def random_desktop_viewport(rng=random) -> dict:
    return {
        "width": rng.randint(*DESKTOP_WIDTH_RANGE),
        "height": rng.randint(*DESKTOP_HEIGHT_RANGE),
    }

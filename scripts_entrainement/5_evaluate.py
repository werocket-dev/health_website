"""
5_evaluate.py — MESURER un modèle sur des sites "difficiles".

Pourquoi ce script ?
  La précision de 97,8 % affichée à l'entraînement se mesure sur des images
  FABRIQUÉES par nous (un site avec ou sans CSS). Ce n'est pas ce qui nous
  intéresse : on veut savoir si le modèle se trompe sur de VRAIS sites
  (fond sombre, bannière cookies, animation, mobile...). Ce script le mesure.

Le jeu de test (eval_hard_set/) contient des captures de vrais sites relues à
l'œil par un humain :
    healthy/  -> le site fonctionne (même s'il est sombre, avec un pop-up...)
    broken/   -> vraie page d'erreur (403 Forbidden, erreur critique WordPress...)

Règle d'or : ces images ne doivent JAMAIS servir à l'entraînement, sinon le
modèle les connaîtrait par cœur et la mesure ne voudrait plus rien dire.

Usage (depuis scripts_entrainement/) :
    ../backend/.venv/bin/python 5_evaluate.py ../backend/werocket_vision_model.h5
"""
import os
import sys
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import numpy as np
import tensorflow as tf
import keras
import preprocess

EVAL_DIR = "eval_hard_set"
IMG_SIZE = (224, 224)

# Mêmes seuils que backend/tf_infer.py (c'est ce que voit l'utilisateur).
def status_from_score(score: float) -> str:
    if score > 0.7:
        return "OK"
    if score > 0.5:
        return "ATTENTION"
    return "ALERTE"


# ── Deux façons de préparer l'image ─────────────────────────────────────────
# Le modèle attend 224x224 pixels. Une capture fait ~1440x900 : il faut la
# RÉDUIRE, et il existe plusieurs façons de le faire, qui donnent des images
# différentes pour le modèle.
#
#  "prod"  : ce que fait l'audit aujourd'hui (backend/tf_infer.py) :
#            keras.utils.load_img(..., target_size=(224,224)) -> interpolation
#            "nearest" par défaut (on prend le pixel le plus proche, sans lissage).
#  "train" : ce qu'a vu le modèle pendant l'entraînement (4_train_ai.py) :
#            tf.image.resize(...) -> interpolation bilinéaire.
#
# Si les deux ne donnent pas le même résultat, le modèle "voit" en production
# des images qui ne ressemblent pas à celles de son entraînement.
def load_prod(path: str) -> np.ndarray:
    img = keras.utils.load_img(path, target_size=IMG_SIZE)
    return keras.utils.img_to_array(img) / 255.0


def load_train(path: str) -> np.ndarray:
    # L'ANCIENNE préparation d'entraînement (bilinéaire sans antialias).
    # Gardée uniquement pour mesurer les anciens modèles.
    raw = tf.io.read_file(path)
    img = tf.image.decode_png(raw, channels=3)
    img = tf.image.resize(img, IMG_SIZE)
    return img.numpy() / 255.0


def load_shared(path: str) -> np.ndarray:
    # La NOUVELLE préparation, partagée avec 4_train_ai.py (preprocess.py).
    return preprocess.load_image(path).numpy() / 255.0


def collect_images():
    items = []
    for label in ("healthy", "broken"):
        folder = os.path.join(EVAL_DIR, label)
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".png"):
                items.append((label, name, os.path.join(folder, name)))
    return items


def predict_all(model, items, loader):
    batch = np.stack([loader(path) for _, _, path in items])
    # Le modèle sort un score entre 0 et 1 : proche de 1 = "site sain".
    # (Les dossiers sont triés par ordre alphabétique à l'entraînement :
    # broken = classe 0, healthy = classe 1.)
    return model.predict(batch, verbose=0)[:, 0]


def summarize(title, items, scores):
    healthy = [s for (label, _, _), s in zip(items, scores) if label == "healthy"]
    broken = [s for (label, _, _), s in zip(items, scores) if label == "broken"]
    h_status = [status_from_score(s) for s in healthy]
    b_status = [status_from_score(s) for s in broken]

    print(f"\n=== {title} ===")
    print(f"Sites SAINS ({len(healthy)}) : "
          f"OK {h_status.count('OK')} | ATTENTION {h_status.count('ATTENTION')} | "
          f"ALERTE {h_status.count('ALERTE')}   <- ALERTE ici = FAUSSE ALARME")
    print(f"Pages CASSÉES ({len(broken)}) : "
          f"OK {b_status.count('OK')} | ATTENTION {b_status.count('ATTENTION')} | "
          f"ALERTE {b_status.count('ALERTE')}   <- OK ici = PANNE NON DÉTECTÉE")
    fausses_alarmes = h_status.count("ALERTE") + h_status.count("ATTENTION")
    print(f"=> {fausses_alarmes}/{len(healthy)} sites sains signalés à tort "
          f"({100 * fausses_alarmes / max(1, len(healthy)):.0f} %)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    model_path = sys.argv[1]
    model = keras.models.load_model(model_path, compile=False)
    items = collect_images()
    print(f"Modèle : {model_path}")
    print(f"Images : {sum(1 for i in items if i[0] == 'healthy')} saines, "
          f"{sum(1 for i in items if i[0] == 'broken')} cassées")

    loaders = {
        "prod": ("audit actuel : nearest", load_prod),
        "ancien_train": ("ancien entraînement : bilinéaire", load_train),
        "partagé": ("NOUVEAU preprocess.py : antialias", load_shared),
    }
    all_scores = {}
    for key, (desc, loader) in loaders.items():
        all_scores[key] = predict_all(model, items, loader)
        summarize(f"Préparation '{key}' ({desc})", items, all_scores[key])
    scores_prod = all_scores["prod"]

    print("\n=== Détail image par image (score 0-100, 100 = sain) ===")
    print(f"{'vérité':8} {'prod':>5} {'ancien':>6} {'nouv.':>5}  image")
    for i, (label, name, _) in enumerate(items):
        sp, st, sn = (all_scores[k][i] for k in ("prod", "ancien_train", "partagé"))
        wrong = (label == "healthy" and sn <= 0.7) or (label == "broken" and sn > 0.5)
        flag = "  <-- erreur (préparation nouvelle)" if wrong else ""
        print(f"{label:8} {sp * 100:5.0f} {st * 100:6.0f} {sn * 100:5.0f}  {name}{flag}")


if __name__ == "__main__":
    main()

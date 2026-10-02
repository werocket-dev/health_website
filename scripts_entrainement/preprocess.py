"""
preprocess.py — LA façon de préparer une image pour le modèle. Une seule.

Le problème qu'on corrige :
  Jusqu'ici, l'entraînement (4_train_ai.py) et l'audit (backend/tf_infer.py)
  réduisaient chacun les captures à 224x224 avec leur propre méthode :
      entraînement -> tf.image.resize (bilinéaire)
      audit        -> keras.utils.load_img (interpolation "nearest")
  Le modèle apprenait sur des images et en voyait d'autres en production.

La règle maintenant :
  TOUT ce qui montre une image au modèle (entraînement, mesure, audit) passe par
  load_image() ci-dessous. Si on veut changer la méthode, on la change ICI, à un
  seul endroit, et on réentraîne le modèle : un modèle et sa préparation d'image
  forment un tout, on ne peut pas changer l'un sans l'autre.

Pourquoi "antialias=True" ?
  Réduire une capture de 1440 px à 224 px, c'est jeter ~97 % des pixels. Sans
  antialias, on prend quelques pixels isolés et on ignore les autres : les
  lettres fines et les traits deviennent du bruit ou disparaissent. Avec
  antialias, chaque pixel de l'image réduite est la moyenne de la zone qu'il
  remplace : le texte et les formes restent lisibles pour le modèle.
"""
import tensorflow as tf

IMG_SIZE = (224, 224)  # taille attendue par MobileNetV2


def load_image(filepath):
    """Lit une capture PNG et la prépare pour le modèle.

    N'utilise que des opérations TensorFlow : la même fonction marche donc
    telle quelle dans le pipeline d'entraînement (tf.data) et pour une image
    isolée (appelée avec une simple chaîne).

    Retourne un tableau float32 de forme (224, 224, 3), valeurs de 0 à 255.
    La division par 255 est faite à part (couche Rescaling à l'entraînement,
    `/ 255.0` à l'inférence).
    """
    raw = tf.io.read_file(filepath)
    image = tf.image.decode_png(raw, channels=3)  # 3 canaux : rouge, vert, bleu
    return tf.image.resize(image, IMG_SIZE, method="bilinear", antialias=True)

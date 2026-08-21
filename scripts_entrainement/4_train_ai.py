import os
import json
import random
import numpy as np
import matplotlib.pyplot as plt
import tensorflow as tf
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.layers import Dense, GlobalAveragePooling2D, Dropout, RandomZoom, RandomContrast
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay

print("🚀 Script lancé, TensorFlow est en cours de chargement...")

# --- CONFIGURATION ---
DATASET_DIR = "dataset"
IMG_SIZE = (224, 224) # Taille standard pour MobileNet
BATCH_SIZE = 8
EPOCHS = 50 # Plafond max, l'EarlyStopping arrêtera avant si besoin

print("📂 Chargement des images...")

# 1. PRÉPARATION DES DONNÉES — split PAR SITE (pas par image)
# But : éviter qu'une page d'un même site se retrouve à la fois en train et en
# validation (ex: home_healthy en train / contact_broken en validation), ce qui
# fausserait l'évaluation en laissant le modèle "reconnaître" le style du site.
class_names = sorted([d for d in os.listdir(DATASET_DIR) if os.path.isdir(os.path.join(DATASET_DIR, d))])

SITE_ID_SUFFIXES = ["_contact_desktop", "_contact_mobile", "_desktop", "_mobile"]

def get_site_id(filename):
    """'ClientX_desktop.png', 'ClientX_mobile.png', 'ClientX_contact_desktop.png' et
    'ClientX_contact_mobile.png' doivent tous partager le même site_id ('ClientX'),
    pour que toutes les pages/devices d'un même site restent dans le même split."""
    name = filename.rsplit(".", 1)[0]
    for suffix in SITE_ID_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name

site_ids = set()
for class_name in class_names:
    for f in os.listdir(os.path.join(DATASET_DIR, class_name)):
        site_ids.add(get_site_id(f))

site_ids = sorted(site_ids)
random.Random(123).shuffle(site_ids)
split_idx = int(len(site_ids) * 0.8)
train_sites = set(site_ids[:split_idx])
val_sites = set(site_ids[split_idx:])

def collect_files(sites_set):
    filepaths, labels = [], []
    for label_idx, class_name in enumerate(class_names):
        class_dir = os.path.join(DATASET_DIR, class_name)
        for f in os.listdir(class_dir):
            if get_site_id(f) in sites_set:
                filepaths.append(os.path.join(class_dir, f))
                labels.append(label_idx)
    return filepaths, labels

train_files, train_labels = collect_files(train_sites)
val_files, val_labels = collect_files(val_sites)

print(f"📊 Split par site : {len(train_sites)} sites en train, {len(val_sites)} sites en validation")
print(f"📊 Images : {len(train_files)} en train, {len(val_files)} en validation")

def load_image(filepath, label):
    image = tf.io.read_file(filepath)
    image = tf.image.decode_png(image, channels=3)
    image = tf.image.resize(image, IMG_SIZE)
    return image, label

train_labels_arr = np.array(train_labels, dtype=np.float32).reshape(-1, 1)
val_labels_arr = np.array(val_labels, dtype=np.float32).reshape(-1, 1)

train_ds = tf.data.Dataset.from_tensor_slices((train_files, train_labels_arr))
train_ds = train_ds.shuffle(buffer_size=len(train_files), seed=123)
train_ds = train_ds.map(load_image, num_parallel_calls=tf.data.AUTOTUNE)
train_ds = train_ds.batch(BATCH_SIZE)

validation_ds = tf.data.Dataset.from_tensor_slices((val_files, val_labels_arr))
validation_ds = validation_ds.map(load_image, num_parallel_calls=tf.data.AUTOTUNE)
validation_ds = validation_ds.batch(BATCH_SIZE)

# Normalisation et Data Augmentation
# Note : pas de flip/rotation — un screenshot de site web n'est jamais retourné
# ni penché en conditions réelles, contrairement à une photo classique. On garde
# uniquement des variations réalistes (zoom léger, contraste).
normalization_layer = tf.keras.layers.Rescaling(1./255)
data_augmentation = tf.keras.Sequential([
    RandomZoom(0.1),
    RandomContrast(0.1),
])

# Application de la normalisation et augmentation
train_ds = train_ds.map(lambda x, y: (data_augmentation(normalization_layer(x), training=True), y))
validation_ds = validation_ds.map(lambda x, y: (normalization_layer(x), y))

# Optimisation des performances
AUTOTUNE = tf.data.AUTOTUNE
train_ds = train_ds.cache().prefetch(buffer_size=AUTOTUNE)
validation_ds = validation_ds.cache().prefetch(buffer_size=AUTOTUNE)

# 2. CRÉATION DU MODÈLE (Transfer Learning)
print("🧠 Téléchargement du cerveau MobileNetV2...")
# On récupère le modèle pré-entraîné (sans la dernière couche "top")
base_model = MobileNetV2(weights='imagenet', include_top=False, input_shape=(224, 224, 3))

# On gèle le modèle de base (on ne veut pas casser ce qu'il sait déjà)
base_model.trainable = False

# On ajoute NOTRE couche de décision à la fin
x = base_model.output
x = GlobalAveragePooling2D()(x)
x = Dense(128, activation='relu')(x) # Une couche de "réflexion"
x = Dropout(0.5)(x) # Pour éviter le par cœur (Overfitting)
predictions = Dense(1, activation='sigmoid')(x) # La décision finale (0 ou 1)

model = Model(inputs=base_model.input, outputs=predictions)

# Compilation
model.compile(optimizer=Adam(learning_rate=0.0001),
              loss='binary_crossentropy',
              metrics=['accuracy'])

# 3. ENTRAÎNEMENT
early_stop = EarlyStopping(monitor='val_loss', patience=8, restore_best_weights=True)
checkpoint = ModelCheckpoint('werocket_vision_model_best.h5', monitor='val_loss', save_best_only=True)

print("🚀 Démarrage de l'entraînement...")
history = model.fit(
    train_ds,
    epochs=EPOCHS,
    validation_data=validation_ds,
    callbacks=[early_stop, checkpoint]
)

# 4. SAUVEGARDE
print("💾 Sauvegarde du modèle...")
model.save("werocket_vision_model.h5")
print("✅ Modèle sauvegardé sous 'werocket_vision_model.h5' !")


acc = history.history['accuracy']
val_acc = history.history['val_accuracy']
plt.plot(acc, label='Entraînement')
plt.plot(val_acc, label='Validation')
plt.title('Précision de l\'IA')
plt.legend()
plt.savefig('training_graph.png')
print("📊 Graphique généré : training_graph.png")

# 5. HISTORIQUE D'ENTRAÎNEMENT (nombre réel d'epochs + courbes)
epochs_effectues = len(history.history['loss'])
history_data = {
    "epochs_max": EPOCHS,
    "epochs_effectues": epochs_effectues,
    "history": history.history
}
with open("training_history.json", "w") as f:
    json.dump(history_data, f, indent=2)
print(f"📝 Historique sauvegardé sous 'training_history.json' ({epochs_effectues} epochs effectués sur {EPOCHS} max)")

# 6. MATRICE DE CONFUSION (sur les données de validation)
print("📐 Calcul de la matrice de confusion...")
y_true = np.concatenate([y.numpy() for _, y in validation_ds], axis=0).flatten().astype(int)
y_pred_proba = model.predict(validation_ds)
y_pred = (y_pred_proba > 0.5).astype(int).flatten()

cm = confusion_matrix(y_true, y_pred)
disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=class_names)
plt.figure()
disp.plot(cmap="Blues")
plt.title("Matrice de confusion - Validation")
plt.savefig("confusion_matrix.png")
print("✅ Matrice de confusion sauvegardée sous 'confusion_matrix.png'")
print(cm)


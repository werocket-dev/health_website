import os
import numpy as np
import matplotlib.pyplot as plt
import tensorflow as tf
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.layers import Dense, GlobalAveragePooling2D, Dropout, RandomFlip, RandomRotation, RandomZoom
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam

print("🚀 Script lancé, TensorFlow est en cours de chargement...")

# --- CONFIGURATION ---
DATASET_DIR = "dataset"
IMG_SIZE = (224, 224) # Taille standard pour MobileNet
BATCH_SIZE = 8
EPOCHS = 5 # Nombre de fois où l'IA va revoir toutes les images

print("📂 Chargement des images...")

# 1. PRÉPARATION DES DONNÉES (Nouvelle API TensorFlow)
# Chargement des données avec validation split
train_ds = tf.keras.utils.image_dataset_from_directory(
    DATASET_DIR,
    validation_split=0.2,
    subset="training",
    seed=123,
    image_size=IMG_SIZE,
    batch_size=BATCH_SIZE,
    label_mode='binary'  # 0 ou 1 (Sain ou Cassé)
)

validation_ds = tf.keras.utils.image_dataset_from_directory(
    DATASET_DIR,
    validation_split=0.2,
    subset="validation",
    seed=123,
    image_size=IMG_SIZE,
    batch_size=BATCH_SIZE,
    label_mode='binary'
)

# Normalisation et Data Augmentation
normalization_layer = tf.keras.layers.Rescaling(1./255)
data_augmentation = tf.keras.Sequential([
    RandomFlip("horizontal"),
    RandomRotation(0.1),
    RandomZoom(0.2)
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
print("🚀 Démarrage de l'entraînement...")
history = model.fit(
    train_ds,
    epochs=EPOCHS,
    validation_data=validation_ds
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


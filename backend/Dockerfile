# Dockerfile backend — API FastAPI + moteur d'audit (Playwright + TensorFlow/Keras)
FROM python:3.11-slim

WORKDIR /app

# curl : nécessaire pour le HEALTHCHECK défini dans docker-compose.yml
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installe Chromium + toutes les dépendances système requises par Playwright
RUN playwright install --with-deps chromium

COPY main.py audit_engine.py pb_worker.py tf_infer.py tf_worker.py werocket_vision_model.h5 ./
COPY services/ ./services/

# screenshots_audit/ n'est PAS copié dans l'image : à monter en volume côté
# Dokploy (données de sortie, pas de code). Le modèle IA (.h5), lui, est
# intégré à l'image ci-dessus — Dokploy n'a pas de moyen fiable d'uploader
# un binaire de cette taille (~11 Mo) autrement ; un commit+push suffit à
# le remplacer au prochain réentraînement (MODEL_PATH reste surchargeable
# via une variable d'env si un Bind Mount redevient possible un jour).

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

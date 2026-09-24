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

COPY main.py audit_engine.py pb_worker.py tf_infer.py tf_worker.py ./
COPY services/ ./services/

# werocket_vision_model.h5 et screenshots_audit/ ne sont PAS copiés dans l'image :
# à monter en volumes côté Dokploy (modèle remplaçable sans rebuild,
# screenshots = données de sortie).

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

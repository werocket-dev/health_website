# 🗂️ WeRocket — Roadmap SaaS

---

## 1. 📁 Préparation de la structure *(Immédiat)*

> Ne code pas tout de suite, organise tes dossiers. Crée un nouveau dossier parent `werocket-saas`.

- [x] Créer le dossier `werocket-saas/`
- [x] Créer le sous-dossier `backend/` *(C'est là qu'ira ton code Python)*
- [x] Créer le sous-dossier `frontend/` *(C'est là qu'ira ton React)*
- [x] Créer le fichier `docker-compose.yml` à la racine *(vide pour l'instant)*

---

## 2. 🐍 Le Backend *(Ton moteur Python)*

> L'objectif est de transformer ton script qui "fait tout d'un coup" en une API qui "répond aux ordres".

- [x] **Initialiser le projet** : dans `backend/`, crée un environnement virtuel et un fichier `requirements.txt`
- [x] **Installer FastAPI** : `pip install fastapi uvicorn`
- [x] **Migrer ton code d'audit** :
  - [x] Copie `6_run_global_audit_test.py` dans `backend/`
  - [x] Renomme-le en `audit_engine.py` et transforme-le en module *(enlève le `if __name__ == "__main__":` à la fin)*
  - [x] Fais en sorte que `run_audit()` accepte des paramètres *(ex: une liste d'URL)* au lieu de lire toute la BDD
- [x] **Créer l'API** (`main.py`) :
  - [x] Route `POST /api/scan` : reçoit une URL, lance l'audit en arrière-plan (`BackgroundTasks`), renvoie `"Scan démarré"`
  - [x] Route `GET /api/results` : lit PostgreSQL et renvoie le JSON des résultats
- [x] **Gérer le CORS** : ajouter `CORSMiddleware` dans FastAPI pour autoriser le frontend React

---

## 3. ⚛️ Le Frontend *(Ton tableau de bord)*

- [x] **Initialiser Next.js** : dans le dossier parent, lancer :
  ```bash
  npx create-next-app@latest frontend --typescript --tailwind --app --no-src-dir
  ```
- [x] **Installer les outils** :
  - [x] `npm install axios` *(appels API Python)*
  - [x] `npm install @tanstack/react-query` *(gestion chargement/refresh auto — une pépite)*
- [x] **Créer la page d'accueil** :
  - [x] Dans `app/page.tsx`, créer un bouton "Lancer l'Audit Global"
  - [x] Ajouter un tableau vide en dessous
- [x] **Brancher les fils** :
  - [x] Clic bouton → `axios.post('http://localhost:8000/api/scan')`
  - [x] Tableau → `axios.get('http://localhost:8000/api/results')` toutes les 2s
  - [x] Configurer les variables d'environnement dans `.env.local` pour l'URL de l'API

---

## 4. 🐳 La Dockerisation *(Pour OVH)*

> C'est l'étape qui rendra ton déploiement "magique" et sans douleur pour l'admin réseau.

- [x] **Backend Dockerfile** : créer `backend/Dockerfile`
  > ⚠️ **Point CRITIQUE** : utiliser une image qui contient Python ET Playwright
  > `mcr.microsoft.com/playwright/python:v1.40.0-jammy`
- [x] **Frontend Dockerfile** : créer `frontend/Dockerfile`
  - Image `node` pour construire (`npm run build`)
  - Image `nginx` pour servir les fichiers statiques
- [ ] **Docker Compose** : configurer `docker-compose.yml` pour lancer :
  - [ ] Service `db` *(PostgreSQL)*
  - [ ] Service `backend` *(Python)*
  - [ ] Service `frontend` *(React)*

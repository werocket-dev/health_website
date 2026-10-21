# WeRocket Agent — guide de configuration

Le backend audite les sites WordPress de deux façons :

1. **Agent WordPress** (méthode fiable) : le plugin `werocket-agent` installé sur le site expose des routes REST signées (`/wp-json/werocket/v1/...`).
2. **Scraping HTML** (repli) : analyse du code source quand l'agent est absent ou injoignable. Pas de version PHP/MySQL, pas de statut actif/inactif des plugins.

Les actions à distance (mise à jour de plugin ou du cœur, suppression de thème, activation de licence, installation d'un zip) ne passent **que** par l'agent.

---

## Sécurité de l'agent

Chaque requête vers une route de l'agent doit réussir ces contrôles, dans l'ordre :

1. **Liste d'IP autorisées** (`WEROCKET_ALLOWED_IPS`) — l'IP réelle est `REMOTE_ADDR` ; les en-têtes `X-Forwarded-For`/`CF-Connecting-IP` ne sont crus que si la requête vient d'un proxy déclaré dans `WEROCKET_TRUSTED_PROXIES`.
2. **Limitation de débit** par IP.
3. **En-tête secret** `X-WeRocket-Key` (= `WEROCKET_AGENT_HEADER_KEY`).
4. **Horodatage** frais (±5 minutes).
5. **Signature Ed25519** : le backend signe `URL|timestamp|route` avec sa clé privée ; l'agent vérifie avec la clé publique (`WEROCKET_PUBLIC_KEY_HEX`). La clé privée ne quitte jamais le backend. `install-plugin-zip` et `delete-plugin` signent en plus leurs paramètres.
6. **POST uniquement**.

> Limite connue : `update-plugin`, `delete-theme`, `activate-license` et `update-core` ne signent pas leurs paramètres (`plugin_slug`, `theme_slug`, `license_key`…). Une requête interceptée pourrait être rejouée dans la fenêtre de 5 minutes avec d'autres paramètres. HTTPS limite le risque.

Routes de l'agent : `/status`, `/update-plugin`, `/update-core`, `/set-allowed-ips`, `/delete-theme`, `/activate-license`, `/install-plugin-zip`, `/delete-plugin`.

---

## Configuration

### Backend (`backend/.env`, voir `backend/.env.example`)

```bash
# Générer la paire de clés Ed25519 : la clé privée va ici, la publique dans wp-config.php des sites.
WEROCKET_PRIVATE_KEY_HEX=<seed 32 octets en hexadécimal, 64 caractères>
WEROCKET_AGENT_HEADER_KEY=<openssl rand -hex 32>
```

Sans `WEROCKET_PRIVATE_KEY_HEX`, le backend n'a pas d'agent (`AGENT_AVAILABLE = False`) et bascule en scraping HTML.

### Site WordPress (`wp-config.php`, avant `/* That's all, stop editing! */`)

```php
define( 'WEROCKET_PUBLIC_KEY_HEX', '<clé publique Ed25519 en hexadécimal>' );
define( 'WEROCKET_AGENT_HEADER_KEY', '<la même valeur que dans backend/.env>' );
define( 'WEROCKET_ALLOWED_IPS', '<IP du serveur backend>' );
// Facultatifs :
// define( 'WEROCKET_TRUSTED_PROXIES', '...' );
// define( 'WEROCKET_ZIP_ALLOWED_HOSTS', 'backend.exemple.com' ); // durcissement de /install-plugin-zip
```

Installer le plugin (dossier `werocket-agent/`, zip via *Extensions → Ajouter → Téléverser*, ou FTP dans `wp-content/plugins/`) puis l'activer. Le plugin se met à jour tout seul (plugin-update-checker, dépôt GitHub).

### Vérifier la connexion

Le plus simple est de lancer un audit sur un site depuis l'interface : la méthode « Agent » doit apparaître dans les résultats. Un appel à la main exige une signature Ed25519 valide, il n'y a pas de `curl` possible sans elle.

---

## Utilisation

- **Audit** : depuis l'interface (`POST /api/scan`), tous les sites actifs de PocketBase ou une seule URL. Un seul audit à la fois.
- **Résultats** : `backend/data/results.json` (écriture atomique et verrouillée), relu par le récap.
- **Mises à jour à distance** : onglet Récap (plugins, cœur, thèmes). Après chaque action, le site est relu et son entrée de `results.json` rafraîchie.
- **Breakdance** : upload d'un zip dans le récap, puis « Installer + activer » (`POST /api/breakdance/install-and-activate`). L'agent télécharge le zip sur `PUBLIC_BACKEND_URL/dl/breakdance/{token}` (lien signé, 10 minutes) et vérifie son sha256.

- **Ajouter un plugin quelconque** : onglet « Ajouter un plugin » du récap. Upload d'un zip (un seul dossier racine, en-tête « Plugin Name »), choix des sites, installation en arrière-plan (2 sites en parallèle, arrêt si le serveur bloque). Un plugin déjà présent est remplacé en gardant son état actif ou inactif ; un nouveau plugin est activé. Exige `PUBLIC_BACKEND_URL` en `https://` (voir `.env.example`). Les zips sont stockés dans `data/plugins/<slug>/`.

Le backend n'accepte d'envoyer des ordres qu'à des **sites publics déjà audités** (`results.json`) : les URLs vers `localhost`, des IP privées ou un hôte inconnu sont refusées.

---

## Dépannage

| Symptôme | À vérifier |
|---|---|
| `403 - Accès refusé` | IP du backend dans `WEROCKET_ALLOWED_IPS` ; `WEROCKET_AGENT_HEADER_KEY` identique des deux côtés ; clé publique du site = clé privée du backend ; horloge du serveur (`date`). |
| `404 - Route API non déclarée` | Plugin inactif ou permaliens à régénérer (Réglages → Permaliens → Enregistrer). |
| `429` | Trop de tentatives échouées depuis l'IP : attendre, puis corriger la cause du refus. |
| Mise à jour de plugin « sans effet » | Le plugin a toujours une mise à jour en attente après l'appel (ex. licence Breakdance refusée, « Unauthorized ») : installer le zip depuis le récap. |
| Agent injoignable / délai dépassé | Pare-feu ou WAF (Wordfence) qui bloque l'IP du backend. |

En cas de compromission : régénérer la paire de clés et `WEROCKET_AGENT_HEADER_KEY`, mettre à jour `backend/.env` puis `wp-config.php` sur tous les sites, et chercher `WeRocket Agent Security` dans `wp-content/debug.log`.

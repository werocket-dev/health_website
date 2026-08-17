# 🚀 WeRocket Agent - Guide de Configuration

## 📋 Vue d'ensemble

Le système WeRocket Agent est une architecture **hybride** qui combine deux méthodes de scan :

1. **🔥 Agent WordPress (Méthode Premium)** : Plugin installé sur les sites qui retourne des données fiables
2. **🌐 Scraping HTML (Méthode Fallback)** : Analyse du code source si l'agent n'est pas disponible

---

## 🔐 Architecture de Sécurité

Le plugin WeRocket Agent utilise **7 couches de sécurité** :

1. ✅ **IP Whitelist** : Seules les IPs autorisées peuvent se connecter
2. ✅ **Rate Limiting** : Maximum 5 requêtes par heure par IP
3. ✅ **Header Secret** : Clé secrète dans le header `X-WeRocket-Key`
4. ✅ **Token Master** : Token principal pour l'authentification
5. ✅ **Timestamp** : Validation de la fraîcheur de la requête (±5 minutes)
6. ✅ **Signature HMAC-SHA256** : Signature cryptographique anti-replay
7. ✅ **POST uniquement** : Pas d'exposition via GET

---

## 📦 Installation

### Étape 1 : Configuration du Serveur d'Audit

#### 1.1 Générer les secrets

```bash
# Token Master (32 caractères minimum)
openssl rand -hex 32

# Header Secret (32 caractères minimum)
openssl rand -hex 32

# IP du serveur d'audit (à récupérer)
curl ifconfig.me
```

#### 1.2 Configurer le fichier `.env`

Ajoute ces lignes dans ton fichier `.env` :

```bash
# Configuration existante
POSTGRES_USER=ton_user
POSTGRES_PASSWORD=ton_password
DB_PORT=5434
POSTGRES_DB=werocket_audit

# NOUVELLES VARIABLES pour l'Agent
WEROCKET_AGENT_TOKEN=ton_token_genere_etape_1_1
WEROCKET_AGENT_HEADER_KEY=ton_header_secret_etape_1_1
```

#### 1.3 Créer les colonnes BDD

```bash
python3 setup_audit_columns.py
```

✅ Ce script ajoute automatiquement toutes les colonnes nécessaires :
- Colonnes de base : `Diagnostic_IA`, `Score_IA`, `Builder_Detecte`
- Colonnes plugins : `Plugins_Detectes`, `Mises_A_Jour`, `Nb_Updates`
- Colonnes Agent : `Methode_Scan`, `Version_WP`, `Version_PHP`, `Version_MySQL`, `Theme_Actif`, `Version_Theme`

---

### Étape 2 : Installation sur les Sites WordPress

#### 2.1 Préparer le plugin

Le fichier `werocket-agent.php` est un **plugin WordPress autonome** (pas besoin de dossier).

#### 2.2 Installation manuelle

**Option A : FTP/SFTP**
```bash
# Télécharge le plugin sur le serveur
scp werocket-agent.php user@site.com:/var/www/html/wp-content/plugins/

# Ou via FTP : dépose le fichier dans /wp-content/plugins/
```

**Option B : Interface WordPress**
1. Renomme `werocket-agent.php` en `werocket-agent.zip` (ajoute-le dans un dossier `werocket-agent/`)
2. Upload via **Extensions → Ajouter → Téléverser**

#### 2.3 Configuration du site WordPress

Édite le fichier `wp-config.php` et ajoute **AVANT** la ligne `/* That's all, stop editing! */` :

```php
// Configuration WeRocket Agent (Sécurité)
define( 'WEROCKET_AGENT_TOKEN', 'le_meme_token_que_dans_ton_env' );
define( 'WEROCKET_AGENT_HEADER_KEY', 'le_meme_header_que_dans_ton_env' );
define( 'WEROCKET_ALLOWED_IPS', '1.2.3.4' ); // IP de ton serveur d'audit
```

⚠️ **IMPORTANT** : Utilise les **MÊMES valeurs** que dans ton `.env`

#### 2.4 Activation

**Via l'interface WordPress :**
1. Va dans **Extensions**
2. Active "WeRocket Agent"

**Via WP-CLI (plus rapide pour installations en masse) :**
```bash
wp plugin activate werocket-agent
```

#### 2.5 Vérification

Teste la connexion depuis ton serveur :

```bash
curl -X POST https://site-client.com/wp-json/werocket/v1/status \
  -H "Content-Type: application/json" \
  -H "X-WeRocket-Key: ton_header_secret" \
  -d '{
    "token": "ton_token",
    "timestamp": '$(date +%s)',
    "signature": "teste_avec_script_python"
  }'
```

Si tu vois un JSON avec `"success": true`, c'est OK ! ✅

---

## 🚀 Utilisation

### Lancer l'audit

```bash
# (Première fois) Créer les colonnes BDD
python3 setup_audit_columns.py

# Lancer l'audit complet
python3 run_global_audit.py
```

### Sortie attendue

```
✅ Agent WeRocket configuré - Mode hybride activé

🚀 Lancement de l'audit global sur 100 sites...
⏱️  Mode GENTIL: pause de 2-4s entre chaque site pour éviter les blocages

🔎 [1/100] Audit : MORIFLO...
   ✅ [AGENT] IA: OK (87%) | WP: 6.4.2 | PHP: 8.1.0 | Builder: Divi | Plugins: 8 | MAJ: 1

🔎 [2/100] Audit : GARAGE FOLLENS...
   ⚠️  Agent KO (Plugin non installé) - Fallback scraping HTML...
   ✅ [SCRAPING] IA: OK (92%) | Builder: Elementor | Plugins: 5 | MAJ: 0
```

---

## 📊 Rapport Final Amélioré

À la fin de l'audit, tu obtiens un rapport détaillé :

```
📊 RAPPORT D'AUDIT FINAL
======================================================================

🎯 Sites analysés : 100
   ✅ Succès : 98
   ❌ Erreurs : 2

🤖 Diagnostic IA :
   🟢 OK : 85 (87%)
   🟡 ATTENTION : 10 (10%)
   🔴 ALERTE : 3 (3%)

🔐 Méthode de scan :
   🚀 Agent WordPress : 75 sites (76%)
   🌐 Scraping HTML : 23 sites (24%)

   ⚠️  Raisons d'échec de l'Agent :
      • Plugin non installé : 18 fois
      • Authentification refusée : 3 fois
      • Timeout : 2 fois

🌐 Technologies WordPress :
   WordPress détecté : 98/98 sites

   🎨 Builders utilisés :
      • Divi : 45 site(s)
      • Elementor : 30 site(s)
      • Breakdance : 10 site(s)

🔌 Plugins détectés :
   Total de plugins : 487
   Sites avec plugins : 98/98
   Moyenne par site : 5.0 plugins

🔄 Mises à jour :
   🔴 Sites nécessitant des MAJ : 23
   🟢 Sites à jour : 75
```

---

## 🔧 Déploiement en Masse

### Script d'installation automatique (WP-CLI)

Si tu as WP-CLI sur tous tes sites, tu peux automatiser :

```bash
#!/bin/bash
# deploy_agent.sh

SITES=(
    "user1@site1.com:/var/www/html"
    "user2@site2.com:/var/www/html"
    # Ajoute tous tes sites...
)

for site in "${SITES[@]}"; do
    echo "📦 Installation sur $site..."
    
    # 1. Upload du plugin
    scp werocket-agent.php "$site/wp-content/plugins/"
    
    # 2. Activation (via SSH)
    ssh "${site%%:*}" "cd ${site##*:} && wp plugin activate werocket-agent"
    
    echo "✅ Terminé pour $site"
done
```

⚠️ **N'oublie pas** de configurer `wp-config.php` sur chaque site !

---

## 🛡️ Sécurité

### Recommandations

1. **Rotation des secrets** : Change les tokens tous les 6 mois
2. **Logs de sécurité** : Le plugin log toutes les tentatives d'accès dans `/wp-content/debug.log`
3. **IP Whitelist stricte** : N'autorise QUE ton serveur d'audit
4. **HTTPS obligatoire** : Le plugin vérifie les certificats SSL

### En cas de compromission

1. Régénère immédiatement les secrets
2. Mets à jour le `.env` sur le serveur
3. Mets à jour `wp-config.php` sur tous les sites
4. Vérifie les logs : `grep "WeRocket Agent Security" /wp-content/debug.log`

---

## ❓ FAQ

### Le plugin ralentit-il mon site ?

Non ! Le plugin :
- Ne consomme aucune ressource en temps normal
- S'active uniquement lors d'un audit (quelques secondes)
- Utilise le cache natif de WordPress

### Puis-je auditer un site sans agent ?

Oui ! Le système **fallback automatiquement** sur le scraping HTML. Tu obtiens :
- ✅ Version des plugins (si visible dans le HTML)
- ✅ Détection du builder
- ❌ Pas de version PHP/MySQL
- ❌ Pas de statut actif/inactif des plugins

### Que faire si l'agent retourne 403 ?

Vérifie que :
1. Les secrets sont **identiques** dans `.env` et `wp-config.php`
2. Ton IP est dans la whitelist
3. Le timestamp de ton serveur est correct : `date`

---

## 📝 Changelog

### v2.0.0 (Mars 2026)
- ✅ Mode hybride Agent + Scraping
- ✅ Sécurité renforcée (7 couches)
- ✅ Stockage versions PHP/MySQL/Thème
- ✅ Détection statut actif/inactif des plugins
- ✅ Rapport enrichi avec méthode de scan

---

## 🤝 Support

En cas de problème :
1. Vérifie les logs : `tail -f /wp-content/debug.log` sur le site WordPress
2. Active le mode debug dans `.env` : `DEBUG=True`
3. Teste la connexion avec curl (voir section 2.5)

---

**Développé avec ❤️ pour We Rocket**

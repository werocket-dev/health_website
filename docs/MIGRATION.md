# 🔄 Guide de Migration - Nouvelle Structure

## ✅ Changements de Chemins

### Scripts Principaux

| Ancien Chemin | Nouveau Chemin |
|--------------|----------------|
| `extraction_dataset.py` | `scripts/1_extraction_dataset.py` |
| `init_db.py` | `scripts/2_init_db.py` |
| `generate_broken_dataset.py` | `scripts/3_generate_dataset.py` |
| `train_ai.py` | `scripts/4_train_ai.py` |
| `setup_audit_columns.py` | `scripts/5_setup_audit_columns.py` |
| `run_global_audit.py` | `scripts/6_run_global_audit.py` |

### Scripts de Test

| Ancien Chemin | Nouveau Chemin |
|--------------|----------------|
| `test_notion.py.py` | `tests/test_notion.py` |
| `test_universal_detection.py` | `tests/test_universal_detection.py` |
| `check_maintenance.py` | `tests/check_maintenance.py` |
| `debug_plugins.py` | `tests/debug_plugins.py` |
| `update_premium_versions.py` | `tests/update_premium_versions.py` |

### Documentation

| Ancien Chemin | Nouveau Chemin |
|--------------|----------------|
| `README_AGENT.md` | `docs/README_AGENT.md` |
| `README_API.md` | `docs/README_API.md` |
| (nouveau) | `README.md` |

### Données

| Ancien Chemin | Nouveau Chemin |
|--------------|----------------|
| `sites_werocket.csv` | `data/sites_werocket.csv` |

### Plugin WordPress

| Ancien Chemin | Nouveau Chemin |
|--------------|----------------|
| `werocket-agent.php` | `wordpress/werocket-agent.php` |

---

## 📝 Mise à Jour des Commandes

### Anciennes Commandes

```bash
# ❌ Anciennes commandes (ne fonctionnent plus)
python3 extraction_dataset.py
python3 init_db.py
python3 setup_audit_columns.py
python3 run_global_audit.py
```

### Nouvelles Commandes

```bash
# ✅ Nouvelles commandes (à utiliser)
python3 scripts/1_extraction_dataset.py
python3 scripts/2_init_db.py
python3 scripts/5_setup_audit_columns.py
python3 scripts/6_run_global_audit.py
```

---

## 🚀 Workflow Complet

### Setup Initial (Une seule fois)

```bash
# 1. Extraire les sites depuis Notion
python3 scripts/1_extraction_dataset.py

# 2. Initialiser la BDD
python3 scripts/2_init_db.py

# 3. Générer le dataset
python3 scripts/3_generate_dataset.py

# 4. Entraîner l'IA
python3 scripts/4_train_ai.py

# 5. Créer les colonnes
python3 scripts/5_setup_audit_columns.py
```

### Audit Régulier

```bash
# Lancer l'audit complet
python3 scripts/6_run_global_audit.py
```

---

## 🗂️ Fichiers Supprimés

Ces fichiers étaient obsolètes et ont été supprimés :

- ❌ `add_audit_columns.sql` → Remplacé par `scripts/5_setup_audit_columns.py`
- ❌ `capture_sites.py` → Fonctionnalité intégrée dans `scripts/6_run_global_audit.py`
- ❌ `setup_agent_columns.py` → Fusionné dans `scripts/5_setup_audit_columns.py`

---

## 📋 Checklist Post-Migration

- [ ] Tester l'extraction Notion : `python3 scripts/1_extraction_dataset.py`
- [ ] Vérifier la connexion BDD : `docker-compose ps`
- [ ] Tester un audit : `python3 scripts/6_run_global_audit.py`
- [ ] Vérifier que le modèle IA existe : `ls -lh werocket_vision_model.h5`
- [ ] Consulter la documentation : `cat README.md`

---

## ❓ Problèmes Courants

### "No such file or directory"

Si tu as des scripts/alias qui utilisent les anciens chemins :

```bash
# Créer des alias temporaires (ajoute dans ~/.zshrc)
alias audit='python3 scripts/6_run_global_audit.py'
alias setup='python3 scripts/5_setup_audit_columns.py'
```

### Import errors dans les scripts

Les scripts utilisent des chemins relatifs. Assure-toi d'être dans le dossier racine :

```bash
cd /Users/montagnonromain/Desktop/We\ Rocket/maintenance_wp
python3 scripts/6_run_global_audit.py
```

---

## 🎯 Bénéfices de la Nouvelle Structure

✅ **Organisation claire** : Scripts numérotés par ordre d'exécution  
✅ **Séparation logique** : Production vs Tests vs Documentation  
✅ **Scalabilité** : Facile d'ajouter de nouveaux scripts  
✅ **Navigation** : Structure évidente pour les nouveaux développeurs  
✅ **Maintenance** : Plus facile de trouver et modifier du code  

---

**Migration effectuée le 4 mars 2026**

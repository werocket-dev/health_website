# 🚀 API WordPress.org - Guide de mise à jour

## ✅ Ce qui a été ajouté

### 1. **Fonction `check_updates_api()` optimisée**
- ✅ Interroge l'API WordPress.org en temps réel (Wordfence, Yoast, Rank Math, Elementor)
- ✅ Cache intelligent (24h) pour éviter les requêtes répétées
- ✅ Fallback automatique si l'API est down
- ✅ Références manuelles pour les plugins premium (Divi, Breakdance)

### 2. **Performance optimisée**
- Cache global qui expire après 24h
- Une seule requête API par plugin par jour
- Timeout de 5s pour éviter les blocages

## ⚠️ IMPORTANT : Versions Premium

Les versions de **Divi** et **Breakdance** dans le script sont des estimations.

### Comment vérifier et mettre à jour :

```bash
# Lance le script de vérification
python3 update_premium_versions.py
```

Ce script :
1. ✅ Récupère automatiquement les versions des plugins gratuits (API WordPress.org)
2. ⚠️ Te donne les liens pour vérifier Divi et Breakdance manuellement

### Mise à jour manuelle :

1. Ouvre [run_global_audit.py](run_global_audit.py#L142)
2. Cherche le dictionnaire `PREMIUM_VERSIONS`
3. Modifie les versions :
```python
PREMIUM_VERSIONS = {
    "Divi": "4.XX.X",        # ← Remplace par la vraie version
    "Breakdance": "1.X.X"    # ← Remplace par la vraie version
}
```

### Sources officielles :

- **Divi** : https://www.elegantthemes.com/gallery/divi/
  - Va dans la page, cherche le changelog ou "Latest Version"
  
- **Breakdance** : https://breakdance.com/changelog/
  - La première entrée du changelog est la version actuelle

## 🎯 Utilisation

```bash
# 1. Vérifier/mettre à jour les versions premium
python3 update_premium_versions.py

# 2. (Première fois seulement) Créer les colonnes BDD
python3 setup_audit_columns.py

# 3. Lancer l'audit avec API WordPress.org
python3 run_global_audit.py
```

## 📊 Résultat attendu

```
🔎 [1/50] Audit : MORIFLO...
   ✅ IA: OK (87%) | Builder: Divi | Plugins: 3 | MAJ: 1
   
   Plugins détectés :
   - Wordfence (8.0.2 🟢)
   - Yoast SEO (7.9.0 🔴 → 22.5)  ← Mise à jour disponible !
   - Divi (4.24.0 🟢)
```

## 🔄 Fréquence de mise à jour

- **Plugins gratuits** : Automatique via API (cache 24h)
- **Plugins premium** : Manuel (vérifie 1x par mois)

---

💡 **Astuce** : Ajoute un reminder mensuel pour vérifier les versions de Divi et Breakdance !

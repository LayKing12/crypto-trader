# Dépôt GitHub `BBarry1080/cryptomind-etoro`

Ce dossier est déjà un dépôt git local (branche `feature/etoro-module`). Le dépôt distant doit être
créé vide par vous, puis la branche est poussée depuis ce PC, où Git Credential Manager connaît déjà
le compte BBarry1080.

## 1. Créer le dépôt vide

https://github.com/new, connecté en **BBarry1080** :

- Repository name : `cryptomind-etoro`
- Public ou Private au choix (le CI GitHub Actions est gratuit dans les deux cas pour ce volume)
- **Ne cochez rien** : pas de README, pas de .gitignore, pas de licence

## 2. Pousser le code

```bash
cd C:\Users\Aliou\Desktop\cryptomind-etoro && git remote add origin https://github.com/BBarry1080/cryptomind-etoro.git && git push -u origin feature/etoro-module
```

Puis créer `main` à partir de cette branche :

```bash
cd C:\Users\Aliou\Desktop\cryptomind-etoro && git checkout -b main && git push -u origin main
```

## 3. Ce qui ne doit jamais être poussé

`.gitignore` exclut `.env`, `data/`, `__pycache__/` et `dashboard/node_modules/`. Vérifier avant le
premier push :

```bash
cd C:\Users\Aliou\Desktop\cryptomind-etoro && git ls-files | findstr /i "\.env node_modules"
```

Attendu : uniquement `.env.example`.

## 4. Brancher Render

Render › New › Blueprint › `cryptomind-etoro`, branche `main`. Chaque push sur `main` redéploie.
Détails : `docs/hosting_render.md`.

## 5. CI

Le workflow est fourni dans `ci/github-workflow-ci.yml` : à copier vers `.github/workflows/ci.yml` depuis l'interface GitHub (le jeton git du PC n'a pas le scope `workflow`). Il lance `pytest` sur chaque push et pull request (Python 3.12).

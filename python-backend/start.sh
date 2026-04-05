#!/usr/bin/env bash
# CryptoMind Python Backend — démarrage rapide
set -e

echo "=== CryptoMind Risk Engine V1 ==="

# Copier .env si absent
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  .env créé depuis .env.example — remplis les clés API avant de lancer en mode LIVE"
fi

# Installer dépendances si besoin
if [ ! -d venv ]; then
  python -m venv venv
fi

source venv/bin/activate 2>/dev/null || source venv/Scripts/activate

pip install -r requirements.txt -q

echo ""
echo "Mode: $(grep PAPER_TRADING .env | head -1)"
echo ""

uvicorn main:app --reload --host 0.0.0.0 --port 8000

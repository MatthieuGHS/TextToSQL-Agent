#!/usr/bin/env bash
# Lancement en développement : l'API et l'interface, rechargées à chaud.
#
# Deux processus, deux ports, mais **une seule adresse à ouvrir** : Vite sert l'interface
# sur 5173 et relaie `/api` vers uvicorn sur 8000 (voir `web/vite.config.ts`). Il n'y a
# donc aucun CORS à configurer, et l'interface appelle `/api/...` en relatif — exactement
# comme en production, où uvicorn sert les deux.
#
# Pour le livrable, ce n'est pas ce script : c'est `docker compose up --build`.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f data/mmm.duckdb ]]; then
  echo "Base absente. La construire d'abord :  python -m src.etl.build_db" >&2
  exit 1
fi

if [[ ! -d web/node_modules ]]; then
  echo "→ installation des dépendances de l'interface"
  (cd web && npm install)
fi

# Les deux processus meurent ensemble : un serveur d'API resté seul en arrière-plan est
# la cause classique du « ça marchait tout à l'heure » au lancement suivant.
trap 'kill 0' EXIT INT TERM

echo "→ API        http://127.0.0.1:8000"
.venv/bin/uvicorn src.app.api:application --reload --port 8000 &

echo "→ interface  http://127.0.0.1:5173"
(cd web && npm run dev) &

wait

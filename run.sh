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
#
# On arrête **les deux enfants nommément**, et non le groupe de processus. Le
# `trap 'kill 0'` d'origine faisait exactement ce qu'il ne fallait pas : `kill 0` vise le
# groupe entier, donc ce script aussi ; il commençait à mourir, ce qui redéclenchait son
# propre trap `EXIT`, qui rappelait `kill 0`. Bash tombait en erreur de segmentation
# (trois fois le 19/08/2026, core dumps à l'appui) **avant** d'avoir arrêté uvicorn et
# Vite, qui restaient orphelins à tenir les ports. Symptôme vécu : « Ctrl+C ne tue pas
# l'application », puis un port occupé au lancement suivant.
#
# Le désarmement du trap en première ligne du nettoyage est la pièce essentielle : sans
# lui, `kill` puis la sortie du script rejoueraient le nettoyage en boucle.
api="" ; web=""

nettoyer() {
  trap - EXIT INT TERM
  [[ -n $api ]] && kill "$api" 2>/dev/null
  [[ -n $web ]] && kill "$web" 2>/dev/null
  # Laisse à uvicorn le temps d'arrêter son propre processus de rechargement, et à Vite
  # de rendre son port. Sans attente, le lancement suivant retombe sur un port occupé.
  wait 2>/dev/null
  return 0
}

trap nettoyer EXIT INT TERM

echo "→ API        http://127.0.0.1:8000"
.venv/bin/uvicorn src.app.api:application --reload --port 8000 &
api=$!

echo "→ interface  http://127.0.0.1:5173"
(cd web && npm run dev) &
web=$!

wait

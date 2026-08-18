# Interface

React + Vite + TypeScript, avec Tailwind. Aucune logique métier : elle affiche ce que
l'API rend, et ne recalcule rien — en particulier pas `arret_normal`, qui est décidé côté
serveur.

## Développement

Depuis la racine du dépôt : `./lancer.sh`. Vite sert l'interface sur 5173 et relaie
`/api` vers uvicorn sur 8000, donc une seule adresse à ouvrir et aucun CORS.

## Structure

| Fichier | Rôle |
|---|---|
| `src/types.ts` | les types de la frontière, recopiés de `src/app/schemas.py` |
| `src/api.ts` | le client HTTP ; lit le flux NDJSON ligne à ligne |
| `src/App.tsx` | la conversation et son état |
| `src/composants/` | affichage — message, requête, tableau, progression, saisie |

## Ce qui reste à faire à E7

Le composant de graphique n'existe pas encore. Il se branchera dans `BlocRequete`, à
partir des `colonnes` et `lignes` que l'API rend déjà : la frontière n'aura pas à changer.
La bibliothèque retenue est Recharts, et le choix est sans conséquence forte — la
*spécification* du graphique sera produite côté serveur par `src/charts/`, l'interface ne
faisant que la rendre.

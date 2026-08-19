# Interface

React + Vite + TypeScript, avec Tailwind. Aucune logique métier : elle affiche ce que
l'API rend, et ne recalcule rien — en particulier pas `arret_normal`, qui est décidé côté
serveur.

## Développement

Depuis la racine du dépôt : `./run.sh`. Vite sert l'interface sur 5173 et relaie
`/api` vers uvicorn sur 8000, donc une seule adresse à ouvrir et aucun CORS.

## Structure

| Fichier | Rôle |
|---|---|
| `src/types.ts` | les types de la frontière, recopiés de `src/app/schemas.py` |
| `src/api.ts` | le client HTTP ; lit les flux NDJSON ligne à ligne |
| `src/App.tsx` | l'aiguillage entre les deux pages, et l'état de la conversation |
| `src/composants/` | affichage — message, graphique, requête, tableau, progression, saisie |
| `src/composants/PageDonnees.tsx` | chargement des sources et relance de la pipeline |

Deux pages, donc pas de routeur : l'application est mono-utilisateur et sans état serveur,
et l'URL n'a pas à être partageable.

## Ce que l'interface ne décide pas

Le graphique. Son type, son abscisse, ses séries et leur répartition sur deux axes viennent
de `src/charts/`, côté serveur, où ils sont testés en fonctions pures. Une règle de
lisibilité ajoutée ici échapperait à ces tests et se dédoublerait.

`arret_normal` non plus : il est calculé côté serveur. Le redéduire de `arret` dans le
navigateur le ferait diverger un jour, et un abandon finirait par s'afficher comme un
succès.

## Sécurité

`rehype-raw` n'est pas installé et ne doit jamais l'être. Le texte affiché vient d'un
modèle qui lit lui-même une base de données : sans cette garantie, une chaîne malveillante
stockée dans les données pourrait ressortir dans une réponse et s'exécuter.
`tests/Markdown.test.tsx` en fait une propriété vérifiée.

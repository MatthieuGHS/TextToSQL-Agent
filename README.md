# Agent data — média

Un agent conversationnel qui répond en langage naturel à des questions sur une base de
données média : il écrit le SQL, l'exécute en lecture seule, et rend le résultat en texte,
en tableau et en graphique.

Les données sont des investissements publicitaires hebdomadaires et leurs indicateurs de
performance, destinés à alimenter un modèle de *Marketing Mix Modeling*. **L'agent sert à
explorer et auditer ces données avant modélisation** — il ne modélise pas, ne calcule pas
de ROI et n'attribue aucune vente à un canal, faute de données le permettant.

## Aperçu

> **Comment s'est réparti le budget marketing entre les canaux l'année dernière ?**

L'agent annonce ce qu'il cherche, écrit sa requête, et rend :

- une **réponse rédigée**, qui explicite le périmètre retenu et les réserves qui s'imposent
  — ici, que le référencement naturel n'apparaît pas au classement parce que son coût est
  `NULL` : non acheté, et non pas gratuit ;
- un **graphique**, choisi par le code à partir de la forme du résultat, ou refusé avec son
  motif quand aucune figure honnête n'est possible ;
- **chaque requête exécutée**, avec le raisonnement écrit avant de la lancer, les tables
  qu'elle a réellement lues, son SQL et son résultat.

Les questions pièges font partie du contrat. Demander un classement des canaux « les plus
performants » obtient un refus argumenté : clics, GRP et impressions ne se comparent pas,
et rien dans ces données ne relie un canal à une vente.

## Démarrage rapide

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # requirements.lock.txt pour les versions exactes
cp .env.example .env                 # puis renseigner ANTHROPIC_API_KEY
```

Déposer les fichiers sources dans `data/raw/`, puis construire la base :

```bash
python -m src.etl.build_db
```

Lancer, au choix :

```bash
./run.sh                     # développement : API + interface, rechargement à chaud
docker compose up --build    # livraison : une image, une seule adresse
python -m src.agent "Quel budget média sur la dernière année ?"   # sans interface
```

L'interface est sur <http://127.0.0.1:5173> en développement, <http://127.0.0.1:8000> en
conteneur.

⚠️ **Chaque question consomme des appels API facturés.**

## Les données

Trois tables, construites depuis quatre fichiers sources.

| Table | Contenu | Périmètre |
|---|---|---|
| `media` | Investissements et performances média, par semaine / canal / entité | l'annonceur seul |
| `kpi_compteurs` | L'indicateur cible, par semaine et par énergie | l'annonceur seul |
| `contexte` | Prix, parts de marché, et investissements des concurrents | le marché |

**Le principal risque du jeu de données** : `cost`, `grp` et `compteurs` existent dans
`contexte` **et** dans les deux autres tables, avec des ordres de grandeur voisins. Une
somme entre tables produit un résultat faux mais crédible. La colonne `brand_name` indique
de qui l'on parle.

Quatre autres propriétés structurent les réponses, et l'agent les connaît :

- chaque canal ne porte **qu'une seule** métrique de performance — GRP, impressions ou
  clics — et elles ne sont pas comparables entre elles ;
- le coût est `NULL` sur tout le référencement naturel : non acheté, pas gratuit ;
- **aucune attribution** n'est possible d'un canal vers une vente ;
- aucune dimension géographique ni démographique, et les trois tables n'ont pas les mêmes
  bornes temporelles.

La construction de la base est **atomique et vérifiée** : les invariants du contrat de
données sont contrôlés avant remplacement, et un échec laisse la base précédente intacte.

## Comment ça marche

Une question traverse quatre étages.

**La boucle** (`src/agent/boucle.py`) expose `ask(question, historique) -> AgentResponse`.
Le modèle dispose d'un seul outil : exécuter une requête de lecture. Il peut en enchaîner
plusieurs, se reprendre après une erreur, et s'arrête quand il a de quoi répondre.

**L'accès SQL** (`src/db/sql.py`) est le point d'entrée unique vers la base. Il valide la
requête avec le parseur du moteur — jamais avec une expression régulière —, refuse tout ce
qui n'est pas une lecture unique, borne le nombre de lignes et le temps d'exécution, et
annonce toute troncature. Il rend aussi la **somme des colonnes issues d'un `SUM()`**, pour
que le modèle n'ait jamais à additionner lui-même.

**Le graphique** (`src/charts/`) est décidé **par le code**, à partir de la forme du
résultat de la dernière requête traçable. Le modèle n'a ni outil de dessin ni spécification
à produire : son SQL *est* l'expression de son intention. Le refus est un résultat de
premier ordre — un résultat d'une seule ligne, une série qui écrase ses propres valeurs ou
des dates choisies par leur valeur ne donnent pas de figure honnête, et le module le dit.

**L'interface** (`web/`) n'est qu'une couche de présentation. La remplacer ne touche pas au
moteur.

### Trois propriétés vérifiées mécaniquement

- **Aucun module n'ouvre la base hors de `src/db/connexion.py`.** Toutes les protections de
  `sql.py` reposent sur cette prémisse ; un test parcourt les sources pour la garantir.
- **La suite de tests ne consomme aucun appel API.** Un test qui construirait un agent réel
  la fait échouer. C'est cette contrainte qui a décidé de l'architecture : le client de
  modèle est injecté partout.
- **L'observation ne change pas le résultat.** `ask()` accepte un `trace=` optionnel pour
  que l'interface montre son travail ; deux exécutions identiques, l'une observée et
  l'autre non, rendent la même réponse.

## Structure du code

```
src/
├── etl/      construction de la base depuis les fichiers sources
│   ├── transforms.py   fonctions pures — aucun fichier, aucune connexion
│   ├── checks.py       contrat de données : invariants, avertissements, volumétrie
│   ├── build_db.py     orchestration, écriture atomique, CLI
│   └── rechargement.py rejeu de la pipeline depuis l'interface
├── db/       seul accès à la base
│   ├── connexion.py    ouverture durcie : lecture seule, accès externe fermé
│   └── sql.py          run_sql() : valide, borne, exécute
├── agent/
│   ├── boucle.py       ask(question, historique) -> AgentResponse
│   ├── outil.py        le seul outil du modèle
│   └── prompt/         description des données : générée + écrite
├── charts/   décide s'il y a un graphique, et lequel
└── app/      API HTTP — coquille mince, aucune logique métier

web/          interface React + Vite + TypeScript
tests/
├── test_*.py           unitaires
└── eval/               harnais d'évaluation en conditions réelles
```

## L'interface

Deux pages.

**Conversation** — la question, la réponse en Markdown, le graphique quand le résultat s'y
prête, puis chaque requête avec son raisonnement, ses tables, son SQL coloré et son
résultat. Les requêtes en échec y figurent aussi : la boucle est faite pour se reprendre,
et les masquer donnerait de l'exécution une image plus lisse que la réalité. Thème clair
par défaut, bascule sombre ; aucun composant n'écrit une couleur littérale et le contraste
des deux thèmes est vérifié par un test.

**Données** — l'état des fichiers sources, leur remplacement, et la relance de la pipeline
avec son journal. Le téléversement passe par un dossier d'attente qui n'est promu qu'après
une construction réussie, et seuls les quatre noms de la pipeline sont acceptés : un
fichier mal formé ne dégrade ni les sources ni la base.

## Développement

```bash
pytest tests/ -q             # suite complète, ~10 s, aucun appel API
cd web && npm test           # interface
```

## Déploiement

```bash
docker compose up --build
```

Une seule image sert l'API et l'interface. Deux choses sont **montées** et jamais
construites dans une couche :

- **`data/`**, qui porte la base et les fichiers sources. Une image qui embarquerait des
  données client se diffuserait par accident ; `.dockerignore` l'en écarte. Le montage est
  en écriture, parce que la page « Données » reconstruit la base.
- **la clé API**, lue depuis `.env`. Le conteneur refuse de démarrer sans elle, plutôt que
  de laisser découvrir le problème à la première question.

L'image tourne sous un utilisateur non privilégié.

## Évaluation

Un corpus de questions et une grille fournie par le client mesurent le comportement de
l'agent en conditions réelles. **C'est le seul endroit d'où partent des appels facturés**,
et il faut le vouloir :

```bash
python -m tests.eval --a-blanc                 # rejoue le cache : 0 appel, 0 $
python -m tests.eval --k 1 --k-grille 1        # ⚠ peuplement facturé
python -m tests.eval --k 3 --k-grille 3        # ⚠ campagne de référence
```

Les campagnes déjà payées sont en cache et se rejouent gratuitement : **toujours commencer
à blanc**. Le cache est indexé sur tout ce qui peut changer une réponse — modèle, prompt,
bornes, description d'outil, version de la boucle — pour qu'une campagne ne soit jamais
resservie sous d'autres réglages.

Le harnais produit un score automatique. **Ce n'est pas la note du client** : sa grille se
note à la main, sur les trois colonnes qu'il a définies. Les deux mesurent des choses
différentes et divergent ; le score automatique reste utile comme détecteur d'anomalie.

## Pour reprendre le projet

**Lire `docs/decisions.md` avant le code.** Il porte le *pourquoi* de chaque choix
structurant et ce qui a déjà été mesuré — sa dernière section liste des pièges de
bibliothèque silencieux qu'il vaut mieux ne pas redécouvrir à ses frais.

**Le principe qui gouverne le reste** : *ce qui doit être vrai à chaque fois ne peut pas
dépendre du modèle*. Les bornes de `run_sql`, les plafonds de la boucle, les règles de
lisibilité des graphiques et le total d'une ventilation sont dans le code. Le prompt décrit
le monde ; il n'arbitre rien de critique.

**Le prompt décrit, il ne scripte pas.** La grille d'évaluation est un instrument de
mesure, jamais un cahier des charges. Une règle qui mentionnerait une question précise ou
une valeur attendue ferait passer ce cas et échouer le suivant.

**Ce qui n'est pas versionné** : les fichiers sources, la base construite, `.env` et
`docs/prive/`. Le dépôt reste exécutable sans eux — la suite de tests passe, seul le
harnais refuse de démarrer faute de campagne à rejouer.

## Conventions

- Français partout : code, commentaires, docstrings, messages, commits.
- Lignes de 92 caractères au plus.
- Les commentaires expliquent **pourquoi**, pas quoi. Les docstrings portent le
  raisonnement, pas seulement la signature.
- Aucune clé API dans le code : tout passe par les variables d'environnement.

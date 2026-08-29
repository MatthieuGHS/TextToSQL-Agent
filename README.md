# TextToSQL-Agent

Agent conversationnel qui traduit des questions en langage naturel en requêtes SQL, les
exécute sur une base en lecture seule, et restitue le résultat en texte, en tableau et en
graphique.

Le jeu de données porte sur des investissements média hebdomadaires et leurs indicateurs
de performance, destinés à alimenter un modèle de *Marketing Mix Modeling*. L'agent sert à
**explorer et auditer ces données avant modélisation** ; il ne fait pas de modélisation.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # ou requirements.lock.txt pour les versions exactes
cp .env.example .env                 # puis renseigner ANTHROPIC_API_KEY
```

## Préparation des données

Déposer les fichiers sources dans `data/raw/`, puis construire la base :

```bash
python -m src.etl.build_db
```

La construction est **atomique et vérifiée** : les invariants du contrat de données sont
contrôlés avant remplacement, et un échec laisse la base précédente intacte. La même
pipeline se relance depuis l'interface, page « Données ».

Trois tables sont produites :

| Table | Contenu | Périmètre |
|---|---|---|
| `media` | Investissements et performances média, par semaine / canal / entité | l'annonceur suivi uniquement |
| `kpi_compteurs` | L'indicateur cible, par semaine et par énergie | l'annonceur suivi uniquement |
| `contexte` | Variables d'environnement : prix, parts de marché, et **investissements, GRP et compteurs des concurrents** | le marché |

⚠️ Trois métriques (`cost`, `grp`, `compteurs`) existent dans `contexte` **et** dans les
deux autres tables, avec des périmètres différents et des ordres de grandeur voisins. Ne
jamais les additionner entre tables : la colonne `brand_name` indique de qui l'on parle.
C'est le principal risque du jeu de données — une somme entre tables produit un résultat
faux mais crédible.

## Lancer l'agent

```bash
./run.sh                     # développement : API + interface, rechargement à chaud
docker compose up --build    # le livrable : une image, une seule adresse
```

En développement, l'interface est sur <http://127.0.0.1:5173> ; en conteneur, sur
<http://127.0.0.1:8000>. La base et la clé API sont **montées** et jamais construites dans
l'image — une image qui porterait des données client se diffuserait par accident.

En ligne de commande, sans interface :

```bash
python -m src.agent "Quel budget média sur la dernière année ?"
```

⚠️ **Chaque question consomme des appels API facturés.** Le cache d'évaluation est un
dispositif du harnais de mesure, pas du produit.

## L'interface

Deux pages.

**Conversation** — la question, la réponse rendue en Markdown, le graphique quand le
résultat s'y prête, puis chaque requête exécutée avec **le raisonnement écrit avant de la
lancer**, les tables qu'elle a réellement lues, son SQL coloré et le tableau de son
résultat. Le bloc qui porte la conclusion s'ouvre de lui-même ; les autres se déplient.
Les requêtes en échec y figurent aussi : la boucle est faite pour se reprendre, et les
masquer donnerait de l'exécution une image plus lisse que la réalité. Les étapes défilent
pendant que l'agent travaille et restent affichées ensuite.

Thème clair par défaut, bascule sombre en haut à droite. Aucun composant n'écrit une
couleur littérale : tout passe par des jetons sémantiques définis deux fois dans
`web/src/index.css`, et le contraste des deux thèmes est vérifié par un test.

**Données** — l'état des fichiers sources, leur remplacement, et la relance de la pipeline
avec son journal complet. Le téléversement passe par un dossier d'attente qui n'est promu
qu'après une construction réussie : un fichier mal formé ne dégrade ni les sources ni la
base. Seuls les quatre noms de la pipeline sont acceptés, ce qui ferme aussi la traversée
de chemin.

⚠️ Cette page **écrit** dans `data/`, c'est pourquoi `compose.yaml` y monte le dossier en
écriture. Le monter en lecture seule casse la page sans que rien ne l'annonce autrement
qu'un « erreur interne » — c'est arrivé, et ça n'a été vu qu'en ouvrant le conteneur.

## Architecture

```
src/
├── etl/      construction de la base depuis les fichiers sources
│   ├── transforms.py   fonctions pures, testables sans base ni fichier
│   ├── checks.py       contrat de données
│   └── build_db.py     orchestration, écriture atomique
├── db/       seul accès à la base
│   ├── connexion.py    ouverture en lecture seule et durcie
│   └── sql.py          run_sql() : valide, borne, exécute
├── agent/
│   ├── boucle.py       ask(question, historique) -> AgentResponse
│   ├── outil.py        le seul outil du modèle
│   └── prompt/         description des données : générée + écrite
├── charts/   décide s'il y a un graphique à faire, et lequel
└── app/      API HTTP — coquille mince, sans logique métier

web/          interface React + Vite + TypeScript
tests/eval/   harnais d'évaluation en conditions réelles (appels facturés)
```

Le cœur expose `ask(question, historique) -> AgentResponse`. **L'interface n'est qu'une
couche de présentation : la remplacer ne touche pas au moteur.** Elle ne décide notamment
rien du graphique — le type, les axes et les séries sont choisis côté serveur, en code
déterministe et testé en fonctions pures.

**Aucun module n'ouvre la base hors de `src/db/connexion.py`** — vérifié par un test qui
parcourt les sources. Les protections de `sql.py` reposent sur cette prémisse : lecture
seule, aucun accès disque ni réseau, une seule instruction de lecture par appel, plafond de
lignes annoncé, délai maximal.

## Tests

```bash
pytest tests/ -q             # suite complète, ~8 s, aucun appel API
cd web && npm test           # interface
```

**La suite ne consomme aucun appel API**, et c'est une propriété vérifiée
mécaniquement — un test qui construirait un agent réel fait échouer
`tests/test_agent_structure.py`. C'est cette contrainte qui a décidé de l'architecture :
le client de modèle est injecté partout.

Le harnais d'évaluation est le seul endroit d'où partent des appels facturés, et il faut
le vouloir :

```bash
python -m tests.eval --a-blanc                  # rejoue le cache : 0 appel, 0 $
python -m tests.eval --k 1 --k-grille 1        # ⚠ peuplement facturé (~35 appels)
python -m tests.eval --k 3 --k-grille 3        # ⚠ campagne de référence (~105 appels)
```

## Reprendre ce dépôt

Pour l'équipe qui récupère le projet. À lire dans cet ordre.

**1. `docs/decisions.md` avant le code.** Il porte le *pourquoi* de chaque choix
structurant, et surtout ce qui a déjà été mesuré : sa dernière section liste des pièges
de bibliothèque silencieux, qu'il vaut mieux ne pas redécouvrir à ses frais. Les
arbitrages tranchés y figurent avec le chiffre qui les a tranchés.

**2. Trois propriétés portent tout le reste.** Le noyau expose `ask(question, historique)`
et ne connaît pas l'interface. Aucun module n'ouvre la base hors de `src/db/connexion.py`.
Et le graphique est décidé **par le code**, à partir de la forme du résultat : le modèle
n'a ni outil de dessin ni spécification à produire, son SQL *est* l'expression de son
intention. Les trois sont vérifiées par des tests qui échouent si on les casse.

**3. Ce qui doit être vrai à chaque fois ne dépend jamais du modèle.** Les bornes de
`run_sql`, les plafonds de la boucle, les règles de lisibilité des graphiques et le total
d'une ventilation sont dans le code. Le prompt décrit le monde ; il n'arbitre rien de
critique. Ajouter une règle de prompt pour garantir un comportement est le geste qui a
échoué quatre fois sur ce projet.

**4. Ce qui n'est pas dans Git.** Les données sources, la base construite, `.env` et
`docs/prive/` (documents de travail, notations, chiffres client). Le dépôt reste
exécutable sans eux : la suite de tests passe, le harnais refuse simplement de démarrer
faute de campagne à rejouer.

**5. Le coût.** Les tests ne consomment aucun appel. L'interface et la ligne de commande
en consomment à chaque question. Le harnais d'évaluation est le seul endroit d'où partent
des campagnes facturées, et il faut le vouloir — commencer systématiquement par
`--a-blanc`, qui rejoue gratuitement ce qui a déjà été payé.

**6. Évaluer une modification.** La grille de 18 questions du client se note **à la main**,
sur ses trois colonnes. Le score du harnais mesure autre chose et diverge : il reste utile
comme détecteur d'anomalie, jamais comme note. Toute campagne payée doit tester un
correctif déjà écrit, sinon elle mesure sans améliorer.

## Conventions

- Les données sources, la base générée, le `.env` et `docs/prive/` sont **hors Git**.
  Les jeux d'essai des tests font exception : ils sont inventés, donc versionnés.
- Aucune clé API dans le code : tout passe par les variables d'environnement.
- Le prompt système décrit les données et des principes généraux — jamais de règle
  spécifique à une question donnée.
- Français partout : code, commentaires, docstrings, messages, commits.

Le *pourquoi* de chaque choix structurant est dans **`docs/decisions.md`**. Les consignes
de travail sur le dépôt sont dans **`CLAUDE.md`**.

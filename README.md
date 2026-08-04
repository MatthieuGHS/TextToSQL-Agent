# TextToSQL-Agent

Agent IA conversationnel qui traduit des questions en langage naturel en requêtes SQL,
les exécute, et restitue le résultat sous forme de tableau et de graphique.

Le jeu de données porte sur des investissements média hebdomadaires et leurs indicateurs
de performance, destinés à alimenter un modèle de *Marketing Mix Modeling*.

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

Génère `data/mmm.duckdb` avec trois tables :

| Table | Contenu |
|---|---|
| `media` | Investissements et performances média, par semaine / canal / entité |
| `kpi_compteurs` | L'indicateur cible, par semaine et par segment |
| `contexte` | Variables de contexte : prix, parts de marché, concurrence |

## Utilisation

```bash
python -m src.agent.cli "quel est le budget par canal ?"   # ligne de commande
streamlit run src/app/streamlit_app.py                     # interface web
```

## Architecture

```
src/
├── etl/      construction de la base depuis les fichiers sources
├── db/       connexion en lecture seule + validation des requêtes
├── agent/    prompt système, boucle agentique, outils
├── charts/   spécification de graphique + règles de lisibilité
└── app/      interface Streamlit (coquille mince, sans logique métier)
```

Le cœur expose `ask(question, history) -> AgentResponse`. L'interface n'est qu'une couche
de présentation : la remplacer ne touche pas au moteur.

## Tests

```bash
pytest tests/              # unitaires
pytest tests/eval/         # harnais d'évaluation (consomme des appels API)
```

## Conventions

- Les données sources, la base générée, le `.env` et les documents de travail sont **hors Git**.
- Aucune clé API dans le code : tout passe par les variables d'environnement.
- Le prompt système décrit les données et des principes généraux — jamais de règle
  spécifique à une question donnée.

# Text-to-SQL Agent — Media Analytics

Un agent conversationnel qui répond en langage naturel à des questions sur une base de données média. Il écrit le SQL, l'exécute, et rend le résultat en texte, tableau et graphique — avec le raisonnement à chaque étape.

Construit dans le cadre d'un projet de *Marketing Mix Modeling* : l'agent sert à **explorer et auditer les données avant modélisation**, pas à modéliser.

---

## Démonstration

> *« Comment s'est réparti le budget marketing entre les canaux l'année dernière ? »*

L'agent annonce ce qu'il cherche, écrit sa requête SQL, et rend :

- une **réponse rédigée** — périmètre explicité, réserves incluses (ex. : le SEO n'apparaît pas car son coût est `NULL`, non acheté et non pas gratuit)
- un **graphique**, choisi automatiquement par le code selon la forme du résultat, ou refusé avec son motif si aucune figure honnête n'est possible
- **chaque requête exécutée** — raisonnement pré-requête, tables réellement lues, SQL coloré, résultat

Les **questions pièges** font partie du contrat. Demander un classement des canaux « les plus performants » obtient un refus argumenté : GRP, clics et impressions ne se comparent pas, et rien dans ces données ne relie un canal à une vente.

---

## Stack

| Couche | Technologie |
|---|---|
| LLM | Claude (Anthropic) via API |
| Base de données | DuckDB |
| Backend | Python — FastAPI, Pandas |
| Frontend | React + Vite + TypeScript |
| Livraison | Docker |
| Tests | pytest, Vitest |

---

## Architecture

Le principe directeur : **ce qui doit être vrai à chaque fois ne peut pas dépendre du modèle**. Les bornes SQL, les règles de graphique, le calcul des totaux — tout ça est dans le code. Le prompt décrit le monde ; il n'arbitre rien de critique.

```
src/
├── etl/          construction de la base depuis les fichiers sources
│   ├── transforms.py     fonctions pures — aucun effet de bord
│   ├── checks.py         contrat de données : invariants bloquants + avertissements
│   └── build_db.py       orchestration, écriture atomique, CLI
├── db/
│   ├── connexion.py      ouverture durcie : lecture seule, accès réseau fermé
│   └── sql.py            point d'entrée unique — valide, borne, exécute
├── agent/
│   ├── boucle.py         ask(question, historique) → AgentResponse
│   └── prompt/           description des données : générée depuis la base + écrite
├── charts/               décide s'il y a un graphique, et lequel — fonctions pures
└── app/                  API HTTP — coquille mince, aucune logique métier

web/                      interface React + Vite + TypeScript
tests/
├── test_*.py             unitaires (~480 tests, 0 appel API)
└── eval/                 harnais d'évaluation en conditions réelles
```

**Trois propriétés vérifiées mécaniquement :**

- Aucun module n'ouvre la base hors de `src/db/connexion.py` — un test parcourt les sources pour le garantir
- La suite de tests ne consomme aucun appel API — un test fait échouer toute tentative
- L'observation ne change pas le résultat — `ask()` accepte un `trace=` optionnel, mais deux exécutions identiques, observée ou non, rendent la même réponse

---

## Fonctionnalités

**Boucle agentique**
Le modèle dispose d'un seul outil : exécuter une requête SQL de lecture. Il peut en enchaîner plusieurs par tour, se reprendre après une erreur SQL, et s'arrête quand il a de quoi répondre. Le dernier tour est toujours une réponse rédigée — le travail n'est jamais jeté.

**Accès SQL durci**
`run_sql` valide avec le parseur du moteur (jamais une regex), refuse tout ce qui n'est pas une lecture unique, borne lignes et temps d'exécution, annonce toute troncature. Il calcule aussi automatiquement la **somme des colonnes issues d'un `SUM()`** pour que le modèle n'additionne jamais de tête.

**Graphiques décidés par le code**
Quatre formes reconnues : série temporelle ou catégorielle (format large), pivot (format long), nuage de points (corrélation), histogramme. Le refus est un résultat de premier ordre — résultat vide, ligne unique, trop de catégories, unités incomparables : le module le dit, il ne force pas.

**Interface en temps réel**
Les étapes d'exécution sont diffusées au fil de l'eau (Server-Sent Events). Thème clair/sombre, graphiques interactifs (zoom, bascule courbe/barres/empilées), second axe automatique sur les séries d'ordres de grandeur éloignés.

**Chargement de données depuis l'interface**
Une page dédiée permet de téléverser les fichiers sources et de relancer la pipeline sans toucher au terminal. Le téléversement passe par un dossier d'attente promu seulement après une construction réussie — un fichier mal formé ne dégrade ni les sources ni la base.

---

## Lancer le projet

### Prérequis

- Python 3.11+
- Node.js 18+
- Une clé API Anthropic

### Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # renseigner ANTHROPIC_API_KEY
```

Déposer les fichiers sources dans `data/raw/`, puis :

```bash
python -m src.etl.build_db
```

### Lancement

```bash
./run.sh                      # développement — API + interface, rechargement à chaud
docker compose up --build     # production — une image, une seule adresse (:8000)
```

> ⚠️ Chaque question consomme des appels API facturés.

---

## Tests

```bash
pytest tests/ -q              # ~480 tests, ~10 s, 0 appel API
cd web && npm test            # tests interface
```

### Évaluation en conditions réelles

```bash
python -m tests.eval --a-blanc          # rejoue le cache : 0 appel, 0 $
python -m tests.eval --k 1              # ⚠ campagne facturée
```

Le cache est indexé sur tout ce qui peut changer une réponse (modèle, prompt, bornes, version de la boucle). **Toujours commencer à blanc** : les campagnes déjà payées se rejouent gratuitement.

---

## Déploiement

```bash
docker compose up --build
```

Une seule image sert l'API et l'interface statique. Les données et la clé API sont montées, jamais construites dans une couche — une image ne contient aucune donnée client. Le conteneur tourne sous un utilisateur non privilégié.

---

## Conventions

- **Français** partout — code, commentaires, commits
- Lignes ≤ 92 caractères
- Les commentaires expliquent **pourquoi**, pas quoi
- Aucune clé API dans le code — tout passe par les variables d'environnement

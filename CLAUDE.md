# Instructions projet

Agent conversationnel text-to-SQL sur des données média destinées à un futur modèle de
*Marketing Mix Modeling*. Le code a vocation à être repris par l'équipe du client : qualité
professionnelle exigée partout — fonctions pures, tests effectifs, journalisation,
invariants explicites.

## Avant de coder

**Lire `docs/decisions.md`** — le *pourquoi* de chaque choix structurant. Il évite de
rejouer des arbitrages déjà tranchés, et de refaire des mesures déjà faites (sa dernière
section liste des pièges de bibliothèque silencieux).

Les documents de travail détaillés — plan de réalisation, bons de travail par étape,
résultats de mesure — sont dans `docs/prive/`, **hors Git** (ils contiennent des chiffres
client). S'ils sont absents, `docs/decisions.md` suffit à comprendre le projet.

## Principes non négociables

**Décrire le monde, ne pas scripter les réponses.** Le client fournit une grille de
18 questions d'évaluation. C'est un instrument de *validation*, pas un cahier des charges.
Une règle de prompt qui mentionne un numéro de question, une valeur attendue ou un mot-clé
de la grille est mauvaise : elle fait passer ce cas et échouer le suivant. La reformuler en
propriété générale, ou la supprimer.

**Un échec n'autorise jamais une correction ciblée sur la question qui a échoué.** Il
autorise une hypothèse sur un défaut général — du schéma, de la description des données, de
la boucle — validée ensuite sur l'ensemble du corpus.

**Déterministe partout où c'est possible.** Ce qui doit être vrai à chaque fois ne peut pas
dépendre du modèle : un prompt est du texte, et le texte se contourne. On confie au modèle
ce qu'il fait bien (comprendre, écrire du SQL) et on verrouille dans le code tout le reste.

**Un test qui ne peut pas échouer ne prouve rien.** Chaque invariant, chaque garde est testé
dans les deux sens : il passe sur un cas sain, il **échoue** sur un cas délibérément
corrompu. Pour les protections, ajouter une contre-épreuve montrant que sans elles, ça
passerait.

**Préférer le simple et le général au malin.** Une propriété portée par les données est plus
robuste qu'une propriété portée par une phrase de prompt.

## Architecture

```
src/
├── etl/      construction de la base depuis les CSV sources
│   ├── transforms.py   fonctions pures — aucun fichier, aucune connexion, aucun log
│   ├── checks.py       contrat de données : invariants / avertissements / volumétrie
│   └── build_db.py     orchestration, écriture atomique, CLI
├── db/       seul accès à la base
│   ├── connexion.py    ouverture durcie (lecture seule + accès externe fermé)
│   └── sql.py          run_sql() : valide, borne, exécute. Point d'entrée unique.
├── agent/
│   └── prompt/         description des données : générée (schema.py) + écrite (*.md)
├── charts/   (à venir) spécification de graphique + règles de lisibilité
└── app/      (à venir) interface — coquille mince, aucune logique métier

tests/
├── test_*.py           unitaires
└── eval/               harnais : corpus, assertions, runner, chargeur de grille
```

**Le noyau est découplé de l'interface.** Le cœur exposera `ask(question, historique) ->
AgentResponse`. L'interface n'est qu'une couche de présentation : la remplacer ne doit pas
toucher au moteur. À respecter dès la première ligne — une logique métier installée dans le
fichier d'interface est très coûteuse à en extraire.

**Aucun module n'ouvre la base hors de `src/db/connexion.py`.** C'est vérifié
mécaniquement par `tests/test_db_point_unique.py` : toutes les protections de `sql.py`
reposent sur cette prémisse.

## Commandes

```bash
source .venv/bin/activate
python -m src.etl.build_db      # reconstruit data/mmm.duckdb depuis data/raw/
python -m pytest tests/ -q      # suite complète (~5 s, sans appel API)
```

Les tests ne consomment aucun appel API. Ceux qui en consommeraient (harnais d'évaluation
en conditions réelles) sont explicitement séparés.

## Conventions

- **Français** partout : code, commentaires, docstrings, messages, commits.
- **Commits sans trailer d'attribution** (`Co-Authored-By`) — l'auteur visible est Matthieu.
- **Lignes ≤ 92 caractères.**
- Les commentaires expliquent **pourquoi**, pas quoi. Un commentaire qui paraphrase le code
  est du bruit ; un commentaire qui justifie un choix non évident est ce qui rend le code
  reprenable.
- Les docstrings portent le raisonnement, pas seulement la signature.

## Données — les pièges à connaître

Trois tables, **deux périmètres** :

| Table | Périmètre |
|---|---|
| `media`, `kpi_compteurs` | l'annonceur seul |
| `contexte` | le marché, concurrents compris |

**`cost`, `grp` et `compteurs` existent des deux côtés**, avec des ordres de grandeur
voisins. Une somme entre tables produit un résultat faux mais crédible : c'est le principal
risque du jeu de données.

Autres propriétés structurelles : chaque canal n'a qu'une seule métrique de performance
(GRP, impressions ou clics, non comparables) · `cost` est NULL sur tout le SEO (non acheté,
pas gratuit) · aucune attribution possible d'un canal à une vente · aucune dimension
géographique ni démographique · les trois tables n'ont pas les mêmes bornes temporelles.

## Sécurité et confidentialité

- `data/raw/`, la base générée, `.env` et `docs/prive/` sont **hors Git**. Les jeux d'essai
  des tests font exception : ils sont inventés.
- Aucune donnée client, aucun chiffre réel, aucun nom d'entreprise dans le code versionné —
  y compris dans les commentaires et les exemples de docstring. Vérifier avant de committer.
- Aucune clé API dans le code : tout passe par les variables d'environnement.

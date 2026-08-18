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

**On ne corrige l'instrument que sur un défaut constaté, jamais anticipé.** Le dispositif
de mesure a produit, à lui seul, les quatre derniers défauts du projet — chacun né d'un
mécanisme ajouté au tour précédent pour couvrir le mécanisme d'avant. Avant de durcir une
assertion : la mesurer sur le cache d'évaluation, qui rend la vérification gratuite. Deux
fois sur deux, la mesure a contredit l'intuition et le « correctif » aurait dégradé
l'instrument. À défaut égal, préférer la correction qui **retire** du mécanisme.
`tests/eval` tient sous un budget de lignes exécutables, vérifié par
`tests/test_eval_budget.py`. Quand il est atteint, chercher l'assertion qui ne **peut
pas** échouer — et non celle qui n'a jamais échoué : mesuré, 9 familles d'assertions sur
11 sont dans ce second cas, dont celles qui gardent les vrais pièges du jeu de données.
Deux issues légitimes, retirer ce qui est inerte ou relever le plafond en écrivant le
motif à côté de la constante. Détail et mesures dans `docs/decisions.md`.

**Avant d'ajouter un réglage, regarder ce qui l'indexe.** Quatre défauts du dispositif
ont eu la même forme — une clé qui ne contient pas tout ce qui distingue ce qu'elle
indexe — et le symptôme est toujours un rapport plausible sans erreur. Tout ce qui peut
faire répondre autrement entre dans `agent_reel.empreinte_reglages`. Le recensement des
clés est dans `docs/decisions.md`.

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
└── app/      API HTTP — coquille mince, aucune logique métier
    ├── schemas.py        types de la frontière (distincts de ceux du noyau)
    ├── serialisation.py  Decimal, date, tuple -> JSON
    └── api.py            routes ; /api/question et /api/question/flux

web/          interface React + Vite + TypeScript (voir web/README.md)

tests/
├── test_*.py           unitaires
└── eval/               harnais : corpus, assertions, runner, chargeur de grille
```

**Le noyau est découplé de l'interface.** Le cœur expose `ask(question, historique) ->
AgentResponse`. L'interface n'est qu'une couche de présentation : la remplacer ne doit pas
toucher au moteur. À respecter dès la première ligne — une logique métier installée dans le
fichier d'interface est très coûteuse à en extraire.

`ask()` accepte un `trace=` optionnel, purement observationnel : il rapporte les étapes au
fil de l'eau à une interface qui doit montrer qu'elle travaille. Aucune décision de la
boucle n'en dépend, et `tests/test_agent_boucle.py` vérifie que deux exécutions identiques,
l'une observée et l'autre non, rendent la même réponse. Sans cette propriété, `trace=`
ouvrirait un second chemin d'exécution à couvrir partout.

**Une connexion par requête HTTP**, décidé à E9 : `interrupt()` de DuckDB porte sur la
connexion et non sur la requête, donc deux questions en vol sur une connexion partagée
s'interrompraient l'une l'autre. Le prompt et son empreinte restent partagés — les
regénérer par requête rouvrirait le risque d'instabilité du préfixe mis en cache.

**Aucun module n'ouvre la base hors de `src/db/connexion.py`.** C'est vérifié
mécaniquement par `tests/test_db_point_unique.py` : toutes les protections de `sql.py`
reposent sur cette prémisse.

## Commandes

```bash
source .venv/bin/activate
python -m src.etl.build_db      # reconstruit data/mmm.duckdb depuis data/raw/
python -m pytest tests/ -q      # suite complète (~8 s, sans appel API)
./lancer.sh                     # API + interface en développement (rechargement à chaud)
docker compose up --build       # le livrable : une image, une adresse
```

⚠ `./lancer.sh` et le conteneur **consomment de vrais appels facturés** à chaque question.
Le cache d'évaluation est un dispositif du harnais, pas du produit.

Les tests ne consomment aucun appel API. Ceux qui en consommeraient (harnais d'évaluation
en conditions réelles) sont explicitement séparés.

Le harnais est le **seul** endroit d'où partent des appels facturés, et il faut le vouloir :

```bash
python -m tests.eval --a-blanc                    # rejoue le cache, 0 appel, 0 $
python -m tests.eval --a-blanc --effort low       # une campagne précise du balayage
python -m tests.eval --a-blanc --campagne <empr.> # quand l'effort n'en désigne plus une
python -m tests.eval --k 1 --source corpus        # ⚠ campagne réelle, facturée
```

**Toujours commencer à blanc.** Les campagnes déjà payées sont en cache et se rejouent
gratuitement ; c'est ce qui permet de vérifier une hypothèse sur l'instrument avant de le
modifier. Séquence d'une nouvelle campagne : peuplement `--k 1` → itérations `--a-blanc`
→ campagne de référence.

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
  La règle se viole le plus facilement en *justifiant une mesure* : citer un montant réel
  pour illustrer un correctif est la fuite typique. Utiliser un chiffre inventé.
- Reste à trancher : `EDF` apparaît dans une question du corpus et dans une docstring de
  `transforms.py`, `Twitch` dans trois cas de test. Ce sont des valeurs du jeu de données,
  versionnées depuis les premiers commits. Anonymiser demanderait une purge d'historique —
  décision à prendre avant toute publication du dépôt.
- Aucune clé API dans le code : tout passe par les variables d'environnement.

## État au 18 août 2026 (interface)

Fait : socle · ETL et contrat de données (E1) · accès SQL unique et durci (E2) · prompt
système généré (E3) · boucle agent (E4) · harnais d'évaluation en conditions réelles (E5)
· balayage d'effort · deux relectures méthodologiques et leurs corrections.

La seconde relecture (18/08) a fermé la famille de défaut « clé d'indexation incomplète »
plutôt que ses cas : le scellé est ancré au corpus, les bornes de `run_sql` et la
description d'outil entrent dans l'empreinte de réglages, et le cache a été re-clé sans
repayer — ligne de base reproduite à l'identique.

**E9 (interface) a été avancé avant E7**, pour une démonstration client. React + Vite,
API FastAPI, étapes diffusées pendant que l'agent travaille, image Docker livrable. Les
réponses portent les lignes et colonnes brutes de chaque requête : E7 branchera ses
graphiques dessus sans retoucher la frontière.

**Prochaine étape : E7, les graphiques** — 8 des 18 questions du client en sont, et
l'agent répond aujourd'hui qu'il ne sait pas dessiner. Passe avant E6, qui est supprimé
(voir `docs/decisions.md`). Deux contraintes posées et à respecter : **E7 n'ajoute aucune
assertion au harnais** — le « Chart Check » est une colonne de notation manuelle — et
**E7 n'a pas de boucle de correction**, le modèle n'ayant produit aucune requête fautive
sur 171 exécutions.

Puis E8 (réglage), E9 (interface), E10 (observabilité), E11 (livraison). Le jeu de
contrôle sous scellé ne s'ouvre qu'à la fin d'E8, et il mesure une non-régression sur les
refus — pas une généralisation.

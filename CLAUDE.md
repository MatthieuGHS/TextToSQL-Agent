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
├── charts/   spécification de graphique — décide s'il y a un graphique, et lequel
│   └── specification.py  fonctions pures ; le refus est un résultat de premier ordre
└── app/      API HTTP — coquille mince, aucune logique métier
    ├── schemas.py        types de la frontière (distincts de ceux du noyau)
    ├── serialisation.py  Decimal, date, tuple -> JSON
    └── api.py            routes ; /api/question et /api/question/flux

web/          interface React + Vite + TypeScript (voir web/README.md)
              deux pages : conversation, et chargement des données sources

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
./run.sh                     # API + interface en développement (rechargement à chaud)
docker compose up --build       # le livrable : une image, une adresse
```

⚠ `./run.sh` et le conteneur **consomment de vrais appels facturés** à chaque question.
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
- Reste à trancher avant toute publication (portfolio compris) : des valeurs du jeu de
  données réel sont versionnées depuis les premiers commits — `EDF` et `Twitch` sont les
  exemples connus, mais le périmètre est plus large. Le **recensement exhaustif** (huit
  termes, avec fichiers et commits) et la **procédure de purge** (jeu de données inventé
  puis `git filter-repo`) sont tenus dans `docs/prive/confidentialite-recensement.md`,
  hors Git : versionner cette liste serait la fuite qu'elle recense. On n'anonymise pas
  avant la livraison — corpus et gardes ETL doivent viser les vraies valeurs tant que
  l'agent opère sur les vraies données.
- Aucune clé API dans le code : tout passe par les variables d'environnement.

## État au 24 août 2026 (retour client reçu, lots A à F arrêtés)

Fait : socle · ETL et contrat de données (E1) · accès SQL unique et durci (E2) · prompt
système généré (E3) · boucle agent (E4) · harnais d'évaluation en conditions réelles (E5)
· balayage d'effort · **trois** relectures méthodologiques et leurs corrections.

La seconde relecture (18/08) a fermé la famille de défaut « clé d'indexation incomplète »
plutôt que ses cas : le scellé est ancré au corpus, les bornes de `run_sql` et la
description d'outil entrent dans l'empreinte de réglages, et le cache a été re-clé sans
repayer — ligne de base reproduite à l'identique.

**E9 (interface) a été avancé avant E7**, pour une démonstration client. React + Vite,
API FastAPI, étapes diffusées pendant que l'agent travaille, image Docker livrable.

**Le chargement des données depuis l'interface est fait.** Une seconde page téléverse
les sources et relance la pipeline. Trois propriétés à ne pas casser : le téléversement
passe par un **dossier d'attente** promu seulement après une construction réussie — un
fichier mal formé ne dégrade ni `data/raw/` ni la base ; **seuls les quatre noms de la
pipeline sont acceptés**, ce qui ferme aussi la traversée de chemin ; et
`boucle.reinitialiser()` **oublie l'agent partagé** après un rechargement, sans quoi son
prompt continuerait de décrire l'ancien schéma sans rien lever.

**E7 (graphiques) est fait**, en option « le code décide » : `src/charts` lit la forme du
résultat de la dernière requête réussie **qui se trace** et en déduit le graphique. Le
modèle n'a ni outil de dessin ni spécification à produire — son SQL *est* l'expression de
son intention. Les règles de lisibilité, qui vivaient dans `principes.md` sans que rien ne
les applique, sont descendues dans le code. Les deux contraintes posées ont tenu : aucune
assertion ajoutée au harnais — `PasDeGraphiqueSurResultatVide` a seulement été rendue
vivante — et aucune boucle de correction.

**Relecture du 19/08, corrigée le jour même** (détail dans `docs/decisions.md`) :
le flux ne peut plus pendre (sentinelle posé en `finally` extérieur, règle à respecter
dans tout fil producteur) ; `sql.py` attend la mort effective du fil après `interrupt()`,
ce qui rend sûre la fermeture par requête d'E9 ; l'orchestration du rechargement vit dans
`src/etl/rechargement.py`, testable sans HTTP.

**Les graphiques couvrent trois formes** : large (une série par colonne), long (pivot —
une série par catégorie, gardes d'échelle incluses : la catégorie peut être un nom de
métrique), et nuage de points (deux mesures sans abscisse, la forme d'une corrélation).
Monotonie ordinale dans les deux sens, continuum affiché en croissant. Vérifié à blanc
sur les 171 exécutions en cache : aucune régression. Attention en rejouant à blanc : les
règles de `src/charts` ne sont dans aucune clé de cache, à dessein — voir le recensement
des clés dans `docs/decisions.md`.

**La ligne de base a été rejouée le 19/08** (rapport `2026-08-19-1710.md`, prompt
`56d615266615`, réglages `4f7a1f99217d`, séquence peuplement → à blanc → référence) :
**corpus 47/51, grille 15/18**, un seul tâtonnement SQL sur 69 exécutions. C'est la
référence des comparaisons d'E8. Ses échecs, tous lus dans les réponses réelles avant
d'être retenus, dessinent les pistes d'E8 — et aucun n'accuse l'instrument :

- **la somme faite de tête** (0/3 sur Twitch, plus deux questions de grille) : l'agent
  additionne de tête deux valeurs rendues par une requête au lieu de les faire sommer
  par SQL. `role.md` l'interdit déjà en clair ; c'est le défaut qu'E6 (supprimé) devait
  traiter par mécanisme et qu'E8 doit traiter par la description ;
- **le grain de `kpi_compteurs`** (2/3 instable + un `plafond_iterations` de grille) :
  la corrélation déjà consignée tient toujours ;
- une question de grille au libellé tronqué (« Evolution trafic SEA Est-ce que… ») finit
  en `plafond_iterations`. **Vérifié le 24/08 : la troncature est délibérée**, le xlsx la
  décrit comme un test de robustesse au langage interrompu et écrit le comportement
  attendu — comprendre l'intention, ou tracer SEO/SEA en demandant confirmation. L'échec
  est donc réel, mais il appelle une clarification et non un plafond plus haut.

**Graphiques étendus le 19/08 au soir** (détail dans `docs/decisions.md`) : second axe
décidé à l'étendue et non à la médiane, réglette de zoom, histogramme natif, « tracer à
la demande » sur chaque bloc de requête, bascules courbe/barres/empilées déclarées par
le serveur. Aucun de ces changements ne touche le prompt : la ligne de base tient.

## ⚠ Le constat du 25 août 2026 — la grille client ne bouge pas

| | ligne de base | B0 (5 tours) | B1 (prompt + outil) |
|---|---|---|---|
| corpus | 47/51 | 48/51 | 49/51 |
| **grille** | **15/18** | **15/18** | **15/18** |

Deux campagnes payées, **zéro point gagné sur l'instrument du client**. C'est le fait qui
prime sur la feuille de route ci-dessous.

**Le levier « mieux décrire le monde » a atteint son plafond, et c'est mesuré.** Tout ce
qui a été fait depuis E5 passe par le prompt. B1 en montre la limite : la somme faite de
tête recule de 0/3 à **1/3**, pas davantage. Une règle de prompt infléchit un
comportement, elle ne le garantit pas — ce que ce document dit depuis le début, et qui
prescrit la suite : *ce qui doit être vrai à chaque fois ne peut pas dépendre du modèle*.
**Ce principe n'a jamais été appliqué aux défauts qui restent.**

Les trois échecs de grille (rapport `2026-08-25-1821.md`) :

| Question | Assertion | Ce que c'est vraiment |
|---|---|---|
| Q5 · corrélation Search / ventes | `arrêt normal` | `plafond_iterations` — **l'agent ne rend rien**, cinq requêtes de travail jetées |
| Q17 · Top 3 des enseignements Social | `arrêt normal` | idem |
| Q15 · « Evolution trafic SEA » (tronquée exprès) | traçabilité | somme faite de tête |

**Deux sur trois ne sont pas des erreurs de raisonnement** : l'agent travaille, puis
l'utilisateur lit « Je n'ai pas abouti dans le nombre d'étapes imparti ». Tout est jeté.

⚠ **La grille est mesurée à k=1**, un tirage par question. `Crash Test` et `Robustesse`
ont basculé sans cause entre deux campagnes : à ce niveau de bruit, **la métrique qui
intéresse le client ne peut pas montrer un gain de un ou deux points**. `--k-grille 3` la
rendrait lisible, pour ~2 $ de plus par campagne.

**Ce qu'on ne veut plus** : des tours d'évaluation facturés qui mesurent sans améliorer.
La prochaine étape doit être une amélioration de l'agent, pas une mesure de plus.

## Feuille de route arrêtée le 24 août 2026, après le retour client

Le retour est **positif**. Quatre demandes — thème clair avec bascule, tables utilisées
par requête, paragraphe de raisonnement avant la requête, et validation de l'affichage du
SQL déjà en place — plus deux questions, sur le score au benchmark et sur le choix du
modèle. Le plan détaillé, avec les coûts et le raisonnement de chaque arbitrage, est dans
`docs/prive/plan-lots-A-a-F.md` ; ce qui suit en est la carte.

**Une seule des demandes touche le dispositif de mesure** : le paragraphe de raisonnement
passe par un champ ajouté à l'outil `run_sql`, donc par la description d'outil, qui est
dans `empreinte_reglages`. Il rejoint la campagne 1 d'E8 plutôt que d'ouvrir une seconde
péremption.

```
0 ──► A1 A2 A4 ──► A3 ──► B0 ──► B1 ──►(B2 si besoin)──► D ──► E ──► F
                     └──► C1…C6 ─────────────────────────────┘
```

Trois dépendances dures, le reste est libre : **A3 avant toute campagne sur un second
modèle**, **A1 avant la campagne de référence d'E8**, **C avant E11**.

- **Lot A — FAIT le 24/08. Socle, gratuit, aucune mesure périmée.** Quatre défauts
  constatés, tous reproduits avant d'être retenus (détail dans `docs/decisions.md`) : le
  pivot refusé sur l'ordre des colonnes (**A1** — la question 14 de la grille rend
  désormais les deux courbes que le client attend), deux tests qui ne prouvaient pas ce
  qu'ils annonçaient (**A2**), le registre des campagnes qui ne distingue pas les modèles
  (**A3** — préalable à toute comparaison Sonnet/Opus). Plus la demande 2, les **tables
  utilisées** par chaque requête (**A4**), lues par le parseur du moteur et non par une
  recherche de noms dans le texte ; la liste des tables réelles se lit sur la connexion et
  jamais en dur, sans quoi elle se périme au premier rechargement de données.
  Vérifié après coup : la ligne de base se rejoue à blanc à l'identique, **corpus 47/51,
  grille 15/18**, 69 exécutions toutes depuis le cache, zéro appel.
- **Lot B — E8, le seul lot facturé. B0 est FAIT le 24/08** (~4 $, rapport
  `2026-08-24-2140.md`) : à k comparable, **5 tours donnent corpus 48/51 · grille 15/18**
  contre 47/51 · 15/18 à 4 tours, pour un coût par question inchangé. Trois propriétés
  bougent — `grain distinct` passe de 2/3 instable à **3/3**, ce qui est exactement le
  défaut documenté pour lequel le plafond avait été relevé, et `Robustesse` de 2/3 à 3/3.
  La seule perte, `Crash Test` 3/3 → 2/3, n'est pas attribuable : **le plafond est
  invisible du modèle** (vérifié dans le texte exact qu'il reçoit), il ne peut donc pas le
  rendre plus explorateur. On garde 5, et `48/51 · 15/18` est la nouvelle référence.
  ⚠ Le peuplement à k=1 annonçait +16 % de coût : artefact du mélange corpus/grille à k
  différent, détaillé dans `docs/decisions.md`. **B1 est FAIT le 25/08** (~4 $, rapport `2026-08-25-1821.md`, prompt `12a0854db592`,
  réglages `7b0ab29b6987`) : les cinq modifications groupées donnent **corpus 49/51 ·
  grille 15/18** contre 48/51 · 15/18. **La somme faite de tête recule d'un tiers** — la
  propriété `valeur présente dans une autre colonne` passe de 0/3 à 1/3, et le mécanisme
  est vérifié dans le SQL réel : l'exécution qui passe lance une requête de plus, celle du
  total, quand les deux autres s'arrêtent à la ventilation et somment de tête. Progrès réel
  et limite claire : une règle de prompt infléchit, elle ne garantit pas. Le champ
  `raisonnement` coûte +5 % en entrée et +20 % en sortie, sans aucune réponse tronquée.
  **B2 ne sera pas payée** : elle départagerait deux causes d'un gain qu'on garde de toute
  façon. ⚠ Le balayage d'effort n'est plus rejouable — `low` et `high` datent de l'ancien
  prompt ; une décision d'effort demanderait de repayer les deux campagnes.
  Restent gratuits : l'ouverture du scellé et la notation manuelle des 18 questions.

- **Lot C — le thème clair par défaut, avec bascule.** Des **jetons sémantiques** dans
  `index.css`, définis deux fois, plutôt que 147 paires `dark:` — un seul endroit décide,
  les composants rendent, comme `src/charts` le fait déjà. Trois pièges vérifiés dans le
  code : `Graphique` est mémoïsé et resterait figé dans l'ancienne palette (d'où un
  contexte, qui traverse `memo`), Recharts veut des couleurs littérales et non des
  classes, et sans script d'amorçage dans `index.html` le rechargement flashe en blanc.
  Deux palettes à re-dériver, pas une : les 12 séries de `Graphique.tsx` **et** les
  9 jetons Prism de `SqlColore.tsx`.
- **Lot D — E10** : consolidation d'observabilité (coût par question en production, taux
  de cache, arrêts anormaux) — mince, l'essentiel existe.
- **Lot E — E11** : image Docker reconstruite et vérifiée (pas rebâtie depuis les
  changements web), vérification visuelle complète de l'interface (jamais faite, et
  désormais sur les deux thèmes), doc de reprise pour l'équipe client, recette sur la
  grille.
- **Lot F — post-livraison, portfolio** : procédure de purge déjà écrite dans
  `docs/prive/confidentialite-recensement.md`. La vidéo de démonstration montre des
  données réelles — elle est à refilmer sur le jeu inventé, au même titre que la purge
  du dépôt.

Le jeu de contrôle sous scellé ne s'ouvre qu'à la fin d'E8, et il mesure une
non-régression sur les refus — pas une généralisation.

**Hors plan tant qu'il n'a pas tranché : le choix du modèle.** `boucle.MODELE` est une
constante de module ; l'effort est paramétrable, le modèle non. Aucune comparaison
Sonnet/Opus n'existe — les trois campagnes sont sur Sonnet. La rendre possible coûte
~5 lignes en miroir de `--effort`, **et A3 en est le préalable**.

## Démonstration client du 20 août 2026

Filmée sur six questions (`QDemo.txt`, hors Git), du panorama des canaux au refus
pédagogique, avec chargement de données. **L'interface a été vue en conditions réelles
pour la première fois** — et c'est ce qui a produit les quatre correctifs du 19/08 au
soir, dont aucun n'était sorti des tests ni du rejeu à blanc.

Ce que la démonstration a validé : trois formes de graphique, double axe, zoom,
bascules, conversation multi-tours, refus expliqués, et une réponse qui distingue
`mes` (raccordements neufs) de `cdf` (changements de fournisseur) pour expliquer
pourquoi le premier n'est pas le bon indicateur publicitaire.

Ce qu'elle a révélé, et qui part en campagne 1 d'E8 : les deux manques de description
du monde décrits plus haut. Rien d'autre n'a été relevé.

**Le retour est arrivé le 24/08 et il est positif** ; il est consigné dans la feuille de
route ci-dessus et détaillé dans `docs/prive/plan-lots-A-a-F.md`.

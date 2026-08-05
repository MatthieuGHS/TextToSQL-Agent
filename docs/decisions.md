# Décisions de conception

Le *pourquoi* des choix structurants du projet. Ce document est versionné et ne contient
aucune valeur issue des données client : ni montant, ni volumétrie, ni nom de marque. Les
documents de travail qui en contiennent vivent dans `docs/prive/`, hors Git.

Chaque décision est datée du moment où elle a été prise, avec l'alternative écartée. Une
décision qu'on ne sait pas justifier est une décision à rejouer.

---

## Données et stockage

### DuckDB plutôt que SQLite

SQLite ne fournit ni `CORR()`, ni `STDDEV()`, ni `QUARTER()`. Un agent qui traduit des
questions d'analyse en SQL a besoin de ces fonctions — les émuler dans le prompt reviendrait
à apprendre au modèle à contourner l'outil plutôt qu'à s'en servir. DuckDB est par ailleurs
orienté colonne, ce qui convient à des agrégations sur un historique hebdomadaire.

*Écarté :* SQLite (fonctions manquantes), PostgreSQL (un serveur à administrer pour une
base de quelques mégaoctets).

### Trois tables aux grains distincts

Les fichiers sources se recoupent partiellement. Les fondre en une table unique imposerait
soit des colonnes majoritairement nulles, soit des jointures produisant un produit
cartésien silencieux. Trois tables aux grains séparés — activité média, indicateur cible,
variables d'environnement — rendent le grain lisible dans le schéma, donc dans le prompt.

Conséquence assumée : toute question croisant deux grains exige une jointure explicite sur
la semaine. C'est une contrainte, et elle est saine : elle rend visible une opération qui,
autrement, se ferait de travers sans prévenir.

### Format large pour l'indicateur cible, long pour l'environnement

Asymétrie volontaire.

**Large** quand les mesures sont peu nombreuses, stables et sémantiquement distinctes : des
noms de colonnes explicites rendent le schéma auto-descriptif, et le modèle n'a pas à
connaître une chaîne exacte à placer dans un `WHERE`.

**Long** quand les variables sont nombreuses et varient selon des dimensions qu'on veut
filtrer et grouper : comparer deux marques devient un `GROUP BY` générique au lieu d'exiger
de nommer deux colonnes précises.

La règle n'est donc pas « long » ou « large » mais : *le format suit la façon dont on
interroge*.

### Conserver une colonne constante quand elle porte le périmètre

Une colonne dont toutes les lignes valent la même chose ne porte aucune information
statistique. Elle peut porter autre chose : le **périmètre** de la table — ce que
l'ensemble des lignes décrit.

C'est le cas de l'identifiant d'annonceur dans la table d'activité média. Sans lui, rien ne
distingue les investissements suivis de ceux des concurrents, qui figurent dans la table
d'environnement avec des ordres de grandeur voisins — une somme erronée n'aurait alors rien
d'aberrant à l'œil. Un invariant garantit l'unicité de la valeur.

Corollaire général : une propriété portée par les données est plus robuste qu'une propriété
portée par une phrase de prompt. Elle survit à une réécriture du prompt, elle est visible
d'un `SELECT *`, et elle ne coûte pas de tokens à chaque question.

### N'éclater une colonne que là où la hiérarchie existe dans la source

Une colonne de la source encode deux formes dans le même champ : une forme hiérarchique à
plusieurs niveaux, et une forme plate. Seule la première est éclatée ; la seconde laisse les
colonnes dérivées nulles et reste lisible dans la colonne d'origine, conservée telle quelle.

Faire retomber les valeurs plates dans la première colonne dérivée ferait cohabiter des
notions sans rapport dans un champ censé n'en porter qu'une. On décrit la source telle
qu'elle est plutôt que de lui inventer une structure.

*Bénéfice second :* la colonne d'origine étant conservée, l'éclatement reste vérifiable
a posteriori — on peut contrôler en SQL qu'aucun niveau n'a été perdu.

---

## Contrat de données

### Trois niveaux : invariant, avertissement, volumétrie

| Niveau | Signification | Effet |
|---|---|---|
| **Invariant** | doit être vrai quelle que soit la version des données ; une violation signifie que notre transformation est fausse, ou que la source a changé de nature | l'ETL s'arrête |
| **Avertissement** | particularité connue du jeu de données, ou vocabulaire inattendu | journalisé |
| **Volumétrie** | information | journalisée |

La confusion des trois est le piège classique. Asserter un nombre de lignes ou un total
transforme un rafraîchissement de données légitime en panne de pipeline. À l'inverse,
ravaler une incohérence structurelle au rang d'avertissement produit une base
silencieusement fausse, dont l'erreur ne se manifestera qu'en aval, sous forme de réponses
fausses.

Test qui tranche : *« si cette propriété devenait fausse, est-ce que ça voudrait dire que
mon code est cassé, ou que les données ont changé ? »* Le premier cas est un invariant, le
second un avertissement.

### Les particularités des données ne se corrigent pas

Les anomalies métier — lignes à coût nul mais performance positive, segments jamais activés,
canaux à diffusion intermittente — sont journalisées, jamais nettoyées. Ce sont précisément
les choses que l'agent doit savoir détecter ; les corriger dans l'ETL les rendrait
invisibles.

Seules sont retirées les lignes **sans segment identifiable**, qui ne décrivent aucune
observation et pollueraient les valeurs distinctes servant à décrire le schéma au modèle.

### Vérifier ce qu'on affirme

Une hypothèse écrite en commentaire n'est pas une garantie. Le pipeline ne lit qu'une partie
des fichiers sources, au motif que les autres les recoupent : ce motif est vérifié à chaque
construction, pas supposé.

Le contrôle déduit ses attentes de la base produite plutôt que de les énumérer dans une
table écrite en dur — il reste juste si le schéma évolue.

### Écriture atomique

La base est écrite dans un fichier temporaire, validée, puis renommée. Un échec en cours de
route laisse la base précédente intacte au lieu de la remplacer par une base tronquée. Le
renommage n'intervient qu'après validation complète du contrat.

---

## Code

### Un module de transformations sans effet de bord

Les transformations sont des fonctions pures : elles prennent un tableau et en renvoient un
autre, sans lire de fichier, ouvrir de connexion ni écrire de journal. Elles sont donc
testables sur quelques lignes inventées, en quelques millisecondes, sans base ni fichier
source.

Cette propriété est structurante : quand un contrôle a besoin de journaliser, il est écrit
ailleurs — dans le module de contrôles, sur la base produite — plutôt que d'introduire un
effet de bord ici.

### Tester les filets dans les deux sens

Un filet de sécurité qu'on n'a jamais vu attraper quoi que ce soit n'est pas un filet.
Chaque invariant est testé deux fois : il passe sur une base saine, et il **échoue** sur une
base délibérément corrompue.

Contrôle occasionnel utile : neutraliser volontairement un contrôle et vérifier que le test
correspondant échoue. Un test qui reste vert dans ce cas ne prouve rien.

---

## Agent

### Noyau découplé de l'interface

Le cœur expose une fonction unique `ask(question, historique) -> réponse structurée`.
L'interface n'est qu'une couche de présentation. Passer d'une interface locale à une API et
un front-end doit revenir à réécrire la coquille, jamais le moteur.

À respecter dès la première ligne : une logique métier qui s'installe dans le fichier
d'interface est très coûteuse à en extraire ensuite.

### Un outil unique plutôt qu'une boîte à outils générique

La boîte à outils SQL fournie par défaut par le cadriciel enchaîne plusieurs appels API
pour découvrir le schéma à chaque question. Un outil unique d'exécution SQL, avec le schéma
injecté dans le prompt système et mis en cache, ramène le coût à un appel.

Contrainte du projet : maîtriser la consommation de tokens. Le schéma étant stable, il
appartient au préfixe mis en cache ; le redécouvrir à chaque question serait le repayer.

### Décrire le monde, ne pas scripter les réponses

Le prompt système décrit les données — schéma, valeurs des colonnes à faible cardinalité,
sémantique métier, période couverte — et des principes de raisonnement généraux. Il ne
contient **aucune règle attachée à une question particulière**.

Test qui tranche : si une règle du prompt mentionne un numéro de question, une valeur précise
ou un mot-clé d'un jeu d'évaluation, elle est mauvaise. La reformuler en propriété générale,
ou la supprimer.

Ce n'est pas un scrupule mais un enjeu de qualité : un jeu d'évaluation est un échantillon
de **validation**, pas d'entraînement. Régler le prompt question par question donne un score
parfait et un agent qui s'effondre sur la question suivante — celle que l'utilisateur posera.

### L'évaluation est un instrument de mesure

On ne règle jamais l'instrument. Un échec sur une question n'autorise pas une modification
qui mentionne cette question ; il autorise une hypothèse sur un défaut général, validée
ensuite sur l'ensemble du corpus.

Le corpus d'évaluation se dérive du **modèle de données** — une question par propriété
structurelle : grain, cardinalité, unité, borne temporelle, homonymie entre tables. Ainsi
construit, il existe indépendamment de tout jeu de questions fourni de l'extérieur, qui n'en
devient qu'un échantillon.

Deux exigences de mesure qui en découlent : rejouer chaque question plusieurs fois (la
génération est stochastique, un score sur un tirage n'est pas une mesure) et épingler
l'identifiant exact du modèle dans chaque rapport (les alias évoluent, et deux mesures prises
sous le même alias à des dates différentes ne sont pas comparables).

---

## Vérifications faites, à ne pas refaire de mémoire

Ces points ont été mesurés sur la version des bibliothèques figée dans
`requirements.lock.txt`. Ils sont notés ici parce que ce sont des pièges silencieux : ils ne
lèvent aucune erreur.

- Le comptage de tokens du connecteur **ignore le prompt système** lorsque celui-ci est
  fourni sous forme de blocs de contenu — or c'est la forme requise pour y poser un marqueur
  de mise en cache. Compter avec un prompt en chaîne simple, ou appeler le SDK directement.
- Le comptage refuse une requête sans message utilisateur : un prompt système seul part dans
  un champ dédié et laisse la liste des messages vide.
- Le compteur d'écritures de cache exposé par le connecteur reste à zéro alors que
  l'écriture a bien lieu ; la valeur réelle est publiée sous une autre clé.
- Le champ « tokens d'entrée » n'a pas le même sens dans le connecteur (total) et dans
  l'API brute (reste non caché). Les mélanger fausse tout calcul de coût.
- Le connecteur n'envoie pas de configuration de raisonnement par défaut : le défaut de
  l'API s'applique, raisonnement adaptatif compris. C'est un poste de coût qui n'apparaît
  dans aucune ligne de code.

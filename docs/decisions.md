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

**La règle s'applique aux tests eux-mêmes, et c'est là qu'on l'oublie.** Trois exemples
trouvés en relecture, tous dans `tests/` : un test de déterminisme qui comparait deux appels
dans le même processus — il aurait passé sans le tri qu'il prétendait garder ; une assertion
sur une volumétrie écrite en dur, qui fait d'un rafraîchissement légitime une panne de suite
de tests ; un raisonnement de majoration pris à l'envers, dont la conclusion était juste par
accident. L'attention se porte naturellement sur le code testé, pas sur le test. Le harnais
d'évaluation étant lui aussi du code de test — et celui qui produira les chiffres montrés au
client — la vigilance doit y être la même.

---

## Accès à la base

### Le moindre privilège plutôt que le filtrage

Ouvrir la base en lecture seule protège **les données**, pas **la machine** : le SQL peut
encore écrire un fichier sur le disque, en lire un, énumérer un répertoire, installer une
extension qui ouvrirait ensuite le réseau. Quatre échappatoires mesurées, toutes passantes.

Le réglage qui les ferme agit au niveau du moteur, à l'ouverture de la connexion, et n'est
pas désactivable depuis le SQL. C'est ce qui le distingue d'un filtre : on ne cherche pas à
reconnaître ce qui est mauvais, on retire la capacité. Une liste de mots-clés interdits
aurait donné une fausse impression d'exhaustivité — on n'y pense jamais tous.

Cette distinction vaut d'être tenue : *garde-fou* = inspecter et décider, avec un problème
de couverture ; *moindre privilège* = ce qui n'est pas accordé est impossible, y compris ce
à quoi on n'a pas pensé.

### Valider avec le parseur du moteur, pas avec une expression régulière

Une expression régulière se fait berner dans les deux sens : elle laisse passer un
`DROP` précédé d'un commentaire, et refuse un mot-clé présent dans une chaîne littérale. Le
parseur du moteur découpe le texte en instructions et donne leur type — c'est exactement le
code qui exécutera la requête ensuite.

La validation est une **liste blanche** : un seul type d'instruction est autorisé. Elle
hérite ainsi de la propriété du moindre privilège — ce qui n'est pas explicitement permis
est refusé.

### Borner en enveloppant, et annoncer la troncature

Ajouter une clause de limite à la fin d'une requête casse dès qu'elle en contient déjà une,
ou qu'elle se termine par un tri dans une sous-requête. L'envelopper comme sous-requête
fonctionne toujours : toute requête valide est une source de données valide.

L'enveloppe met la requête **sur sa propre ligne**. Ce détail n'en est pas un : un modèle
termine souvent son SQL par un commentaire `-- …`, et sans saut de ligne la parenthèse
fermante et la clause de limite se retrouvent commentées. La requête du modèle était
correcte, l'erreur renvoyée parlait de syntaxe, et rien dans le message ne permettait de
comprendre. Un défaut de ce genre ne se voit pas en relisant : il se voit en exécutant ce
que le modèle écrit réellement.

On demande une ligne de plus que la limite, ce qui permet de détecter la troncature — et
elle est **annoncée dans le résultat**. Une troncature silencieuse est pire qu'une erreur :
le modèle croirait avoir tout vu et énoncerait un total faux avec assurance.

### Deux bornes sur un résultat : ce qu'il contient, ce qu'il coûte

Le plafond en lignes borne ce que la base renvoie. Il ne dit rien du prix : deux cents
lignes de deux colonnes et deux cents lignes de douze ne pèsent pas la même chose, et ce
poids-là est payé à chaque question, dans la partie du contexte que le cache ne rattrape
pas. Le rendu est donc borné une seconde fois, en caractères, et l'omission est annoncée
au même titre que la troncature.

### Les messages d'erreur font partie du produit

L'agent relit l'erreur pour corriger sa requête et réessayer. Un message inexploitable
transforme une erreur récupérable en échec. Une colonne inconnue renvoie donc la liste
réelle des colonnes de la table visée, et un refus dit ce qui est permis.

Deux règles de rédaction : ne jamais expliquer *pourquoi* en termes de sécurité — ça
apprendrait à contourner ; et ne jamais montrer au modèle une requête qu'il n'a pas écrite —
l'enveloppe de plafonnement est retirée des extraits cités, sinon il chercherait à corriger
une clause qui n'est pas de lui.

**Troisième règle, apprise en relecture : un indice faux est pire qu'un indice absent.** Le
moteur nomme l'*alias* fautif, pas la table (`Table "c" does not have a column named …`).
Détailler une seule des tables citées revenait donc à tirer au sort, et le message obtenu
envoyait le modèle chercher la colonne dans la mauvaise table — sur une jointure, c'est-à-
dire précisément là où il a besoin d'aide. Toutes les tables citées sont détaillées :
leur nombre est borné par celui des tables de la base, et deux listes valent mieux qu'une
fausse.

## Agent

### Générer la description des données, écrire ce qu'elles signifient

Un prompt écrit à la main énonce des faits — liste des valeurs possibles, bornes
temporelles, métriques existantes — qui deviennent faux au premier rafraîchissement de
l'extrait. L'agent affirmerait alors, de bonne foi, qu'une valeur existe alors qu'elle a
disparu.

Ligne de partage : **tout ce qu'une requête SQL peut établir est généré depuis la base** ;
ce que les données *signifient*, et ce qu'elles ne permettent pas, est écrit à la main —
aucune requête ne le dira.

Bénéfice second, inhabituel pour un prompt : il devient **vérifiable contre sa source**. Les
tests contrôlent que chaque valeur énoncée existe et que chaque valeur de la base est
énoncée. Ils échouent au lendemain d'un rafraîchissement, avant que l'agent ne se mette à
mentir.

Deux précautions, apprises en relecture, sans lesquelles cette garantie est fictive :

**Ces tests portent sur la partie générée seule.** Cherchée dans le prompt entier, une
valeur se trouve aussi dans une phrase écrite à la main — et le test passe au vert
précisément quand la génération a cessé de faire son travail.

**Les affirmations écrites qu'une requête peut trancher ont leurs propres tests.** La
frontière généré / écrit sépare deux origines, pas deux niveaux d'exigence : « un seul canal
est *owned*, d'où son coût nul » est une phrase de prompt, mais c'est un fait sur les
données, et un extrait futur peut la rendre fausse en silence. Ce qu'on ne peut pas générer,
on le garde par un test.

**Le classement lui-même est une affirmation.** Annoncer qu'une colonne « a trop de valeurs
pour être listée » sans compter ses valeurs, c'est écrire à la main dans un module dont la
raison d'être est de générer. La règle est donc que le seuil décide seul de ce qui bascule
d'un groupe à l'autre, et qu'une colonne écartée pour une *autre* raison que le volume
l'annonce sous cette autre raison — sans quoi le prompt donne au modèle un motif faux, et un
extrait plus étroit le rendrait faux en silence. Corollaire : la partie écrite à la main ne
nomme plus ces colonnes ; elle renvoie à ce que la section générée signale.

Il reste des affirmations que ni l'un ni l'autre n'atteint — le sens d'un GRP, ce que le jeu
de données ne permet pas. Celles-là ne périment pas avec les données : elles se relisent.

Le texte écrit vit dans des fichiers Markdown hors du code : il se relit et se corrige sans
toucher au langage de programmation, et un diff reste lisible.

### La génération doit être déterministe, sous peine de perdre le cache

Le prompt est le préfixe mis en cache, et le cache est une correspondance d'octets : une
seule différence, et tout est recalculé au prix fort — sans erreur, sans avertissement.

Or l'ordre des lignes d'une requête n'est pas garanti sans tri explicite. Trois règles :
trier toute énumération, n'introduire aucun horodatage ni identifiant variable, et
**tester** que deux constructions successives produisent les mêmes octets. C'est le seul
moyen de s'en apercevoir avant la facture.

### Les exemples enseignent une forme, jamais un contenu

Quelques exemples question → requête valent mieux qu'un paragraphe pour montrer une
convention. C'est aussi l'endroit où le sur-apprentissage entre sans se voir : personne ne
remarque qu'un exemple ressemble à une question du jeu d'évaluation.

Constat fait en les écrivant : les exemples « évidents » **sont** les questions de la
grille. Les premières idées venues correspondaient à trois d'entre elles. La grille se relit
donc pour s'en écarter, pas pour s'en inspirer, et les contenus retenus sont délibérément
orthogonaux.

Chaque exemple s'exécute réellement et renvoie des lignes — un exemple faux enseigne une
erreur, un exemple vide enseigne le doute. Les deux sont testés.

### Noyau découplé de l'interface

Le cœur expose une fonction unique `ask(question, historique) -> réponse structurée`.
L'interface n'est qu'une couche de présentation. Passer d'une interface locale à une API et
un front-end doit revenir à réécrire la coquille, jamais le moteur.

À respecter dès la première ligne : une logique métier qui s'installe dans le fichier
d'interface est très coûteuse à en extraire ensuite.

### Une réponse coupée n'est pas une réponse

Le plafond de tokens de sortie est partagé avec le raisonnement : une réponse peut s'arrêter
en pleine phrase, ou en plein appel d'outil. Rien ne la distingue alors d'une réponse
terminée, sinon un motif d'arrêt renvoyé par l'API — le texte, lui, paraît complet.

Elle a donc son propre motif d'arrêt, et il est classé **anormal**. C'est le sens de la
mesure qui l'impose : compter une réponse tronquée comme un succès fausserait le score dans
le sens flatteur, celui qu'on ne va pas vérifier. Le texte partiel est conservé — il n'y a
pas de raison de punir l'utilisateur d'une limite qui est la nôtre — mais l'avertissement
vient en dernier, là où la lecture s'arrête.

Cas général derrière ce cas particulier : **tout état que l'API distingue et que la boucle
confond devient une erreur de mesure silencieuse.** Un motif d'arrêt inconnu doit sortir de
la boucle sous son propre nom, pas se fondre dans le chemin nominal.

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

### L'ordre d'une campagne est la méthode, pas un détail d'exécution

Les assertions sont écrites **avant** l'agent, pour qu'aucune ne se calque sur une sortie
observée. Le revers est qu'elles n'ont jamais rien vu de réel : elles seront fausses
plusieurs fois avant d'être justes. D'où trois passes, dans cet ordre.

1. **Peuplement** — une répétition, corpus seul. On paie une fois de vraies réponses, et on
   les archive.
2. **À blanc** — les assertions se corrigent en rejouant l'archive, sans un seul appel.
3. **Référence** — la campagne complète, qui devient la ligne de base.

**Entre 2 et 3, on ne touche ni au prompt ni à l'agent.** Sinon la ligne de base mesure un
système qu'on a bougé pendant qu'on le regardait, et toutes les comparaisons d'E8 partent
faussées. Corollaire : en passe 2, un échec n'autorise une correction que sur l'assertion,
jamais sur ce qu'elle mesure.

Le mode à blanc n'est pas une intention mais une propriété : l'agent y est remplacé par un
objet qui porte les clés du cache et **lève si on l'appelle**. Une exécution absente du
cache est ignorée ; elle ne peut pas partir en appel.

### Trois choses que le score ne doit pas compter

Un harnais mesure ce qu'on lui donne à mesurer. Trois exclusions décidées avant la première
campagne, chacune parce qu'inclure fausserait la mesure dans un sens identifiable.

**Les requêtes en échec ne sont pas transmises aux assertions.** Une requête fautive suivie
d'une requête juste est une reprise — le comportement pour lequel la boucle a été écrite.
La transmettre ferait échouer le contrôle d'exécutabilité sur une auto-correction réussie.
Leur nombre part dans le rapport comme indicateur de clarté du schéma, pas comme faute.

**Une panne du service est écartée, pas comptée en échec.** Sinon le score varie avec la
météo du réseau, et deux campagnes prises à deux moments cessent d'être comparables. Rien
n'est mis en cache non plus : une panne figée dans l'archive se rejouerait indéfiniment.

**Une réponse produite après un abandon de la boucle n'est pas une réponse.** Ni le texte ni
le SQL ne le disent — une réponse coupée au plafond de sortie paraît complète. Le motif
d'arrêt voyage donc jusqu'aux assertions, et tout arrêt anormal est un échec.

### Un échec du harnais accuse d'abord le harnais

Première confrontation des assertions à de vraies réponses : **sur onze échecs, huit
venaient de l'instrument, pas de l'agent**. Et toujours dans le même sens — les contrôles
condamnaient les *meilleures* réponses.

C'est un résultat structurel, pas un accident de rédaction. Les assertions sont écrites
avant l'agent, délibérément, pour ne pas se calquer sur ce qu'il produit ; elles sont donc
écrites contre une idée de la réponse, et l'idée est plus étroite que le réel.

**Un faux positif ne fait pas que du bruit** : il désigne un défaut qui n'existe pas, et
envoie le réglage corriger le prompt là où l'agent travaillait bien. Un instrument trop
sévère est plus dangereux qu'un instrument absent, parce qu'il a l'air de fonctionner.

Le symétrique existe aussi. Après correction, l'un des jeux est monté à 100 % — sur une
seule assertion devenue incapable d'échouer. Il a fallu lui rendre un contrôle qui puisse
mordre pour retomber sur le vrai chiffre. **Un score qui monte après qu'on a touché à
l'instrument doit être regardé avec suspicion, pas avec satisfaction.**

Quatre corrections en sont sorties, toutes générales.

**Ne rien requêter n'est pas une faute.** Une bonne part des questions d'un jeu
d'évaluation sont des pièges : une dimension qui n'existe pas, une valeur mal nommée, une
grandeur que les données ne permettent pas d'établir. La bonne réponse est alors un refus
expliqué, sans requête — ce que la boucle bénit déjà comme un arrêt normal. Le contrôle
d'exécutabilité ne juge donc que ce qui a été écrit ; l'exigence inverse existe séparément,
et se pose cas par cas, là où la question réclame vraiment d'interroger la base.

**Un contrôle sur le vocabulaire ne distingue pas l'affirmation de sa réfutation.** On ne
peut pas interdire à l'agent de nommer une grandeur qu'on lui demande précisément
d'expliquer comme non calculable. La liste de mots interdits a donc disparu des contrôles
universels ; la propriété qu'elle visait est mieux tenue par la traçabilité, puisqu'un
chiffre qu'aucune requête ne peut produire n'a aucune source. *Une propriété portée par les
données plutôt que par une liste de mots* — la même règle que pour le prompt.

Corollaire de rédaction : chercher une sous-chaîne pour **exiger** un mot est utile, pour
l'**interdire** c'est dangereux. Une occurrence en trop rend le premier plus indulgent et le
second faux.

**La traçabilité a trois sources, pas une.** Une valeur calculée par une requête ; un nombre
qui figure littéralement dans un résultat — les dates en sortaient, alors qu'une réponse qui
date son périmètre ne fait qu'obéir au prompt ; et un chiffre annoncé par la description
générée des données, qu'il serait absurde de faire requêter à nouveau.

**L'écriture déclare sa propre précision.** Un coefficient rendu « 0,06 » affirme une valeur
entre 0,055 et 0,065 : le juger à quelques pourcents *relatifs* réclame une exactitude que
son auteur n'a pas revendiquée. On accepte donc aussi une valeur qui, arrondie au nombre de
décimales écrites, redonne le nombre écrit. Ce qui reste attrapé — et doit le rester — est
le chiffre qu'aucune requête n'a produit : un total fait de tête, un ordre de grandeur lu de
loin.

Règle de méthode qui va avec : **quand on assouplit un contrôle, on ajoute la contre-épreuve
dans le même geste**, un cas réel qui doit rester rouge. Et on refuse d'assouplir pour un
seul nombre : un écart isolé au-delà de la tolérance reste un échec, parce qu'inventer une
règle pour lui serait du réglage sur l'instrument. S'il se répète, il devient un signal.

### La clé du cache porte tout ce qui change une réponse

Modèle, prompt, **réglages**, question, numéro de répétition. La règle qui décide de son
contenu : *si ça peut faire répondre autrement, ça y est ; si ça ne le peut pas, ça n'y est
pas* — un réglage sans effet dans la clé ferait repayer une campagne pour rien.

L'oubli des réglages a été un vrai défaut, et instructif par sa forme. Effort de
raisonnement et plafonds de boucle n'apparaissent ni dans l'identifiant du modèle ni dans le
prompt ; ce sont pourtant les premières choses qu'on fait varier. Relancer sous un réglage
différent resservait donc les réponses de l'ancien : deux rapports identiques, et la
conclusion « le réglage ne change rien » énoncée avec assurance. Sans erreur, sans
avertissement — la famille d'échec que ce document existe pour recenser.

### L'instrument est devenu la principale source de défauts

Relevé le 14/08/2026, en relisant E1 à E5 avec une seule question : *est-ce qu'on se perd
en complexité ?*

```
noyau         ETL + accès base + agent .......................... 1 818 lignes
instrument    tests/eval 1 498 + ses propres tests 920 .......... 2 418 lignes

sur 171 exécutions réelles :
   tâtonnements SQL ...................................... 0
   motifs d'arrêt empruntés .............................. 2 sur 6
```

L'instrument pèse 1,3 fois ce qu'il mesure, pour 22 questions. Et ses quatre derniers
défauts — clé de cache incomplète, registre écrasé, scellé recalculé, sélection de
campagne ambiguë — viennent **tous** d'un mécanisme ajouté au tour précédent pour couvrir
le mécanisme d'avant. Trois tours, trois défauts silencieux, aucune question d'évaluation
gagnée.

Deux distinctions permettent de ne pas en tirer la mauvaise conclusion.

**Une protection qui ne se déclenche pas n'est pas du gaspillage.** Quatre des six motifs
d'arrêt de la boucle n'ont jamais été empruntés — erreur d'API, refus du modèle, réponse
tronquée, trop d'échecs SQL. Ils coûtent quelques lignes chacun et empêchent qu'un refus
soit un jour compté comme un succès. Ils restent.

**Un confort de récupération qui ne sert jamais, si.** Le modèle écrit **zéro requête
fautive sur 171 exécutions**. Les quelque 70 lignes qui reconstruisent un message d'erreur
exploitable — dé-enveloppement de l'erreur DuckDB, liste des colonnes de toutes les tables
citées — n'ont donc jamais été exécutées en conditions réelles. Rien ne casse sans elles :
la requête échouerait, simplement. Elles ne sont pas supprimées — elles sont écrites,
testées, et un rafraîchissement des données peut les réveiller — mais **on cesse d'y
investir**, et on n'en construit pas l'équivalent pour le graphique en E7.

### Corriger sur un défaut constaté, jamais anticipé

> **On ne corrige l'instrument que sur un défaut observé sur une réponse réelle. À défaut
> égal, on préfère la correction qui retire du mécanisme à celle qui en ajoute.**

Cette règle est née d'une erreur qu'elle aurait évitée. La relecture du 14/08 avait relevé
que `TracabiliteNumerique` se dilue quand le résultat grossit — mesuré : 1,3 % de
l'espace des nombres accepté en médiane, mais jusqu'à 48 % sur les exécutions ramenant
plusieurs centaines de valeurs. Le correctif proposé était « resserrer les échelles, une
ligne, sans risque ». Mesuré avant d'être appliqué :

- il **ne changeait pas la couverture** — 1,3 % avant, 1,3 % après ; la dilution vient du
  nombre de valeurs comparées, pas des échelles ;
- il **cassait 25 verdicts sur 119**, tous sur des réponses justes : écrire un montant en
  millions demande un facteur 10⁶.

Le durcissement aurait dégradé l'instrument. Le défaut visé, lui, était **anticipé** pour
E7 et non constaté : il se corrigera à E7, avec les vraies réponses sous les yeux. La
limite est écrite dans la docstring de l'assertion, chiffrée — *une limite documentée est
plus sûre qu'une limite corrigée à l'aveugle*.

Corollaire méthodologique, valable au-delà de ce projet : **mesurer avant de proposer**.
Le cache rend la vérification gratuite, et deux fois sur deux elle a contredit
l'intuition.

### Le budget, parce qu'une règle sans chiffre ne tient pas

`tests/eval` ne dépasse pas **1 058 lignes exécutables** — docstrings et commentaires
exclus, sans quoi le budget pousserait à supprimer les explications que ce dépôt exige.
La limite est tenue par `tests/test_eval_budget.py` et non par ce paragraphe : une règle
de méthode se contourne exactement comme une règle de prompt.

Ce que le plafond force, c'est la bonne question. Quand il est atteint, on ne le relève
pas : on cherche **quelle assertion existante n'a jamais échoué**. Le rapport
d'évaluation le dit, et la réponse est déjà connue pour plusieurs d'entre elles.

Trois décisions en découlent pour la suite :

- **E6 est supprimé.** Son contenu résiduel était de verrouiller la traçabilité des
  chiffres dans le code. On ne peut pas empêcher un modèle d'écrire un nombre : le
  mécanisme serait un post-traitement qui détecte sans pouvoir corriger, alors que
  `role.md` l'exige déjà en clair. Le défaut « somme faite de tête » se traite en E8, par
  la description des données — moins cher, réversible, mesurable sur tout le corpus.
- **E7 n'ajoute aucune assertion au harnais.** Le « Chart Check » est une colonne de
  notation *manuelle* du client. Écrire des contrôles automatiques de lisibilité de
  graphique referait exactement ce qui a produit les 2 418 lignes ci-dessus.
  `PasDeGraphiqueSurResultatVide` existe et redevient active ; c'est tout.
- **E7 n'a pas de boucle de correction.** Zéro tâtonnement sur 171 exécutions : rien ne
  justifie de construire pour le graphique le mécanisme de reprise qui n'a jamais servi
  pour le SQL. S'il en faut un, la mesure le dira d'abord.

### Le scellé est écrit, il n'est plus tiré

Le jeu de contrôle était défini par un `random.sample` de graine publiée — argument
d'honnêteté : personne ne peut rejouer le tirage jusqu'à obtenir celui qui l'arrange. Le
tirage se rejouait à chaque appel, donc il dépendait du **contenu courant** du corpus.
Mesuré : ajouter **une seule** propriété faisait sortir 2 des 3 propriétés scellées et
entrer une propriété déjà jouée trois fois à trois niveaux d'effort. E7 ajoute des
propriétés de graphique — le scellé se serait réattribué tout seul, sans erreur ni
avertissement, en perdant sa seule raison d'être : n'avoir jamais été vu.

Il est désormais une constante écrite à la main ; la graine reste en commentaire comme
trace de provenance. Plus faible sur le papier, strictement identique en fait — le tirage
n'était déjà plus reproductible.

Ce qui est fermé du même coup, et vaut d'être dit : **le scellé ne peut plus être
recomposé.** Les 17 questions du corpus de travail ont été jouées et leurs réponses lues.
En faire entrer une reviendrait à sceller une enveloppe ouverte. On peut seulement
l'augmenter de questions neuves, jamais exécutées.

Et il faut lire ce qu'il mesure sans lui prêter davantage : ses trois propriétés sont
toutes de la famille « refus expliqué », et aucun des quatre échecs de la ligne de base
n'y a de contrepartie. Son ouverture dira si le réglage a cassé les refus, pas s'il a
généralisé. C'est une non-régression, et l'annoncer comme telle vaut mieux que de la
présenter comme une preuve de généralisation.

### Ce qui indexe quoi — la famille de défaut, et sa clôture

Quatre défauts du dispositif de mesure ont maintenant la même forme, et il vaut mieux
nommer la forme que continuer à corriger les cas : **une clé d'indexation qui ne contient
pas tout ce qui distingue ce qu'elle indexe.** Le symptôme ne varie pas — une entrée
resservie sous une autre configuration, un rapport parfaitement plausible, aucune erreur,
aucun avertissement. C'est le pire mode de défaillance possible pour un instrument, parce
qu'il produit un chiffre au lieu de s'arrêter.

Recensement de toutes les clés du projet, ce qu'elles contiennent, et ce qui les distingue :

| Clé | Contenu | Ce qui la distingue |
|---|---|---|
| entrée de cache | modèle · empreinte prompt · empreinte réglages · question · répétition | complète |
| `empreinte_reglages` | effort · raisonnement · plafond de sortie · plafonds de boucle · **bornes de `run_sql`** · **description d'outil** | complète depuis le 18/08/2026 |
| `empreinte_prompt` | hachage des `.md` et de la partie générée | ne contient pas la description d'outil — reportée sur les réglages |
| registre des campagnes | une entrée par empreinte de réglages | complète depuis le 14/08/2026 |
| jeu de contrôle | trois noms de propriétés, écrits | ancré au corpus depuis le 18/08/2026 |

Les deux dernières colonnes du 18/08 se lisent ensemble avec ce qui précède : le
correctif du 14/08 sur le scellé — passer d'un tirage à une constante écrite — avait
*déplacé* le risque au lieu de le fermer. Un `random.sample` sur `proprietes()` ne peut
pas nommer une propriété inexistante ; une liste écrite à la main le peut. Son intégrité
reposait alors sur une *autre* liste épinglée, dans un test qui ne mentionne pas le
scellé et dont le message d'échec invite à mettre la liste à jour. Mesuré : renommage
plus mise à jour de bonne foi, et la suite entière passe au vert avec un jeu de contrôle
amputé.

Ce qui manquait à `empreinte_reglages` change directement ce que le modèle voit et donc
ce qu'il répond : les trois bornes de `run_sql` décident du contenu d'un retour d'outil,
et la description d'outil est prescriptive — elle dit *quand* appeler, donc elle pèse sur
le taux de sollicitation. Aucune des deux n'était nulle part. Le cache existant a été
re-clé plutôt que repayé, ce qui n'était légitime que parce que l'historique établit que
`sql.py` et `outil.py` étaient figés avant les trois campagnes. La preuve est le rejeu à
blanc : ligne de base reproduite à l'identique, 171 entrées relues, zéro appel.

**Ce qu'on ne construit pas.** Une détection automatique des réglages omis — introspection
des constantes, hachage des modules — serait le quatrième mécanisme posé pour couvrir le
troisième, c'est-à-dire exactement le motif qui a produit ces défauts. Ce qu'on met à la
place : un seul endroit où la question se pose, ce tableau, et une empreinte de référence
épinglée par un test dont le message dit ce que sa modification emporte.

### Les assertions qui n'ont jamais échoué, et celles qui ne peuvent pas

Le budget de complexité prescrivait, en cas de dépassement, de chercher « l'assertion qui
n'a jamais échoué ». Mesuré le 18/08/2026 en rejouant les 171 exécutions en cache :

| | familles | verdicts | échecs |
|---|---|---|---|
| familles ayant déjà échoué | 2 | 261 | 13 |
| familles n'ayant jamais échoué | 9 | 342 | 0 |

La question désigne donc les neuf dixièmes de l'instrument, dont le contrôle qui garde le
principal piège du jeu de données — l'homonymie entre périmètres. Il n'a jamais échoué
parce que l'agent a eu raison à chaque fois ; le retirer serait le contraire de ce que la
mesure suggère.

La question qui discrimine est autre : **quelle assertion ne *peut pas* échouer ?** Une
seule y répond, et elle est déjà documentée comme telle — le contrôle de graphique, inerte
tant que l'agent ne dessine pas. E7 la réactive.

Le budget est donc conservé, avec deux issues au lieu d'une : retirer ce qui est
structurellement inerte, **ou** relever le plafond en écrivant le motif à côté de la
constante. Un budget qui n'offre que la suppression transforme un chiffre en objectif et
pousse à retirer des gardes qui fonctionnent. Premier relèvement motivé le 18/08/2026,
1 058 → 1 063, pour la complétion de la clé de réglages.

Écarté, et pour la même raison qu'ailleurs : remplacer le budget de lignes par un audit de
discrimination — « chaque famille a-t-elle déjà échoué ? ». C'est la mesure directe de ce
qui compte, mais elle exige le cache, qui est hors dépôt : elle ne tient donc pas comme
test, et en faire une commande ajouterait du mécanisme contre un défaut que le budget n'a
pas encore causé.

### Le grain de `kpi_compteurs` est la cause mesurée de l'instabilité « grain distinct »

Constaté le 18/08/2026 dans l'interface, puis vérifié sur le cache. La propriété « grain
distinct » est notée **2/3 instable** dans la ligne de base ; les 9 exécutions en cache de
sa question donnent une corrélation parfaite :

| requête sur `kpi_compteurs` | motif d'arrêt | nombre de requêtes |
|---|---|---|
| agrégée (7 exécutions) | `reponse_donnee` | 1 à 3 |
| **non agrégée (2 exécutions)** | **`plafond_iterations`** | 4 et 5 |

`kpi_compteurs` a une ligne par semaine **et par énergie**. Une CTE qui la lit sans
`GROUP BY` double donc le nombre de lignes et duplique la valeur jointe sur chacune. Le
modèle tâtonne ensuite pour s'en sortir et heurte le plafond d'itérations. Symptôme
secondaire, visible dans l'interface : la réponse affirme « sommé sur les deux énergies »
alors que sa requête ne somme pas — l'intention est juste, la requête ne la réalise pas.

**Ce n'est pas une lacune du prompt.** La section générée annonce explicitement « Grain :
une semaine × une énergie » pour cette table. L'information est présente et exacte ; le
modèle ne s'en sert pas systématiquement.

Non corrigé, et pour la raison habituelle : une observation n'est pas un défaut, c'est une
hypothèse. Elle est notée ici parce qu'elle est **chiffrée et reproductible**, ce qui en
fait la première piste utilisable d'E8 — la question étant de savoir si le grain doit être
rappelé autrement (une phrase de `metier.md` sur les tables à plusieurs lignes par semaine)
ou si c'est le plafond d'itérations qui est trop bas. Les deux se mesurent sur le corpus
entier, et pas sur cette question.

### E7 — le code décide du graphique, pas le modèle

Trois façons de produire un graphique étaient possibles : le modèle appelle un second
outil avec une spécification, le modèle émet un bloc balisé dans son texte, ou le code lit
la forme du résultat. **La troisième a été retenue.**

Le raisonnement tient en une phrase : *le SQL que le modèle écrit est déjà l'expression de
son intention*. Quand il sélectionne une date et deux mesures, il dit ce qu'il veut
montrer ; lui demander de le décrire une seconde fois n'ajoute pas d'information, ça ajoute
une occasion de diverger.

Trois raisons concrètes s'y ajoutent :

- un second outil entre dans le préfixe mis en cache **et** coûte un aller-retour, contre
  un plafond d'itérations dont on venait de mesurer qu'il mord déjà — deux exécutions sur
  neuf de la question « grain distinct » finissaient en `plafond_iterations` ;
- il confierait au modèle un choix qui varie d'une exécution à l'autre, alors que les
  règles en jeu sont des propriétés des données : deux unités ne se superposent pas, un
  NULL n'est pas un zéro, un résultat vide ne se trace pas ;
- analyser un bloc balisé dans une sortie de modèle est fragile, et le projet tient qu'un
  texte se contourne.

**Ce qu'E7 a surtout fait, c'est déplacer des règles existantes.** `principes.md` portait
déjà « Ne trace jamais de graphique sur un résultat vide » et « Ne mets pas GRP,
impressions et clics sur le même axe » — dans un texte que rien n'appliquait. Elles sont
descendues dans `src/charts`, où elles sont exécutables et vues mordre par des tests. La
première a été retirée du prompt, devenue sans objet ; la seconde a été recalée vers le
SQL, qui est l'endroit amont où elle sert encore.

Une règle nouvelle mérite d'être signalée parce qu'elle attrape un défaut réel : **une
abscisse qui se répète fait refuser le graphique**. Le résultat est alors lu à un grain
plus fin qu'on ne le croit — le cas `kpi_compteurs` consigné la veille — et superposer ces
lignes produirait un graphique lisible et faux, c'est-à-dire le pire cas possible.

Le prix payé, une fois : le prompt gagne deux faits qu'il taisait — les résultats sont
tracés automatiquement, la réponse est rendue en Markdown. L'empreinte change, donc les
171 exécutions en cache ne se rejouent plus. Les deux modifications ont été groupées pour
ne périmer la ligne de base qu'une seule fois, et l'hypothèse sur le grain de
`kpi_compteurs` en a été **exclue volontairement** : la mélanger aurait confondu deux
mesures.

Vérifié après coup sur trois questions de graphique réelles, avant de payer la campagne :
le tracé sort correctement, et l'énumération en toutes lettres de ce qui est déjà tracé
n'apparaît que sur un cas — des barres à dix catégories, où un tableau de dix lignes se
défend. Pas de correctif, donc : la mesure a de nouveau contredit l'intuition.

### L'alias n'est pas l'identifiant

Le nom de modèle écrit dans le code est un **alias** : il désigne aujourd'hui une génération
précise, il en désignera une autre demain sans qu'une ligne change. Or le cache du harnais
est indexé sur l'identifiant du modèle, et sert précisément à ne pas repayer.

Indexer sur l'alias laisserait un basculement resservir des réponses produites par un autre
modèle, en silence. L'identifiant exact ne descend que dans les métadonnées d'une réponse :
il faut donc appeler pour le connaître. Une campagne commence par un appel minimal — sans
prompt système, quelques tokens — dont c'est le seul but, et qui vérifie du même coup que la
clé répond avant d'engager le reste.

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
  l'écriture a bien lieu ; la valeur réelle est publiée sous une autre clé — une par durée
  de vie de cache. Ces valeurs se **somment** : n'en retenir qu'une donne un total juste
  tant qu'une seule durée est utilisée, c'est-à-dire juste par coïncidence.
- Le champ « tokens d'entrée » n'a pas le même sens dans le connecteur (total) et dans
  l'API brute (reste non caché). Les mélanger fausse tout calcul de coût.
- Le connecteur n'envoie pas de configuration de raisonnement par défaut : le défaut de
  l'API s'applique, raisonnement adaptatif compris. C'est un poste de coût qui n'apparaît
  dans aucune ligne de code.

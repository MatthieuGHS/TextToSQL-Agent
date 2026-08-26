"""Assertions du harnais d'évaluation.

Une réponse d'agent est du texte libre : on ne peut pas l'asserter directement. Le
harnais sépare donc quatre types de contrôle, du plus mécanique au plus subjectif :

- **A — valeur** : le chiffre restitué est-il le bon ? Comparé au résultat d'un SQL de
  référence, écrit et relu indépendamment de tout appel d'agent.
- **B — forme du SQL** : bonne table, pas de date du jour, jointure sur la semaine.
- **C — forme du texte** : présence d'un caveat, absence d'un classement de performance,
  traçabilité des chiffres.
- **D — jugement** : la pertinence globale. Non automatisable, colonne du rapport.

S'y ajoute un contrôle qui ne porte ni sur le SQL ni sur le texte mais sur **l'état dans
lequel la réponse a été produite** — la boucle a-t-elle abouti, ou renoncé ?

Ce module implémente A, B et C. Chaque assertion est testée dans les deux sens
(`tests/test_eval_assertions.py`) : un contrôle qu'on n'a jamais vu échouer n'en est pas un.

Règle non négociable : une valeur attendue vient toujours d'un SQL de référence, jamais
de ce que l'agent a produit. Sinon le harnais note l'agent contre lui-même.

**Tout SQL est ré-exécuté par `run_sql`**, jamais par `con.execute()` en direct. Le SQL
évalué ici est celui d'un modèle : il peut croiser deux tables sans condition de jointure
et balayer indéfiniment. `run_sql` borne le temps et le nombre de lignes — sans lui, un
harnais lancé pour mesurer se bloquerait sur la question qui l'intéressait le plus. Second
bénéfice : le plafond de lignes est celui que l'agent a réellement vu, donc la traçabilité
se juge sur les mêmes valeurs que lui.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

import duckdb

from src.agent.reponse import Arret
from src.db import sql as acces_sql

TABLES = ("media", "kpi_compteurs", "contexte")

# Les trois façons dont `run_sql` refuse une requête. Regroupées ici : toutes signifient
# la même chose pour une assertion — aucune valeur n'a pu être obtenue.
ECHECS_SQL = (acces_sql.SqlRefuse, acces_sql.SqlInvalide, acces_sql.SqlTropLong)

# Délai de ré-exécution, passé explicitement à chaque appel.
#
# Deux raisons de ne pas se contenter du défaut de `run_sql`. D'abord il est figé à la
# définition de la fonction : le remplacer dans le module d'accès ne change rien à un
# appel qui l'omet — un réglage qu'on croirait actif et qui ne l'est pas. Ensuite le
# harnais a besoin de le baisser pour se tester lui-même.
#
# La valeur reste **celle de l'agent** : une requête que l'agent a pu exécuter ne doit pas
# échouer ici faute de budget, sinon le harnais inventerait des échecs.
DELAI_SECONDES = acces_sql.DELAI_SECONDES

# Fonctions de date « maintenant » : leur présence signale que le modèle a calculé une
# période relative depuis aujourd'hui au lieu de la fin des données.
MOTIFS_DATE_COURANTE = ("CURRENT_DATE", "CURRENT_TIMESTAMP", "NOW(", "TODAY(", "GETDATE(")


@dataclass(frozen=True)
class Resultat:
    """Ce qu'une exécution de l'agent expose au harnais.

    Volontairement pauvre : le harnais ré-exécute lui-même le SQL en lecture seule pour
    en tirer les valeurs, plutôt que de faire confiance à ce que l'agent rapporte.
    """

    reponse: str
    sql: list[str] = field(default_factory=list)
    graphique: bool = False
    # Motif d'arrêt de la boucle (`src.agent.reponse.Arret`). Valeur par défaut explicite :
    # les sorties fabriquées à la main des tests, et les traces déjà en cache, restent
    # lisibles sans être réécrites.
    arret: str = Arret.REPONSE_DONNEE.value


@dataclass(frozen=True)
class Verdict:
    nom: str
    ok: bool
    detail: str = ""


class Assertion(Protocol):
    def verifier(self, r: Resultat, con: duckdb.DuckDBPyConnection) -> Verdict: ...


# --- Outils partagés -----------------------------------------------------------------


def _valeurs_numeriques(
    requete: str, con: duckdb.DuckDBPyConnection
) -> list[float]:
    """Ré-exécute une requête et aplatit ses valeurs numériques.

    Une requête invalide ou trop longue ne lève pas : elle ne produit simplement aucune
    valeur. C'est l'assertion appelante qui décide si c'est un échec.
    """
    try:
        rows = acces_sql.run_sql(requete, con, delai=DELAI_SECONDES).lignes
    except ECHECS_SQL:
        return []
    valeurs = []
    for row in rows:
        for cell in row:
            # `bool` est un `int` en Python : l'exclure avant tout autre test.
            if cell is None or isinstance(cell, bool):
                continue
            # DuckDB renvoie un `Decimal` dès qu'une colonne est DECIMAL — ce que produit
            # un littéral comme `1000.0`, un ROUND ou un AVG. Le manquer laisserait passer
            # silencieusement des requêtes dont on ne vérifierait plus la valeur.
            if isinstance(cell, (int, float, Decimal)):
                valeur = float(cell)
                if math.isfinite(valeur):
                    valeurs.append(valeur)
    return valeurs


def _proche(a: float, b: float, tolerance: float) -> bool:
    """Égalité relative, avec repli sur l'absolu quand la référence vaut zéro."""
    if b == 0:
        return abs(a) <= tolerance
    return abs(a - b) / abs(b) <= tolerance


def _normalise(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).upper()


def _tables_citees(sql: str) -> set[str]:
    haut = _normalise(sql)
    return {t for t in TABLES if re.search(rf"\b{t.upper()}\b", haut)}


# --- Type A : valeur ------------------------------------------------------------------


@dataclass(frozen=True)
class ValeurAttendue:
    """Le résultat de référence apparaît dans ce que l'agent a effectivement calculé.

    On compare des **résultats de requêtes**, pas des nombres extraits du texte : le
    texte est libre, formaté, arrondi, et son analyse serait fragile. La traçabilité du
    texte vers le SQL est contrôlée séparément par `TracabiliteNumerique`.
    """

    sql_ref: str
    tolerance: float = 0.001
    nom: str = "valeur attendue"

    def verifier(self, r: Resultat, con: duckdb.DuckDBPyConnection) -> Verdict:
        attendues = _valeurs_numeriques(self.sql_ref, con)
        if not attendues:
            return Verdict(self.nom, False, "le SQL de référence ne renvoie rien")
        cible = attendues[0]

        obtenues = [v for q in r.sql for v in _valeurs_numeriques(q, con)]
        if not obtenues:
            return Verdict(self.nom, False, "aucune valeur calculée par l'agent")

        if any(_proche(v, cible, self.tolerance) for v in obtenues):
            return Verdict(self.nom, True, f"{cible:,.2f} retrouvée")
        return Verdict(
            self.nom, False, f"attendu {cible:,.2f}, absent des {len(obtenues)} valeurs"
        )


# --- Type B : forme du SQL ------------------------------------------------------------


@dataclass(frozen=True)
class SqlUtiliseTable:
    table: str
    nom: str = ""

    def verifier(self, r: Resultat, con) -> Verdict:
        nom = self.nom or f"interroge {self.table}"
        citees = {t for q in r.sql for t in _tables_citees(q)}
        return Verdict(nom, self.table in citees, f"tables vues : {sorted(citees)}")


@dataclass(frozen=True)
class SqlNeTouchePas:
    """Le périmètre demandé exclut cette table.

    Sert au piège d'homonymie : `cost`, `grp` et `compteurs` existent des deux côtés
    avec des périmètres différents et des ordres de grandeur voisins.
    """

    table: str
    nom: str = ""

    def verifier(self, r: Resultat, con) -> Verdict:
        nom = self.nom or f"n'interroge pas {self.table}"
        citees = {t for q in r.sql for t in _tables_citees(q)}
        return Verdict(nom, self.table not in citees, f"tables vues : {sorted(citees)}")


@dataclass(frozen=True)
class SqlSansDateCourante:
    """Une période relative se calcule depuis la fin des données, pas depuis aujourd'hui."""

    nom: str = "pas de date du jour"

    def verifier(self, r: Resultat, con) -> Verdict:
        for q in r.sql:
            haut = _normalise(q)
            for motif in MOTIFS_DATE_COURANTE:
                if motif in haut:
                    return Verdict(self.nom, False, f"{motif} trouvé")
        return Verdict(self.nom, True)


@dataclass(frozen=True)
class SqlJointureSurSemaine:
    """Croiser deux grains sans jointure sur la semaine produit un produit cartésien.

    Le contrôle ne se déclenche que si au moins deux tables sont citées dans la même
    requête — sinon il n'y a rien à joindre.
    """

    nom: str = "jointure sur la semaine"

    def verifier(self, r: Resultat, con) -> Verdict:
        for q in r.sql:
            if len(_tables_citees(q)) < 2:
                continue
            if "STEP_DATE" not in _normalise(q):
                return Verdict(self.nom, False, "deux grains croisés sans step_date")
        return Verdict(self.nom, True)


@dataclass(frozen=True)
class SqlExecutable:
    """Toute requête produite s'exécute — et n'en produire aucune n'est pas une faute.

    Le contrôle porte sur ce qui a été écrit, pas sur le fait qu'il ait été écrit quelque
    chose. La distinction n'est pas théorique : une bonne part des questions d'un jeu
    d'évaluation sont des pièges — une dimension qui n'existe pas, un canal mal nommé, une
    attribution impossible — et la bonne réponse est alors un refus expliqué, sans
    requête. `Arret.est_normal` bénit déjà ce cas côté boucle ; le faire échouer ici
    reviendrait à noter l'agent contre sa propre conception.

    Mesuré sur la première campagne : quatre des cinq échecs de la grille client étaient
    des refus pédagogiques irréprochables, comptés faux pour n'avoir rien requêté.

    Quand une question *exige* d'interroger la base, c'est `SqlProduit` qui le dit — et
    il faut alors le dire explicitement, cas par cas.
    """

    nom: str = "SQL exécutable"

    def verifier(self, r: Resultat, con) -> Verdict:
        if not r.sql:
            return Verdict(self.nom, True, "aucune requête à vérifier")
        for q in r.sql:
            try:
                acces_sql.run_sql(q, con, delai=DELAI_SECONDES)
            except ECHECS_SQL as exc:
                return Verdict(self.nom, False, f"{type(exc).__name__}: {exc}"[:160])
        return Verdict(self.nom, True, f"{len(r.sql)} requête(s)")


@dataclass(frozen=True)
class SqlProduit:
    """Cette question-là ne se répond pas sans interroger la base.

    Séparé de `SqlExecutable` parce que ce sont deux propriétés distinctes, et que les
    confondre a un coût dans les deux sens : exiger partout une requête punit les refus
    voulus, ne l'exiger nulle part laisse passer une réponse affirmée de mémoire.

    À poser cas par cas, et seulement quand la réponse dépend réellement d'une valeur des
    données — pas sur un jeu de questions dont on ne connaît pas d'avance la nature.
    """

    nom: str = "SQL produit"

    def verifier(self, r: Resultat, con) -> Verdict:
        return Verdict(self.nom, bool(r.sql), f"{len(r.sql)} requête(s)")


# --- État de la boucle ----------------------------------------------------------------


@dataclass(frozen=True)
class ArretNormal:
    """La boucle a-t-elle abouti, ou s'est-elle arrêtée en chemin ?

    Ni le SQL ni le texte ne le disent. Une réponse peut être exacte et pourtant coupée au
    plafond de sortie, ou rendue après que la boucle a renoncé — le texte paraît complet
    dans les deux cas. Lire la réponse sans savoir dans quel état elle a été produite
    compterait un abandon comme un succès.

    `reponse_donnee` couvre aussi la demande de précision sur une question ambiguë : le
    prompt l'y invite, c'est un comportement voulu, et `Arret.est_normal` porte déjà la
    distinction. La rejouer ici la ferait diverger un jour.

    `erreur_api` n'arrive jamais jusqu'ici : l'adaptateur écarte l'exécution avant, parce
    qu'une panne de réseau n'est pas un défaut de l'agent.
    """

    nom: str = "arrêt normal"

    def verifier(self, r: Resultat, con) -> Verdict:
        try:
            arret = Arret(r.arret)
        except ValueError:
            # Une trace écrite par une version antérieure, ou un motif mal recopié. Le
            # dire plutôt que de l'assimiler à un succès par défaut.
            return Verdict(self.nom, False, f"motif d'arrêt inconnu : {r.arret!r}")
        return Verdict(self.nom, arret.est_normal, arret.value)


# --- Type C : forme du texte ----------------------------------------------------------


@dataclass(frozen=True)
class TexteContient:
    motifs: tuple[str, ...]
    au_moins: int = 1
    nom: str = ""

    def verifier(self, r: Resultat, con) -> Verdict:
        bas = r.reponse.lower()
        trouves = [m for m in self.motifs if m.lower() in bas]
        nom = self.nom or f"texte mentionne {self.motifs[0]}…"
        return Verdict(
            nom, len(trouves) >= self.au_moins, f"trouvés : {trouves or 'aucun'}"
        )


@dataclass(frozen=True)
class TexteNeContientPas:
    """Vocabulaire interdit, cherché **sur des mots entiers**.

    Asymétrie voulue avec `TexteContient`, et elle se justifie par le sens de l'erreur.
    Chercher une sous-chaîne pour *exiger* un mot est utile — « attribu » attrape
    attribution, attribuer, attribuable — et une occurrence en trop ne fait que rendre le
    contrôle plus indulgent. Chercher une sous-chaîne pour *interdire* fait l'inverse :
    « roi » se trouve dans « droite » et dans « trois », et le contrôle condamne alors des
    réponses irréprochables. Mesuré sur la première campagne réelle : les deux seuls
    échecs de ce contrôle portaient sur des réponses exemplaires, qui refusaient
    explicitement d'additionner des unités incomparables.

    Un faux positif ici ne fait pas que du bruit — il détourne le réglage vers un défaut
    qui n'existe pas.
    """

    motifs: tuple[str, ...]
    nom: str = ""

    def verifier(self, r: Resultat, con) -> Verdict:
        bas = r.reponse.lower()
        trouves = [
            m for m in self.motifs
            if re.search(rf"\b{re.escape(m.lower())}\b", bas)
        ]
        nom = self.nom or f"texte évite {self.motifs[0]}…"
        return Verdict(nom, not trouves, f"trouvés : {trouves or 'aucun'}")


@dataclass(frozen=True)
class PasDeGraphiqueSurResultatVide:
    """Un résultat vide est une information. On le rapporte, on ne le trace pas.

    **Active depuis E7.** Elle a été inerte de sa création au 18/08/2026 :
    `agent_reel._resultat()` forçait `graphique=False`, faute d'agent capable de dessiner,
    et ce contrôle rendait `True` sans rien examiner. Il renseigne désormais le champ
    depuis `AgentResponse.graphique`.

    Ce qu'elle mesure vraiment, et qui a changé de nature : la décision de tracer est prise
    par `src/charts`, en code, et son premier refus est justement le résultat vide. Cette
    assertion contrôle donc que cette garde-là tient de bout en bout — de la boucle jusqu'au
    verdict — plutôt qu'une intention du modèle. C'est plus faible qu'il n'y paraît, et
    c'est voulu : le contrôle fort est dans `tests/test_charts.py`, sur des fonctions pures.
    """

    nom: str = "pas de graphique sur résultat vide"

    def verifier(self, r: Resultat, con) -> Verdict:
        if not r.graphique:
            return Verdict(self.nom, True, "aucun graphique")
        valeurs = [v for q in r.sql for v in _valeurs_numeriques(q, con)]
        return Verdict(self.nom, bool(valeurs), "graphique sur résultat vide")


# Nombres écrits à la française : séparateur de milliers espace (y compris insécable ou
# fine), décimale virgule. On capte aussi la forme anglaise pour ne rien manquer.
# **Les groupes de milliers font exactement trois chiffres, et le dernier n'est pas
# suivi d'un chiffre.** Sans ces deux conditions, « T4 2022 » se lisait comme le nombre
# 42022 — l'espace d'un libellé de trimestre passant pour un séparateur de milliers — et
# la réponse était déclarée non traçable alors qu'elle datait simplement son propos.
# Mesuré le 25/08/2026 en rejouant les assertions **que chaque cas déclare** sur les 344
# exécutions en cache : **1 verdict noté passe au vert, aucun ne passe au rouge**. Une
# première mesure annonçait 8 ; elle appliquait le contrôle à toutes les exécutions, y
# compris à des cas qui ne le portent pas, et surestimait donc son effet. Le défaut reste
# réel — une réponse juste était refusée — mais son emprise sur les scores est d'un
# verdict, et il n'en change aucun sur les campagnes antérieures.
#
# L'ancrage `(?!\d)` n'est pas décoratif : sans lui, la première version de ce correctif
# lisait « 2019 » comme « 201 » — le groupe de trois s'arrêtant avant le quatrième
# chiffre — et faisait passer **37 verdicts au rouge**. La mesure l'a rejetée avant
# qu'elle ne soit appliquée.
_NOMBRE = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?!\d)(?:[.,]\d+)?|\d+(?:[.,]\d+)?")

# Un nombre isolé de 1 ou 2 chiffres est presque toujours un ordinal, une date ou un
# effectif de phrase ("les 3 canaux"), pas un chiffre extrait des données.
#
# Ce que ce plancher exempte, et qu'il faut savoir en lisant un score : **tous les
# pourcentages** (« +47 % »), tous les ratios (« 2,5 fois plus ») et toute valeur
# décimale sous 10 écrite avec une seule décimale. Un pourcentage inventé passe donc
# librement. C'est une exemption assumée — un plancher plus bas condamnerait les
# ordinaux — et non un oubli. À rouvrir si des pourcentages fabriqués apparaissent dans
# les réponses réelles ; à la date du 14/08/2026, aucun n'a été observé.
_PLANCHER_CHIFFRES = 3


def _normalise_nombre(brut: str) -> str:
    """« 1 750 » → « 1750 », « 1,5 » → « 1.5 ». Espaces fine et insécable comprises."""
    texte = brut.strip()
    for espace in (" ", " ", " "):
        texte = texte.replace(espace, "")
    return texte.replace(",", ".")


def _rang_significatif(texte: str) -> int:
    """Le rang du dernier chiffre significatif écrit, au sens de `round()`.

    C'est la précision que l'écriture **revendique**, et elle se lit des deux côtés de la
    virgule : « 0,06 » revendique le centième (rang 2), « 350 000 » ne revendique que la
    dizaine de mille (rang −4). Une seule notion pour les deux, parce que c'est le même
    fait — un auteur qui n'écrit pas un chiffre n'affirme rien à ce rang.

    Les zéros de fin ne comptent que sur un entier : « 1,50 » revendique bien son
    centième, le zéro y est significatif puisque rien n'obligeait à l'écrire.
    """
    decimales = texte.partition(".")[2]
    if decimales:
        return len(decimales)
    entier = texte.lstrip("0") or "0"
    return -(len(entier) - len(entier.rstrip("0")))


def _litteraux(requete: str, con: duckdb.DuckDBPyConnection) -> set[str]:
    """Les nombres tels qu'ils apparaissent **littéralement** dans un résultat.

    `_valeurs_numeriques` ne garde que les cellules numériques : les dates en sortent, et
    avec elles les chiffres qu'elles contiennent. Or une réponse qui écrit « du 31/12/2018
    au 29/12/2025 » cite des valeurs qu'une requête lui a bel et bien rendues. Les
    déclarer non traçables condamnait, sur la première campagne réelle, des réponses
    justes qui ne faisaient que dater leur périmètre — ce que le prompt leur demande.
    """
    try:
        resultat = acces_sql.run_sql(requete, con, delai=DELAI_SECONDES)
    except ECHECS_SQL:
        return set()
    return {
        _normalise_nombre(brut)
        for ligne in resultat.lignes
        for cellule in ligne
        if cellule is not None
        for brut in _NOMBRE.findall(str(cellule))
    }


@lru_cache(maxsize=4)
def _nombres_du_prompt(con: duckdb.DuckDBPyConnection) -> frozenset[str]:
    """Les chiffres que la description des données annonce à l'agent.

    Troisième source légitime, à côté des résultats de requête. Le prompt généré énonce
    des volumétries, des bornes temporelles et des cardinalités — toutes tirées de la base
    par E3, et vérifiées contre elle par ses propres tests. Un agent qui les reprend ne
    fabrique rien : il cite ce qu'on lui a dit, et la traçabilité tient un cran plus haut.
    L'exiger de requêter ce qu'on vient de lui dire coûterait des tokens pour rien.

    L'ensemble reste petit — quelques dizaines de nombres — donc il n'affaiblit pas le
    contrôle : ce qui est inventé continue de ressortir.

    **Construit sur la connexion évaluée**, jamais sur la base par défaut : sinon une
    assertion exercée sur un jeu d'essai irait chercher la vraie base, et blanchirait des
    chiffres qui n'ont rien à voir. Le résultat est mis en cache par connexion — le
    prompt est déterministe, c'est ce qui rend son cache possible chez l'agent aussi.

    Le repli sur l'ensemble vide couvre les bases d'essai, qui n'ont pas le schéma complet
    et pour lesquelles cette source n'a pas de sens. En production il n'est jamais pris,
    et `test_les_nombres_du_prompt_sont_lus_sur_la_vraie_base` le vérifie — sans quoi le
    contrôle pourrait s'affaiblir en silence.
    """
    from src.agent import prompt

    try:
        texte = prompt.construire(con)
    except Exception:  # noqa: BLE001 — base d'essai sans le schéma complet
        return frozenset()
    return frozenset(_normalise_nombre(brut) for brut in _NOMBRE.findall(texte))


@dataclass(frozen=True)
class TracabiliteNumerique:
    """Aucun chiffre du texte qui ne vienne d'une source vérifiable.

    C'est le contrat qui interdit à l'agent d'inventer un ordre de grandeur. Trois
    sources, et trois seulement : une valeur **calculée** par une de ses requêtes, un
    nombre qui figure **littéralement** dans un de ses résultats, ou un chiffre annoncé
    par la **description générée** des données.

    La comparaison aux valeurs calculées est tolérante aux arrondis de présentation
    (« 4,2 millions » pour 4 231 07x) : on accepte un nombre s'il approche une valeur
    calculée, à n'importe quelle puissance de mille près. Les deux autres sources se
    comparent littéralement — une date ne s'arrondit pas.

    **L'écriture déclare sa propre précision.** Un coefficient rendu « 0,06 » affirme une
    valeur entre 0,055 et 0,065 : le juger à 2 % *relatifs* réclamerait une exactitude que
    son auteur n'a pas revendiquée, et condamnerait un 0,0565 parfaitement calculé. On
    accepte donc aussi une valeur qui, arrondie au **rang du dernier chiffre significatif
    écrit**, redonne le nombre écrit. Cette règle ne dépend d'aucune question : elle dit
    seulement qu'un arrondi n'est pas une invention.

    Ce rang se lit des deux côtés de la virgule, et c'est une correction du 26/08/2026 :
    la règle ne valait que pour les décimales, si bien qu'une borne de fourchette écrite
    « 350 000 » était jugée au millier près alors qu'elle n'affirme qu'une dizaine de
    mille. Mesuré sur les 105 exécutions de la campagne de référence : **4 échecs sur 13
    disparaissent**, tous des bornes rondes de fourchettes verbales, et **aucun échec réel
    n'est perdu** — un total faux à l'unité près n'a pas de zéro de fin, il continue
    d'affirmer l'unité et reste attrapé.

    ⚠ Les 4 artefacts restants ne sont **pas** un problème de tolérance et ne doivent pas
    être traités en élargissant celle-ci : ce sont un nom d'unité (le « 1000 » d'un coût
    pour mille), une constante de pourcentage (« NULL à 100 % »), un millésime, et une
    borne dont aucune valeur calculée n'approche le rang revendiqué. Chacun demanderait
    son propre régime, et cette fonction en a déjà trois. Une tolérance élargie d'un cran
    les ferait tous passer — et accepterait aussi « 100 » pour n'importe quelle valeur
    entre 0 et 200, ce qui retirerait au contrôle l'essentiel de ce qu'il sait faire.

    **La comparaison porte sur les grandeurs.** Le signe n'est pas capté par l'expression
    régulière — « −0,23 » en donne « 0,23 » — donc l'opposer à une corrélation négative
    rejetait systématiquement des valeurs justes. Limite assumée : une réponse qui
    inverserait le signe d'une valeur calculée passerait ici. C'est un défaut de lecture,
    pas d'invention, et ce contrôle-ci ne traite que l'invention.

    Ce qui reste attrapé, et c'est le cas qui compte : un chiffre qu'aucune requête n'a
    produit — un total fait de tête à partir de deux résultats, ou une lecture approximative
    d'un ordre de grandeur qu'on n'a pas demandé à la base.

    ⚠️ **Le contrôle se dilue quand le résultat grossit**, et il faut le savoir avant de
    lire un score. Un nombre est accepté s'il approche *n'importe laquelle* des valeurs de
    *n'importe laquelle* des requêtes : la part de l'espace des nombres qu'il accepte croît
    donc avec le nombre de cellules ramenées. Mesuré le 14/08/2026 sur les 119 exécutions
    en cache — part des entiers de 3 à 7 chiffres jugés traçables : **1,3 % en médiane,
    mais 27 à 48 % sur les exécutions ramenant 170 à 570 valeurs**, qui sont les questions
    de graphique.

    Non corrigé, et volontairement. Les deux resserrements envisagés ont été mesurés :
    borner les échelles de `range(0, 4)` à `range(0, 2)` ne change pas la couverture
    (1,3 % avant comme après) et fait échouer 25 verdicts sur 119 portant sur des réponses
    justes : un montant écrit « 47,3 » pour 47 300 000 € demande un facteur 10⁶.
    Conditionner la
    tolérance à la taille du résultat ajoutait un régime de plus à une fonction qui en a
    déjà trois, contre un défaut jamais observé sur une réponse réelle.

    À traiter à E7, quand les gros résultats deviendront la norme et que le défaut sera
    constaté plutôt qu'anticipé.
    """

    tolerance: float = 0.02
    nom: str = "traçabilité des chiffres"

    def verifier(self, r: Resultat, con: duckdb.DuckDBPyConnection) -> Verdict:
        calculees = [v for q in r.sql for v in _valeurs_numeriques(q, con)]
        litteraux = set(_nombres_du_prompt(con))
        for q in r.sql:
            litteraux |= _litteraux(q, con)
        orphelins = []

        for brut in _NOMBRE.findall(r.reponse):
            texte = _normalise_nombre(brut)
            if texte.count(".") > 1 or len(texte.replace(".", "")) < _PLANCHER_CHIFFRES:
                continue
            try:
                n = float(texte)
            except ValueError:
                continue
            if texte in litteraux:
                continue
            rang = _rang_significatif(texte)
            if not any(self._correspond(n, v, rang) for v in calculees):
                orphelins.append(brut.strip())

        return Verdict(
            self.nom,
            not orphelins,
            f"non traçables : {orphelins[:5]}" if orphelins else "",
        )

    def _correspond(self, n: float, valeur: float, rang: int) -> bool:
        """Vrai si `n` approche `valeur`, à un facteur mille près.

        Couvre les unités de présentation : « 4,2 millions » pour 4 231 07x, et l'arrondi
        que l'écriture revendique — « 0,06 » pour 0,0565, « 350 000 » pour 34x xxx.

        Sur les grandeurs, jamais sur les signes — l'expression régulière ne capte pas le
        « − » qui précède, et l'opposer à une valeur négative rejetterait un calcul juste.
        """
        if not math.isfinite(valeur):
            return False
        n, valeur = abs(n), abs(valeur)

        for exposant in range(0, 4):
            echelle = 10 ** (3 * exposant)
            for ecrit, calcule in ((n, valeur / echelle), (n * echelle, valeur)):
                if _proche(ecrit, calcule, self.tolerance):
                    return True
                if round(calcule, rang) == round(ecrit, rang):
                    return True
        return False

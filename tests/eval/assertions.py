"""Assertions du harnais d'évaluation.

Une réponse d'agent est du texte libre : on ne peut pas l'asserter directement. Le
harnais sépare donc quatre types de contrôle, du plus mécanique au plus subjectif :

- **A — valeur** : le chiffre restitué est-il le bon ? Comparé au résultat d'un SQL de
  référence, écrit et relu indépendamment de tout appel d'agent.
- **B — forme du SQL** : bonne table, pas de date du jour, jointure sur la semaine.
- **C — forme du texte** : présence d'un caveat, absence d'un classement de performance,
  traçabilité des chiffres.
- **D — jugement** : la pertinence globale. Non automatisable, colonne du rapport.

Ce module implémente A, B et C. Chaque assertion est testée dans les deux sens
(`tests/test_eval_assertions.py`) : un contrôle qu'on n'a jamais vu échouer n'en est pas un.

Règle non négociable : une valeur attendue vient toujours d'un SQL de référence, jamais
de ce que l'agent a produit. Sinon le harnais note l'agent contre lui-même.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

import duckdb

TABLES = ("media", "kpi_compteurs", "contexte")

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


@dataclass(frozen=True)
class Verdict:
    nom: str
    ok: bool
    detail: str = ""


class Assertion(Protocol):
    def verifier(self, r: Resultat, con: duckdb.DuckDBPyConnection) -> Verdict: ...


# --- Outils partagés -----------------------------------------------------------------


def _valeurs_numeriques(
    sql: str, con: duckdb.DuckDBPyConnection
) -> list[float]:
    """Ré-exécute une requête et aplatit ses valeurs numériques.

    Une requête invalide ne lève pas : elle ne produit simplement aucune valeur. C'est
    l'assertion appelante qui décide si c'est un échec.
    """
    try:
        rows = con.execute(sql).fetchall()
    except Exception:
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
    """Toute requête produite s'exécute. Une requête cassée est un échec, pas un vide."""

    nom: str = "SQL exécutable"

    def verifier(self, r: Resultat, con) -> Verdict:
        if not r.sql:
            return Verdict(self.nom, False, "aucune requête produite")
        for q in r.sql:
            try:
                con.execute(q).fetchall()
            except Exception as exc:
                return Verdict(self.nom, False, f"{type(exc).__name__}: {exc}"[:160])
        return Verdict(self.nom, True, f"{len(r.sql)} requête(s)")


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
    motifs: tuple[str, ...]
    nom: str = ""

    def verifier(self, r: Resultat, con) -> Verdict:
        bas = r.reponse.lower()
        trouves = [m for m in self.motifs if m.lower() in bas]
        nom = self.nom or f"texte évite {self.motifs[0]}…"
        return Verdict(nom, not trouves, f"trouvés : {trouves or 'aucun'}")


@dataclass(frozen=True)
class PasDeGraphiqueSurResultatVide:
    """Un résultat vide est une information. On le rapporte, on ne le trace pas."""

    nom: str = "pas de graphique sur résultat vide"

    def verifier(self, r: Resultat, con) -> Verdict:
        if not r.graphique:
            return Verdict(self.nom, True, "aucun graphique")
        valeurs = [v for q in r.sql for v in _valeurs_numeriques(q, con)]
        return Verdict(self.nom, bool(valeurs), "graphique sur résultat vide")


# Nombres écrits à la française : séparateur de milliers espace (y compris insécable ou
# fine), décimale virgule. On capte aussi la forme anglaise pour ne rien manquer.
_NOMBRE = re.compile(r"\d[\d   ]*(?:[.,]\d+)?")

# Un nombre isolé de 1 ou 2 chiffres est presque toujours un ordinal, une date ou un
# effectif de phrase ("les 3 canaux"), pas un chiffre extrait des données.
_PLANCHER_CHIFFRES = 3


@dataclass(frozen=True)
class TracabiliteNumerique:
    """Aucun chiffre du texte qui ne vienne d'un résultat SQL.

    C'est le contrat qui interdit à l'agent d'inventer un ordre de grandeur. La
    comparaison est tolérante aux arrondis de présentation (« 4,2 millions » pour
    4 231 07x) : on accepte un nombre s'il approche une valeur calculée, à n'importe
    quelle puissance de mille près.
    """

    tolerance: float = 0.02
    nom: str = "traçabilité des chiffres"

    def verifier(self, r: Resultat, con: duckdb.DuckDBPyConnection) -> Verdict:
        calculees = [v for q in r.sql for v in _valeurs_numeriques(q, con)]
        orphelins = []

        for brut in _NOMBRE.findall(r.reponse):
            texte = brut.strip().replace(" ", "").replace(" ", "")
            texte = texte.replace(" ", "").replace(",", ".")
            if texte.count(".") > 1 or len(texte.replace(".", "")) < _PLANCHER_CHIFFRES:
                continue
            try:
                n = float(texte)
            except ValueError:
                continue
            if not any(self._correspond(n, v) for v in calculees):
                orphelins.append(brut.strip())

        return Verdict(
            self.nom,
            not orphelins,
            f"non traçables : {orphelins[:5]}" if orphelins else "",
        )

    def _correspond(self, n: float, valeur: float) -> bool:
        """Vrai si `n` approche `valeur`, à un facteur mille près.

        Couvre les unités de présentation : « 4,2 millions » pour 4 231 07x.
        """
        for exposant in range(0, 4):
            echelle = 10 ** (3 * exposant)
            if _proche(n, valeur / echelle, self.tolerance):
                return True
            if math.isfinite(valeur) and _proche(n * echelle, valeur, self.tolerance):
                return True
        return False

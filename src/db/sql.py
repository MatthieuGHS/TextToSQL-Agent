"""Le seul point d'accès à la base.

Aucun autre module n'ouvre de connexion ni n'exécute de SQL. C'est ce qui rend les
garanties de ce fichier vérifiables **par lecture** plutôt que par confiance : il suffit
de lire ces quelques dizaines de lignes pour savoir tout ce que l'agent peut faire.

Quatre couches, de la plus solide à la plus fine :

1. **La connexion est durcie** (`src/db/connexion.py`) — appliqué par le moteur, ne
   dépend d'aucune analyse de chaîne.
2. **La requête est validée par le parseur de DuckDB**, pas par une expression
   régulière : une regex se laisse berner par un commentaire, une casse inhabituelle ou
   un mot-clé dans une chaîne littérale.
3. **Le résultat est borné** par enveloppement, et la troncature est *annoncée*.
4. **L'exécution est bornée dans le temps**, par interruption.

Les messages d'erreur font partie du produit : ils sont lus par le modèle, qui doit s'en
servir pour corriger sa requête et réessayer. Un message inexploitable transforme une
erreur récupérable en échec.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import duckdb

from src.db import connexion

logger = logging.getLogger(__name__)

LIMITE_LIGNES = 200
DELAI_SECONDES = 15.0

# Seul type d'instruction autorisé. `WITH … SELECT`, `DESCRIBE`, `SUMMARIZE` et `PRAGMA`
# sont tous typés SELECT par le parseur — lectures sans danger une fois l'accès externe
# fermé. Tout le reste (DROP, INSERT, ATTACH, INSTALL, COPY, SET, CALL…) est refusé.
TYPE_AUTORISE = duckdb.StatementType.SELECT


class SqlRefuse(Exception):
    """La requête n'est pas une lecture, ou en contient plusieurs."""


class SqlInvalide(Exception):
    """La requête ne s'analyse pas, ou échoue à l'exécution."""


class SqlTropLong(Exception):
    """L'exécution a dépassé le délai imparti."""


@dataclass(frozen=True)
class ResultatSql:
    colonnes: list[str]
    lignes: list[tuple]
    tronque: bool
    duree_ms: int

    def __len__(self) -> int:
        return len(self.lignes)


def _valider(query: str) -> None:
    """Refuse tout ce qui n'est pas exactement une lecture.

    Raises:
        SqlRefuse: instruction multiple, ou type non autorisé.
        SqlInvalide: la requête ne s'analyse pas.
    """
    try:
        instructions = duckdb.extract_statements(query)
    except Exception as exc:
        raise SqlInvalide(f"Requête non analysable : {exc}") from exc

    if not instructions:
        raise SqlInvalide("Requête vide.")

    if len(instructions) > 1:
        raise SqlRefuse(
            f"Une seule requête par appel ; {len(instructions)} instructions reçues. "
            f"Envoyer la première, puis la suivante au tour d'après."
        )

    type_recu = instructions[0].type
    if type_recu != TYPE_AUTORISE:
        raise SqlRefuse(
            f"Seules les requêtes de lecture sont autorisées ; instruction reçue : "
            f"{type_recu.name}. Utiliser SELECT."
        )


def _colonnes_de(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM duckdb_columns() "
            "WHERE table_name = ? ORDER BY column_index",
            [table],
        ).fetchall()
    ]


def _sans_enveloppe(message: str, query: str) -> str:
    """Retire l'enveloppe de plafonnement des extraits cités par le moteur.

    DuckDB recopie la requête fautive dans son message. Telle quelle, elle contiendrait
    le `SELECT * FROM (…) LIMIT n` que nous avons ajouté — le modèle verrait une requête
    qu'il n'a pas écrite et pourrait chercher à corriger un LIMIT qui n'est pas de lui.
    """
    lignes = []
    for ligne in message.splitlines():
        if ligne.startswith("LINE ") and query.split()[0] in ligne:
            lignes.append(f"Requête : {' '.join(query.split())}")
        elif ligne.strip().startswith("^"):
            continue
        else:
            lignes.append(ligne)
    return "\n".join(lignes).strip()


def _enrichir_erreur(
    con: duckdb.DuckDBPyConnection, exc: Exception, query: str
) -> str:
    """Ajoute à l'erreur du moteur de quoi la corriger.

    Une colonne inconnue est l'erreur la plus fréquente d'un modèle : lui renvoyer la
    liste réelle des colonnes lui permet de se reprendre au tour suivant, là où le
    message brut le laisserait deviner.
    """
    message = _sans_enveloppe(str(exc), query)
    tables = _set_tables(con)
    citees = [t for t in tables if t.lower() in message.lower()]

    if "column" in message.lower() or "referenced" in message.lower():
        cible = citees[0] if citees else None
        if cible:
            return f"{message}\nColonnes de {cible} : {', '.join(_colonnes_de(con, cible))}"
        return f"{message}\nTables disponibles : {', '.join(sorted(tables))}"

    if "table" in message.lower() and not citees:
        return f"{message}\nTables disponibles : {', '.join(sorted(tables))}"

    return message


def _set_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}


def _executer_borne(
    con: duckdb.DuckDBPyConnection, query: str, delai: float, origine: str
) -> tuple[list[str], list[tuple]]:
    """Exécute dans un fil, interrompt au-delà du délai.

    `interrupt()` porte sur la connexion, pas sur la requête : en usage concurrent il
    faudra une connexion par appel. Suffisant tant qu'un seul appel est en vol.
    """
    resultat: dict = {}

    def travail() -> None:
        try:
            curseur = con.execute(query)
            resultat["colonnes"] = [d[0] for d in curseur.description]
            resultat["lignes"] = curseur.fetchall()
        except Exception as exc:  # noqa: BLE001 — relayée telle quelle à l'appelant
            resultat["erreur"] = exc

    fil = threading.Thread(target=travail, daemon=True)
    fil.start()
    fil.join(timeout=delai)

    if fil.is_alive():
        con.interrupt()
        fil.join(timeout=5.0)
        raise SqlTropLong(
            f"Requête interrompue après {delai:.0f} s. Elle croise probablement deux "
            f"tables sans condition de jointure, ou balaie trop de lignes : ajouter une "
            f"condition sur step_date, ou agréger."
        )

    if "erreur" in resultat:
        erreur = resultat["erreur"]
        raise SqlInvalide(_enrichir_erreur(con, erreur, origine)) from erreur

    return resultat["colonnes"], resultat["lignes"]


def run_sql(
    query: str,
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    limite: int = LIMITE_LIGNES,
    delai: float = DELAI_SECONDES,
) -> ResultatSql:
    """Valide, borne et exécute une requête de lecture.

    Args:
        con: connexion déjà ouverte. Si absente, une connexion durcie est ouverte pour
            l'appel puis refermée — pratique en ligne de commande, coûteux en boucle.

    Raises:
        SqlRefuse: la requête n'est pas une lecture unique.
        SqlInvalide: elle ne s'analyse pas, ou échoue.
        SqlTropLong: elle dépasse le délai.
    """
    _valider(query)

    propre = con is None
    con = con or connexion.ouvrir()
    try:
        # Envelopper plutôt qu'injecter : un LIMIT ajouté à la main casserait sur une
        # requête qui en contient déjà un, ou dont la dernière clause est un ORDER BY
        # dans une CTE. `limite + 1` sert à détecter la troncature — une troncature
        # silencieuse ferait croire au modèle qu'il a tout vu, et il énoncerait un
        # total faux.
        enveloppe = f"SELECT * FROM ({query.rstrip().rstrip(';')}) LIMIT {limite + 1}"

        debut = time.monotonic()
        colonnes, lignes = _executer_borne(con, enveloppe, delai, query)
        duree_ms = int((time.monotonic() - debut) * 1000)
    finally:
        if propre:
            con.close()

    tronque = len(lignes) > limite
    logger.info(
        "sql %4d ms · %3d ligne(s)%s · %s",
        duree_ms, min(len(lignes), limite), " (tronqué)" if tronque else "",
        " ".join(query.split())[:120],
    )

    return ResultatSql(colonnes, lignes[:limite], tronque, duree_ms)


def en_texte(resultat: ResultatSql, largeur_max: int = 40) -> str:
    """Rend le résultat lisible par le modèle.

    Volontairement séparé de l'exécution : le harnais d'évaluation a besoin des valeurs,
    l'agent d'un tableau. Les mélanger obligerait à analyser du texte pour retrouver des
    nombres.
    """
    if not resultat.lignes:
        return "Résultat vide : aucune ligne ne correspond."

    def cellule(v) -> str:
        texte = "NULL" if v is None else str(v)
        return texte if len(texte) <= largeur_max else texte[: largeur_max - 1] + "…"

    lignes = [[cellule(v) for v in ligne] for ligne in resultat.lignes]
    largeurs = [
        max(len(resultat.colonnes[i]), *(len(l[i]) for l in lignes))
        for i in range(len(resultat.colonnes))
    ]

    rendu = [
        " | ".join(c.ljust(largeurs[i]) for i, c in enumerate(resultat.colonnes)),
        "-+-".join("-" * w for w in largeurs),
        *(" | ".join(c.ljust(largeurs[i]) for i, c in enumerate(l)) for l in lignes),
    ]

    if resultat.tronque:
        rendu.append(
            f"\n({len(resultat.lignes)} premières lignes affichées, il y en a davantage — "
            f"affiner la requête ou agréger avant de conclure.)"
        )
    return "\n".join(rendu)

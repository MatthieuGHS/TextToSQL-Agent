"""L'API HTTP. Une coquille mince — aucune logique métier.

La règle que ce fichier s'impose, et qui est la seule qui compte ici : **si une ligne
décide quelque chose sur les données plutôt que sur le transport, elle est au mauvais
endroit.** Tout ce qui juge, borne ou interprète vit dans `src/agent` et `src/db`. Ce
module traduit du HTTP en appel de fonction, et une réponse en JSON.

Deux points d'entrée pour une même question, et c'est délibéré :

- `POST /api/question` rend la réponse complète. C'est le contrat simple, celui qu'un
  client tiers utiliserait.
- `POST /api/question/flux` rend les étapes au fil de l'eau, puis la réponse. Une question
  demande dix à trente secondes ; une interface qui n'a rien à montrer pendant ce temps
  passe pour figée.

Le second n'est pas une variante du premier : il appelle la même fonction du noyau, avec
un traceur en plus. Aucune logique n'est dupliquée, et `ask()` ne sait pas qu'un des deux
appelants diffuse.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import queue
import shutil
import tempfile
import threading
from dataclasses import replace

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.agent import boucle
from src.agent.reponse import Echange
from src.app import schemas, serialisation
from src.db import connexion
from src.etl import build_db, checks, transforms

logger = logging.getLogger(__name__)

RACINE_WEB = connexion.ROOT / "web" / "dist"

# Message rendu au client quand une exception inattendue traverse. Volontairement muet sur
# la cause : le détail part au journal, côté serveur. Un message d'erreur qui recopie une
# trace d'exécution finit par exposer un chemin de fichier ou un extrait de requête.
TEXTE_ERREUR_INTERNE = (
    "Une erreur interne est survenue. La question n'a pas pu être traitée."
)

# Le même principe, pour l'autre chemin. Un texte distinct parce qu'un message qui parle
# de « question » sur une page de chargement de fichiers désoriente plus qu'il n'informe.
TEXTE_ERREUR_PIPELINE = (
    "Une erreur interne est survenue pendant la reconstruction. Les sources et la base "
    "précédentes sont intactes."
)

application = FastAPI(
    title="Agent data — API",
    description="Accès HTTP au noyau conversationnel. Aucune logique métier ici.",
)


def _agent_pour_la_requete() -> tuple[boucle.Agent, object]:
    """Un agent par requête, mais **une seule connexion par requête**.

    `interrupt()` de DuckDB porte sur la connexion et non sur la requête : deux questions
    en vol sur une connexion partagée, et le dépassement de délai de l'une interromprait
    l'autre. C'est la décision que `CLAUDE.md` laissait ouverte depuis E4, tranchée ici.

    Le prompt et son empreinte restent ceux de l'agent partagé — donc construits une fois.
    Les regénérer par requête coûterait une lecture complète du schéma et, surtout,
    rouvrirait le risque d'instabilité du préfixe mis en cache, qui est la moitié de
    l'économie du projet. Seule la connexion est propre à l'appel.
    """
    partage = boucle.agent_par_defaut()
    con = connexion.ouvrir()
    return replace(partage, con=con), con


def _historique(entrants: list[schemas.EchangeEntrant]) -> list[Echange]:
    return [Echange(question=e.question, reponse=e.reponse) for e in entrants]


@application.get("/api/sante", response_model=schemas.Sante)
def sante() -> schemas.Sante:
    """Ce qu'on vérifie avant une démonstration.

    Les trois causes de panne au démarrage sont ici : base absente, clé non chargée,
    schéma inattendu. Les découvrir sur la première question coûte bien plus cher.
    """
    tables: list[str] = []
    base = False
    try:
        con = connexion.ouvrir()
        try:
            tables = sorted(
                r[0] for r in con.execute(
                    "SELECT table_name FROM duckdb_tables()"
                ).fetchall()
            )
            base = True
        finally:
            con.close()
    except FileNotFoundError:
        pass

    return schemas.Sante(
        base_presente=base,
        cle_chargee=bool(os.getenv("ANTHROPIC_API_KEY")),
        modele=boucle.MODELE,
        tables=tables,
    )


@application.post("/api/question", response_model=schemas.ReponseSortante)
def question(entree: schemas.QuestionEntrante) -> schemas.ReponseSortante:
    """La réponse complète, en un aller-retour."""
    agent, con = _agent_pour_la_requete()
    try:
        reponse = boucle.ask(entree.question, _historique(entree.historique), agent=agent)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("question en échec")
        raise HTTPException(status_code=500, detail=TEXTE_ERREUR_INTERNE) from exc
    finally:
        con.close()

    return serialisation.reponse(reponse)


@application.post("/api/question/flux")
def question_en_flux(entree: schemas.QuestionEntrante) -> StreamingResponse:
    """Les étapes au fil de l'eau, puis la réponse. Une ligne de JSON par événement.

    NDJSON plutôt que le format `text/event-stream` : l'interface lit ce flux avec
    `fetch`, pas avec `EventSource` — qui ne sait faire que du GET, donc qui obligerait à
    faire voyager la conversation dans l'URL. Sans `EventSource`, le cadrage SSE
    n'apporterait rien qu'un séparateur de lignes ne donne déjà.

    `ask()` est bloquante : elle tourne dans un fil, et pousse ses étapes dans une file
    que le générateur vide. C'est ce qui permet de diffuser sans rendre le noyau
    asynchrone — une réécriture qui n'aurait servi qu'à ça.
    """
    file: queue.Queue = queue.Queue()

    def tracer(etape: str, detail: dict) -> None:
        file.put({"type": etape, **detail})

    def travail() -> None:
        agent, con = _agent_pour_la_requete()
        try:
            reponse = boucle.ask(
                entree.question, _historique(entree.historique),
                agent=agent, trace=tracer,
            )
            file.put({
                "type": "reponse",
                "reponse": serialisation.reponse(reponse).model_dump(),
            })
        except Exception:
            # Le flux a déjà commencé : le code de statut est parti, on ne peut plus
            # rendre une 500. L'erreur devient donc un événement, que l'interface doit
            # savoir afficher — c'est le prix du streaming, et il est explicite.
            logger.exception("question en flux en échec")
            file.put({"type": "erreur", "message": TEXTE_ERREUR_INTERNE})
        finally:
            con.close()
            file.put(None)

    threading.Thread(target=travail, daemon=True).start()

    def evenements():
        while (evenement := file.get()) is not None:
            yield json.dumps(evenement, ensure_ascii=False) + "\n"

    return StreamingResponse(evenements(), media_type="application/x-ndjson")


# --- Données sources et reconstruction -----------------------------------------------
#
# La pipeline est **une reconstruction complète** depuis `data/raw/`, pas un ajout
# incrémental : téléverser un fichier remplace sa version et rejoue tout. C'est le
# comportement de `build_db` depuis E1, et le changer serait un autre sujet que celui-ci.

# Seuls ces noms sont acceptés. La pipeline les code en dur : accepter un autre nom serait
# silencieusement inutile — le fichier serait écrit puis ignoré, et l'utilisateur croirait
# avoir chargé ses données. Le verrou a un second effet, plus important : aucun nom de
# fichier ne vient de l'utilisateur, donc aucune traversée de chemin n'est possible.
FICHIERS_ATTENDUS = {
    **{nom: True for nom in build_db.SOURCES.values()},
    build_db.MASTER_SOURCE: False,
}

# Un CSV de sources pèse quelques dizaines de mégaoctets. Le plafond n'est pas là pour
# protéger le disque mais pour que l'erreur arrive vite : un fichier de 2 Go téléversé
# puis refusé par la pipeline aurait fait attendre pour rien.
TAILLE_MAX = 200 * 1024 * 1024

# Une reconstruction à la fois. Deux simultanées se disputeraient le même fichier
# temporaire, et la seconde publierait une base bâtie sur les sources de la première.
_verrou_reconstruction = threading.Lock()


def _etat_fichier(nom: str, requis: bool) -> schemas.FichierSource:
    chemin = build_db.RAW_DIR / nom
    existe = chemin.is_file()
    return schemas.FichierSource(
        nom=nom,
        present=existe,
        octets=chemin.stat().st_size if existe else None,
        modifie_le=(
            datetime.datetime.fromtimestamp(chemin.stat().st_mtime).isoformat(
                timespec="seconds"
            )
            if existe
            else None
        ),
        requis=requis,
    )


@application.get("/api/donnees", response_model=schemas.EtatDonnees)
def donnees() -> schemas.EtatDonnees:
    """Ce que la page de chargement affiche : quels fichiers sont là, et depuis quand."""
    base = connexion.chemin_base()
    tables: list[str] = []
    if base.exists():
        con = connexion.ouvrir()
        try:
            tables = sorted(
                r[0] for r in con.execute(
                    "SELECT table_name FROM duckdb_tables()"
                ).fetchall()
            )
        finally:
            con.close()

    return schemas.EtatDonnees(
        fichiers=[_etat_fichier(n, r) for n, r in FICHIERS_ATTENDUS.items()],
        base_presente=base.exists(),
        base_modifiee_le=(
            datetime.datetime.fromtimestamp(base.stat().st_mtime).isoformat(
                timespec="seconds"
            )
            if base.exists()
            else None
        ),
        tables=tables,
    )


class _JournalVersFile(logging.Handler):
    """Relaie le journal de l'ETL vers le flux de la réponse.

    L'ETL journalise déjà ce qu'il faut montrer — volumétrie par table, invariants,
    avertissements du contrat de données. Le rediffuser tel quel vaut mieux que de
    réinventer une notion d'avancement à côté : ce que l'exploitant lit dans un terminal
    est exactement ce que l'utilisateur doit voir.
    """

    def __init__(self, file: queue.Queue):
        super().__init__()
        self.file = file

    def emit(self, enregistrement: logging.LogRecord) -> None:
        self.file.put({
            "type": "journal",
            "niveau": enregistrement.levelname.lower(),
            "message": self.format(enregistrement),
        })


@application.post("/api/donnees/recharger")
async def recharger(fichiers: list[UploadFile] = File(default=[])) -> StreamingResponse:
    """Téléverse les fichiers reçus, reconstruit la base, et diffuse le journal.

    Le dossier d'attente est le cœur du geste : on y recopie les sources actuelles, on y
    écrit les fichiers reçus, et **on ne promeut le tout qu'une fois la construction
    réussie**. Un fichier mal formé laisse donc `data/raw/` et la base exactement dans
    l'état où ils étaient — et l'utilisateur peut renvoyer le bon sans rien réparer.

    Sans fichier, c'est une simple reconstruction depuis les sources en place. C'est
    utile : le contrat de données peut se mettre à échouer sans qu'aucune source ait
    bougé, si le code de vérification, lui, a changé.
    """
    recus: dict[str, bytes] = {}
    for fichier in fichiers:
        nom = pathlib.Path(fichier.filename or "").name
        if nom not in FICHIERS_ATTENDUS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"« {nom} » n'est pas un fichier de cette pipeline. Attendus : "
                    f"{', '.join(sorted(FICHIERS_ATTENDUS))}."
                ),
            )
        contenu = await fichier.read()
        if len(contenu) > TAILLE_MAX:
            raise HTTPException(
                status_code=413,
                detail=f"{nom} dépasse {TAILLE_MAX // (1024 * 1024)} Mo.",
            )
        recus[nom] = contenu

    file: queue.Queue = queue.Queue()

    def travail() -> None:
        if not _verrou_reconstruction.acquire(blocking=False):
            file.put({"type": "erreur", "message":
                      "Une reconstruction est déjà en cours."})
            file.put(None)
            return

        journal = _JournalVersFile(file)
        journal.setFormatter(logging.Formatter("%(message)s"))
        etl = logging.getLogger("etl")
        niveau = etl.level
        etl.addHandler(journal)
        etl.setLevel(logging.INFO)

        attente = pathlib.Path(tempfile.mkdtemp(prefix="sources-"))
        try:
            for nom in FICHIERS_ATTENDUS:
                courant = build_db.RAW_DIR / nom
                if courant.is_file():
                    (attente / nom).write_bytes(courant.read_bytes())
            for nom, contenu in recus.items():
                (attente / nom).write_bytes(contenu)

            build_db.construire(connexion.chemin_base(), attente)

            # Promotion : la base est écrite et validée, les sources qui l'ont produite
            # deviennent les sources de référence. Dans cet ordre, jamais l'inverse.
            build_db.RAW_DIR.mkdir(parents=True, exist_ok=True)
            for nom in recus:
                (build_db.RAW_DIR / nom).write_bytes((attente / nom).read_bytes())

            # Le prompt est généré depuis le schéma : sans cet oubli volontaire, l'agent
            # continuerait de décrire l'ancienne base. C'est le défaut silencieux le plus
            # grave de cette page.
            boucle.reinitialiser()
            file.put({"type": "termine", "fichiers": sorted(recus)})
        except Exception as exc:
            logger.exception("reconstruction en échec")
            file.put({"type": "erreur", "message": _cause_lisible(exc)})
        finally:
            etl.removeHandler(journal)
            etl.setLevel(niveau)
            shutil.rmtree(attente, ignore_errors=True)
            _verrou_reconstruction.release()
            file.put(None)

    threading.Thread(target=travail, daemon=True).start()

    def evenements():
        while (evenement := file.get()) is not None:
            yield json.dumps(evenement, ensure_ascii=False) + "\n"

    return StreamingResponse(evenements(), media_type="application/x-ndjson")


def _cause_lisible(exc: Exception) -> str:
    """Ce qu'on rend à l'utilisateur quand la pipeline refuse.

    Contrairement à une panne de l'agent, les échecs d'ETL **sont** l'information utile :
    une violation du contrat dit quelle règle et sur quelle table, une colonne absente dit
    laquelle. C'est ce qu'il faut corriger dans le fichier source, et le taire renverrait
    l'utilisateur à un « ça n'a pas marché » sans recours.

    La liste est close et ne contient que des causes **que l'utilisateur peut corriger**.
    Elle a été établie sur l'échec réel du premier essai — un CSV aux mauvaises colonnes
    rendait « une erreur interne est survenue », ce qui était à la fois faux et inutile.
    Tout ce qui n'y figure pas reste muet : une panne interne recopiée dans une réponse
    exposerait des chemins de fichiers.
    """
    if isinstance(exc, (checks.DataQualityError, transforms.ContextColumnError)):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return f"Fichier source manquant : {exc}"
    if isinstance(exc, KeyError):
        # `transforms` indexe les colonnes par leur nom : une clé absente *est* une colonne
        # absente. Nommer laquelle est ce qui distingue un message utile d'un constat.
        return (
            f"Colonne absente d'un fichier source : {exc}. Vérifier que le fichier "
            f"exporté porte bien les colonnes attendues par la pipeline."
        )
    if isinstance(exc, UnicodeDecodeError):
        return (
            "Fichier illisible : encodage inattendu. Les sources sont attendues en UTF-8."
        )
    if isinstance(exc, pd.errors.EmptyDataError):
        return "Fichier vide."
    if isinstance(exc, pd.errors.ParserError):
        return f"Fichier CSV mal formé : {exc}"
    return TEXTE_ERREUR_PIPELINE


# Monté en dernier : la racine attrape tout ce qui n'est pas `/api/…`, et l'ordre de
# déclaration décide. Monté seulement si le build existe — en développement, c'est Vite
# qui sert l'interface et relaie `/api` vers ici.
if RACINE_WEB.is_dir():
    application.mount(
        "/", StaticFiles(directory=RACINE_WEB, html=True), name="interface"
    )

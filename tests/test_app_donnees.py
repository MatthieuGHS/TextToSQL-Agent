"""Tests du chargement de données par l'interface. Aucun appel API modèle.

Ce que cette page peut casser est d'une autre nature que le reste : elle **écrit**. Les
trois propriétés qui comptent, dans l'ordre :

1. **Un échec ne dégrade rien.** Ni les sources en place, ni la base précédente. C'est le
   rôle du dossier d'attente, et c'est ce qui permet de renvoyer le bon fichier sans avoir
   à réparer quoi que ce soit d'abord.
2. **Aucun nom de fichier ne vient de l'utilisateur.** La liste est close, ce qui ferme la
   traversée de chemin en même temps que les fichiers silencieusement inutiles.
3. **L'agent est oublié après un rechargement.** Sans quoi il continuerait de décrire
   l'ancien schéma, sans rien lever.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.app import api
from src.etl import build_db, checks


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Un `data/raw/` d'essai, avec les quatre fichiers attendus.

    Contenu inventé, comme partout ailleurs : aucune valeur ne vient des données client.
    """
    raw = tmp_path / "raw"
    raw.mkdir()
    for nom in api.FICHIERS_ATTENDUS:
        (raw / nom).write_text(f"colonne\nvaleur-{nom}\n", encoding="utf-8")
    monkeypatch.setattr(build_db, "RAW_DIR", raw)
    return raw


@pytest.fixture
def client(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(api.connexion, "chemin_base", lambda: tmp_path / "mmm.duckdb")
    return TestClient(api.application)


def _evenements(reponse) -> list[dict]:
    return [json.loads(l) for l in reponse.text.splitlines() if l.strip()]


# --- 1. La liste des fichiers est close -----------------------------------------------


def test_un_nom_inattendu_est_refuse_avec_la_liste_des_noms_attendus(client):
    """Refusé, et refusé *utilement* : sans la liste, l'utilisateur devine.

    La pipeline code ces noms en dur. Accepter autre chose écrirait un fichier qui serait
    ensuite ignoré, et l'utilisateur croirait avoir chargé ses données.
    """
    r = client.post(
        "/api/donnees/recharger",
        files={"fichiers": ("mes_donnees.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert r.status_code == 422
    assert "features_cost.csv" in r.json()["detail"]


def test_une_traversee_de_chemin_est_refusee(client, sources, tmp_path):
    """Conséquence de la liste close, vérifiée plutôt qu'affirmée."""
    piege = tmp_path / "vole.csv"
    r = client.post(
        "/api/donnees/recharger",
        files={"fichiers": ("../../vole.csv", b"x\n1\n", "text/csv")},
    )

    assert r.status_code == 422
    assert not piege.exists()


def test_le_fichier_maitre_est_accepte_mais_signale_non_requis(client):
    """`features.csv` n'est pas un intrant : il sert à vérifier la couverture.

    Le présenter comme requis ferait croire à un chargement incomplet quand il manque.
    """
    etat = client.get("/api/donnees").json()
    par_nom = {f["nom"]: f for f in etat["fichiers"]}

    assert par_nom[build_db.MASTER_SOURCE]["requis"] is False
    assert par_nom[build_db.SOURCES["media"]]["requis"] is True


# --- 2. Un échec ne dégrade rien ------------------------------------------------------


def test_un_echec_laisse_les_sources_en_place(client, sources, monkeypatch):
    """Le cœur du dossier d'attente.

    Sans lui, un CSV mal formé écraserait la source précédente, et il faudrait la
    retrouver avant de pouvoir réessayer. Ici, on renvoie simplement le bon fichier.
    """
    avant = (sources / "features_cost.csv").read_text(encoding="utf-8")

    def construction_en_echec(*a, **kw):
        raise checks.DataQualityError("media : 3 lignes de coût négatif")

    monkeypatch.setattr(api.build_db, "construire", construction_en_echec)

    r = client.post(
        "/api/donnees/recharger",
        files={"fichiers": ("features_cost.csv", b"colonne\ncassee\n", "text/csv")},
    )
    evenements = _evenements(r)

    assert evenements[-1]["type"] == "erreur"
    assert (sources / "features_cost.csv").read_text(encoding="utf-8") == avant


def test_la_cause_du_refus_est_rendue_a_l_utilisateur(client, monkeypatch):
    """Contrairement à une panne d'agent, un échec d'ETL *est* l'information utile.

    Une violation du contrat dit quelle règle et sur quelle table : c'est ce qu'il faut
    corriger dans le fichier source. La taire renverrait à un « ça n'a pas marché » sans
    recours.
    """
    def construction_en_echec(*a, **kw):
        raise checks.DataQualityError("media : 3 lignes de coût négatif")

    monkeypatch.setattr(api.build_db, "construire", construction_en_echec)

    r = client.post("/api/donnees/recharger", files=[])

    assert "coût négatif" in _evenements(r)[-1]["message"]


def test_une_panne_interne_reste_muette(client, monkeypatch):
    """Une trace d'exécution rendue au client exposerait des chemins de fichiers."""
    def panne(*a, **kw):
        raise RuntimeError("/home/secret/interne.py a explosé")

    monkeypatch.setattr(api.build_db, "construire", panne)

    r = client.post("/api/donnees/recharger", files=[])
    message = _evenements(r)[-1]["message"]

    assert "secret" not in message
    assert message == api.TEXTE_ERREUR_INTERNE


# --- 3. Le succès promeut, et oublie l'agent ------------------------------------------


def test_un_succes_promeut_les_fichiers_et_oublie_l_agent(client, sources, monkeypatch):
    """La promotion vient **après** la construction, jamais avant.

    Et l'agent est oublié dans le même geste : son prompt décrit un schéma qui vient de
    changer. C'est le défaut le plus grave de cette page, parce qu'il ne lève rien.
    """
    vus = {}

    def construction_reussie(out, raw_dir=None, **kw):
        # Au moment de construire, l'attente porte le nouveau fichier et `data/raw/` non :
        # c'est exactement l'ordre qu'on veut vérifier.
        vus["attente"] = (raw_dir / "features_cost.csv").read_text(encoding="utf-8")
        vus["raw"] = (sources / "features_cost.csv").read_text(encoding="utf-8")
        return out

    oublis = []
    monkeypatch.setattr(api.build_db, "construire", construction_reussie)
    monkeypatch.setattr(api.boucle, "reinitialiser", lambda: oublis.append(1))

    r = client.post(
        "/api/donnees/recharger",
        files={"fichiers": ("features_cost.csv", b"colonne\nneuf\n", "text/csv")},
    )
    evenements = _evenements(r)

    assert vus["attente"] == "colonne\nneuf\n"
    assert vus["raw"] == "colonne\nvaleur-features_cost.csv\n"
    assert (sources / "features_cost.csv").read_text(encoding="utf-8") == "colonne\nneuf\n"
    assert evenements[-1]["type"] == "termine"
    assert oublis == [1], "l'agent partagé doit être oublié après un rechargement"


def test_l_agent_n_est_pas_oublie_quand_la_construction_echoue(client, monkeypatch):
    """Contre-épreuve : oublier l'agent sur un échec le ferait reconstruire pour rien.

    La base n'a pas changé — le prompt est toujours valide.
    """
    oublis = []
    monkeypatch.setattr(api.build_db, "construire",
                        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("x")))
    monkeypatch.setattr(api.boucle, "reinitialiser", lambda: oublis.append(1))

    client.post("/api/donnees/recharger", files=[])

    assert oublis == []


def test_le_journal_de_l_etl_est_diffuse(client, monkeypatch):
    """Ce que l'ETL journalise déjà — volumétrie, invariants — est ce qu'il faut montrer.

    Réinventer une notion d'avancement à côté aurait produit un affichage plus pauvre que
    ce que l'exploitant lit dans son terminal.
    """
    def construction_bavarde(out, raw_dir=None, **kw):
        api.build_db.logger.info("media     table construite            %6d lignes", 1234)
        return out

    monkeypatch.setattr(api.build_db, "construire", construction_bavarde)
    monkeypatch.setattr(api.boucle, "reinitialiser", lambda: None)

    evenements = _evenements(client.post("/api/donnees/recharger", files=[]))
    journaux = [e for e in evenements if e["type"] == "journal"]

    assert any("1234" in e["message"] for e in journaux)

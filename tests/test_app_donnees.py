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
from src.etl import build_db, checks, rechargement


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


@pytest.mark.parametrize(
    "exception, attendu",
    [
        (KeyError("type"), "Colonne absente"),
        (checks.DataQualityError("media : coût négatif"), "coût négatif"),
        (FileNotFoundError("compteurs.csv"), "Fichier source manquant"),
    ],
)
def test_les_causes_corrigeables_sont_nommees(client, monkeypatch, exception, attendu):
    """La liste a été établie sur un échec réel, pas par anticipation.

    Au premier essai bout en bout, un CSV aux mauvaises colonnes rendait « une erreur
    interne est survenue » — faux et inutile, alors que pandas savait exactement quelle
    colonne manquait. C'est l'échec le plus probable au chargement d'un nouvel extrait.
    """
    def echec(*a, **kw):
        raise exception

    monkeypatch.setattr(api.build_db, "construire", echec)

    r = client.post("/api/donnees/recharger", files=[])

    assert attendu in _evenements(r)[-1]["message"]


def test_une_panne_interne_reste_muette(client, monkeypatch):
    """Une trace d'exécution rendue au client exposerait des chemins de fichiers."""
    def panne(*a, **kw):
        raise RuntimeError("/home/secret/interne.py a explosé")

    monkeypatch.setattr(api.build_db, "construire", panne)

    r = client.post("/api/donnees/recharger", files=[])
    message = _evenements(r)[-1]["message"]

    assert "secret" not in message
    # Le texte de la pipeline, pas celui des questions : un message qui parle de
    # « question » sur une page de chargement de fichiers désoriente plus qu'il n'informe.
    assert message == api.TEXTE_ERREUR_PIPELINE


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


def test_une_panne_avant_la_construction_rend_le_verrou(client, monkeypatch):
    """Le sentinelle et le verrou survivent à une exception hors du `try` intérieur.

    Vu en relecture sur le flux de question : une exception levée avant le `try`
    emportait le fil sans sentinelle ni libération. Ici, la même faute aurait un effet
    pire — plus aucune reconstruction possible, chaque tentative répondant « déjà en
    cours » pour toujours.
    """
    monkeypatch.setattr(
        api, "_JournalVersFile",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("panne d'installation")),
    )

    premier = _evenements(client.post("/api/donnees/recharger", files=[]))
    assert premier[-1]["type"] == "erreur"
    assert premier[-1]["message"] == api.TEXTE_ERREUR_PIPELINE

    # Contre-épreuve du verrou : sans libération, ce second appel dirait
    # « une reconstruction est déjà en cours ».
    monkeypatch.undo()
    monkeypatch.setattr(api.build_db, "construire", lambda out, raw_dir=None, **kw: out)
    monkeypatch.setattr(api.boucle, "reinitialiser", lambda: None)
    second = _evenements(client.post("/api/donnees/recharger", files=[]))

    assert second[-1]["type"] == "termine"


# --- 4. La pipeline de rechargement, sans HTTP ----------------------------------------


def test_reconstruire_depuis_promeut_apres_construction(sources, tmp_path, monkeypatch):
    """La garantie du dossier d'attente se teste ici, sans passer par la frontière."""
    def construction_reussie(out, raw_dir=None, **kw):
        assert (raw_dir / "features_cost.csv").read_bytes() == b"colonne\nneuf\n"
        return out

    monkeypatch.setattr(build_db, "construire", construction_reussie)

    rechargement.reconstruire_depuis(
        {"features_cost.csv": b"colonne\nneuf\n"}, tmp_path / "mmm.duckdb"
    )

    assert (sources / "features_cost.csv").read_bytes() == b"colonne\nneuf\n"


def test_reconstruire_depuis_ne_promeut_rien_sur_echec(sources, tmp_path, monkeypatch):
    avant = (sources / "features_cost.csv").read_bytes()
    monkeypatch.setattr(
        build_db, "construire",
        lambda *a, **kw: (_ for _ in ()).throw(checks.DataQualityError("invariant")),
    )

    with pytest.raises(checks.DataQualityError):
        rechargement.reconstruire_depuis(
            {"features_cost.csv": b"colonne\ncassee\n"}, tmp_path / "mmm.duckdb"
        )

    assert (sources / "features_cost.csv").read_bytes() == avant


def test_reconstruire_depuis_refuse_un_nom_hors_pipeline(tmp_path):
    """Ceinture sous les bretelles de l'API : la pipeline vérifie aussi la liste close."""
    with pytest.raises(ValueError):
        rechargement.reconstruire_depuis(
            {"vole.csv": b"x\n"}, tmp_path / "mmm.duckdb", raw_dir=tmp_path
        )


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

"""Tests de l'API. Toujours aucun appel API modèle.

L'invariant du dépôt tient ici pour la même raison qu'ailleurs : l'agent est injecté. On
substitue l'agent partagé par un agent monté sur le faux modèle d'E4, et toute la
frontière HTTP se couvre gratuitement.

Ce qui se joue, par ordre d'importance :

1. **La sérialisation** — c'est le seul endroit où une erreur ne donne pas un chiffre
   faux mais une panne. `Decimal` et `date` sont les deux types que DuckDB rend le plus
   souvent, et aucun des deux n'est du JSON.
2. **Le flux** — les étapes doivent partir avant la réponse, sinon il ne sert à rien.
3. **Ce que la frontière expose** — les lignes brutes, sans lesquelles ni tableau ni
   graphique ne sont possibles.
"""

from __future__ import annotations

import datetime
import decimal
import json
import pathlib

import duckdb
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage

from src.agent import boucle
from src.app import api, serialisation
from src.db import connexion
from tests.test_agent_boucle import ModeleScripte, appel_sql, texte


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> pathlib.Path:
    chemin = tmp_path_factory.mktemp("api") / "test.duckdb"
    con = duckdb.connect(str(chemin))
    con.execute(
        "CREATE TABLE media AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'tv', CAST(1000.50 AS DECIMAL(10,2))), "
        "(DATE '2024-09-09', 'sea', CAST(250.25 AS DECIMAL(10,2)))"
        ") t(step_date, channel, cost)"
    )
    con.close()
    return chemin


@pytest.fixture
def client(base, monkeypatch):
    """Substitue l'agent partagé, et la base, par ceux de l'essai.

    `_agent_pour_la_requete` ouvre sa propre connexion : c'est la décision de conception
    qu'on veut exercer, pas contourner. On redirige donc `connexion.ouvrir` vers la base
    d'essai plutôt que d'injecter une connexion toute faite.
    """
    # La vraie fonction est capturée **avant** d'être remplacée : sans ça la lambda
    # s'appelle elle-même, et la récursion se manifeste par un blocage, pas par une erreur
    # lisible.
    ouvrir = connexion.ouvrir
    monkeypatch.setattr(api.connexion, "ouvrir", lambda *a, **k: ouvrir(base))

    def agent_bouchon():
        return boucle.Agent(
            modele=ModeleScripte([
                appel_sql("SELECT step_date, cost FROM media ORDER BY step_date"),
                texte("Les dépenses sont de 1 000,50 € puis 250,25 €."),
            ]),
            systeme=SystemMessage(content="prompt d'essai"),
            empreinte_prompt="essai00000ab",
        )

    monkeypatch.setattr(api.boucle, "agent_par_defaut", agent_bouchon)
    return TestClient(api.application)


# --- 1. La sérialisation --------------------------------------------------------------


@pytest.mark.parametrize(
    "valeur, attendu",
    [
        (decimal.Decimal("1000.50"), 1000.5),
        (datetime.date(2024, 9, 2), "2024-09-02"),
        (datetime.datetime(2024, 9, 2, 14, 30), "2024-09-02T14:30:00"),
        (datetime.timedelta(seconds=90), 90.0),
        (True, True),
        (None, None),
        (b"\x00\xff", "00ff"),
        ((1, decimal.Decimal("2")), [1, 2.0]),
    ],
)
def test_les_types_de_duckdb_deviennent_du_json(valeur, attendu):
    """Chacun de ces types est rendu par une requête ordinaire sur ce schéma.

    Un oubli ici ne produit pas un chiffre faux, il produit une panne — d'où la couverture
    exhaustive plutôt qu'un échantillon.
    """
    obtenu = serialisation.cellule(valeur)

    assert obtenu == attendu
    json.dumps(obtenu)  # la vraie assertion : ça doit passer le sérialiseur


def test_un_booleen_ne_devient_pas_un_entier():
    """Contre-épreuve d'ordre : `bool` est un `int` en Python.

    Tester `int` avant `bool` rendrait `True` en `1`. La colonne d'un `IS NULL` ou d'une
    comparaison s'afficherait alors en chiffres, sans que rien ne lève.
    """
    assert serialisation.cellule(True) is True
    assert serialisation.cellule(False) is False


def test_un_horodatage_n_est_pas_tronque_a_sa_date():
    """Contre-épreuve d'ordre, l'autre sens : `datetime` est une `date`."""
    assert serialisation.cellule(
        datetime.datetime(2024, 9, 2, 14, 30)
    ) == "2024-09-02T14:30:00"


def test_un_type_inconnu_degrade_au_lieu_de_faire_tomber_la_reponse():
    """Repli délibéré : une cellule mal formatée coûte moins qu'une démonstration plantée."""

    class Exotique:
        def __str__(self) -> str:
            return "valeur exotique"

    assert serialisation.cellule(Exotique()) == "valeur exotique"


# --- 2. Ce que la frontière expose ----------------------------------------------------


def test_la_reponse_porte_les_lignes_brutes(client):
    """Sans elles, ni tableau ni graphique — c'est la raison d'être de cette API."""
    r = client.post("/api/question", json={"question": "Quelles dépenses ?"})

    assert r.status_code == 200
    charge = r.json()
    assert charge["texte"].startswith("Les dépenses")
    assert len(charge["requetes"]) == 1

    requete = charge["requetes"][0]
    assert requete["colonnes"] == ["step_date", "cost"]
    assert requete["lignes"] == [["2024-09-02", 1000.5], ["2024-09-09", 250.25]]
    assert requete["erreur"] is None


def test_le_motif_d_arret_voyage_avec_sa_lecture(client):
    """`arret_normal` est calculé côté serveur, jamais réinterprété par l'interface.

    Le rejouer côté navigateur le ferait diverger un jour, et un abandon finirait par
    s'afficher comme une réponse aboutie.
    """
    charge = client.post("/api/question", json={"question": "Quoi ?"}).json()

    assert charge["arret"] == "reponse_donnee"
    assert charge["arret_normal"] is True


def test_l_historique_est_transmis_au_noyau(client, monkeypatch):
    """L'API est sans état : c'est l'interface qui détient la conversation."""
    vus = {}
    vraie = boucle.ask
    monkeypatch.setattr(
        api.boucle, "ask",
        lambda q, h=(), **kw: vus.update(historique=list(h)) or vraie(q, h, **kw),
    )

    client.post("/api/question", json={
        "question": "Et en septembre ?",
        "historique": [{"question": "Quelles dépenses ?", "reponse": "1 250 €."}],
    })

    assert [e.question for e in vus["historique"]] == ["Quelles dépenses ?"]


def test_une_question_vide_est_refusee_par_la_frontiere(client):
    """Bornée ici, et non dans la boucle : c'est une règle de transport, pas de métier."""
    assert client.post("/api/question", json={"question": ""}).status_code == 422


def test_la_specification_de_graphique_traverse_la_frontiere(client):
    """L'interface ne décide de rien : elle reçoit un type, un axe, des séries.

    C'est ce qui permet de changer de bibliothèque de rendu sans toucher à une règle de
    lisibilité, et de tester ces règles sur des fonctions pures plutôt qu'au travers d'un
    navigateur.
    """
    charge = client.post("/api/question", json={"question": "Quelles dépenses ?"}).json()
    g = charge["graphique"]

    assert g is not None
    assert g["type"] == "courbe"
    assert g["x"] == "step_date"
    # Les dates de l'abscisse sont sérialisées comme partout ailleurs : `src/charts`
    # travaille sur les types du noyau et ignore qu'une frontière HTTP existe.
    assert g["etiquettes"] == ["2024-09-02", "2024-09-09"]
    assert g["series"] == [
        {"colonne": "cost", "valeurs": [1000.5, 250.25], "axe_secondaire": False}
    ]


def test_chaque_requete_reussie_porte_sa_specification_de_graphique(client):
    """Le « tracer à la demande » des blocs de requête : mêmes règles, calcul pur.

    Le tracé automatique reste réservé à la dernière requête — celle de la conclusion —
    mais l'interface doit pouvoir proposer les autres sans redéployer de logique de
    lisibilité côté navigateur.
    """
    charge = client.post("/api/question", json={"question": "Quelles dépenses ?"}).json()

    assert charge["requetes"][0]["graphique"] is not None
    assert charge["requetes"][0]["graphique"]["type"] == "courbe"
    assert "variantes" in charge["requetes"][0]["graphique"]


def test_l_absence_de_graphique_est_explicite_et_non_une_omission(client, monkeypatch):
    """`null` plutôt qu'un champ manquant : le refus est un résultat, pas un oubli."""
    def agent_sans_trace():
        return boucle.Agent(
            modele=ModeleScripte([
                appel_sql("SELECT SUM(cost) FROM media"),
                texte("Le total est de 1 250,75 €."),
            ]),
            systeme=SystemMessage(content="prompt d'essai"),
            empreinte_prompt="essai00000ab",
        )

    monkeypatch.setattr(api.boucle, "agent_par_defaut", agent_sans_trace)
    charge = client.post("/api/question", json={"question": "Total ?"}).json()

    assert "graphique" in charge
    assert charge["graphique"] is None


# --- 3. Le flux -----------------------------------------------------------------------


def _evenements(reponse) -> list[dict]:
    return [json.loads(l) for l in reponse.text.splitlines() if l.strip()]


def test_le_flux_rapporte_les_etapes_avant_la_reponse(client):
    """Sinon il ne sert à rien : tout arriverait au même instant, après l'attente."""
    r = client.post("/api/question/flux", json={"question": "Quelles dépenses ?"})

    assert r.status_code == 200
    types = [e["type"] for e in _evenements(r)]

    assert types == [
        boucle.ETAPE_REFLEXION,
        boucle.ETAPE_REQUETE,
        boucle.ETAPE_REFLEXION,
        boucle.ETAPE_REDACTION,
        "reponse",
    ]


def test_le_flux_rend_la_meme_reponse_que_la_voie_directe(client):
    """Les deux points d'entrée appellent le même noyau ; aucun ne doit dériver."""
    direct = client.post("/api/question", json={"question": "Quelles dépenses ?"}).json()
    flux = _evenements(
        client.post("/api/question/flux", json={"question": "Quelles dépenses ?"})
    )[-1]["reponse"]

    assert flux == direct


def test_une_panne_en_cours_de_flux_devient_un_evenement(client, monkeypatch):
    """Le statut HTTP est déjà parti : une 500 n'est plus possible.

    L'erreur doit donc voyager dans le flux, et l'interface doit savoir l'afficher. Sans
    ce test, le cas ne se découvrirait qu'en démonstration.
    """
    def tombe(*a, **kw):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(api.boucle, "ask", tombe)

    r = client.post("/api/question/flux", json={"question": "Quelles dépenses ?"})
    evenements = _evenements(r)

    assert r.status_code == 200
    assert evenements[-1]["type"] == "erreur"
    assert "panne simulée" not in evenements[-1]["message"]


def test_un_echec_d_assemblage_termine_le_flux_au_lieu_de_le_laisser_pendre(
    client, monkeypatch
):
    """L'assemblage de l'agent échoue *avant* la boucle — base absente au premier appel.

    Vu en relecture : l'assemblage vivait hors du `try/finally` du fil de travail, donc
    l'exception emportait le fil sans jamais poser le sentinelle dans la file — et la
    requête HTTP pendait pour toujours au lieu de rendre une erreur. La requête est jouée
    dans un fil borné : sans le correctif, ce test échoue par dépassement au lieu de
    bloquer toute la suite.
    """
    import threading

    def leve():
        raise FileNotFoundError("Base absente : la construire d'abord")

    monkeypatch.setattr(api.boucle, "agent_par_defaut", leve)

    resultat = {}

    def requete():
        resultat["r"] = client.post(
            "/api/question/flux", json={"question": "Quelles dépenses ?"}
        )

    fil = threading.Thread(target=requete, daemon=True)
    fil.start()
    fil.join(timeout=5.0)

    assert not fil.is_alive(), "le flux n'a pas rendu la main : sentinelle jamais posé"
    evenements = _evenements(resultat["r"])
    assert evenements[-1]["type"] == "erreur"
    # La même cause rend un 503 explicite sur la voie directe : le flux doit dire
    # la même chose, pas un message générique.
    assert "Base absente" in evenements[-1]["message"]


def test_une_panne_ne_fuit_pas_la_cause_au_client(client, monkeypatch):
    """Une trace d'exécution recopiée dans une réponse expose des chemins de fichiers."""
    def tombe(*a, **kw):
        raise RuntimeError("/home/secret/chemin/interne.py a échoué")

    monkeypatch.setattr(api.boucle, "ask", tombe)

    r = client.post("/api/question", json={"question": "Quelles dépenses ?"})

    assert r.status_code == 500
    assert "secret" not in r.text


# --- 4. La santé ----------------------------------------------------------------------


def test_la_sante_dit_ce_qui_manque_avant_la_premiere_question(client):
    charge = client.get("/api/sante").json()

    assert charge["base_presente"] is True
    assert charge["tables"] == ["media"]
    assert charge["modele"] == boucle.MODELE

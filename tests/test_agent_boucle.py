"""Tests de la boucle agent — sans aucun appel API.

C'est la contrainte qui a décidé de l'architecture : le client de modèle est injecté, donc
un faux modèle rejouant des réponses écrites à la main suffit à couvrir toute la boucle.
Sans ça, ces tests coûteraient de l'argent et ne tourneraient pas à chaque sauvegarde.

Ce qu'ils couvrent, dans cet ordre d'importance :

1. **Les bornes** — ce sont elles qui font la différence entre une intention de prompt et
   une garantie. Chacune est vue en train de mordre.
2. **La reprise sur erreur** — les messages d'E2 sont rédigés pour être lus par le modèle ;
   encore faut-il qu'ils lui parviennent.
3. **Les arrêts anormaux** — refus, panne d'API : aucun ne doit produire de trace
   d'exécution ni de réponse qui ressemble à un succès.
"""

from __future__ import annotations

import pathlib

import anthropic
import duckdb
import pytest
from langchain_core.messages import AIMessage

from src.agent import boucle, outil
from src.agent.boucle import Agent, ask
from src.agent.reponse import Arret, Echange
from src.db import connexion


# --- Le faux modèle -------------------------------------------------------------------


class ModeleScripte:
    """Rejoue des réponses préparées, et retient ce qu'on lui a envoyé.

    Retenir les messages est la moitié de l'intérêt : c'est ainsi qu'on vérifie que le
    résultat d'outil — et surtout un message d'erreur — est bien reparti vers le modèle.
    """

    def __init__(self, reponses: list[AIMessage]):
        self.reponses = list(reponses)
        self.appels: list[list] = []

    def invoke(self, messages):
        self.appels.append(list(messages))
        if not self.reponses:
            raise AssertionError("le modèle a été appelé plus de fois que prévu")
        return self.reponses.pop(0)


class ModeleEnPanne:
    def __init__(self, exception: Exception):
        self.exception = exception

    def invoke(self, messages):
        raise self.exception


def texte(contenu: str, usage: dict | None = None) -> AIMessage:
    return AIMessage(
        content=contenu,
        response_metadata={"model": "claude-sonnet-5", "stop_reason": "end_turn"},
        usage_metadata=usage,
    )


def appel_sql(query: str, identifiant: str = "t1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": outil.NOM, "args": {"query": query}, "id": identifiant}],
        response_metadata={"model": "claude-sonnet-5", "stop_reason": "tool_use"},
    )


def coupe(contenu: str) -> AIMessage:
    """Une réponse arrêtée par le plafond de sortie, coupée en pleine phrase."""
    return AIMessage(
        content=contenu,
        response_metadata={"model": "claude-sonnet-5", "stop_reason": "max_tokens"},
    )


# --- La base d'essai ------------------------------------------------------------------


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> pathlib.Path:
    chemin = tmp_path_factory.mktemp("db") / "test.duckdb"
    con = duckdb.connect(str(chemin))
    con.execute(
        "CREATE TABLE media AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'tv', 1000.0), (DATE '2024-09-09', 'sea', 250.0)"
        ") t(step_date, channel, cost)"
    )
    con.close()
    return chemin


@pytest.fixture
def con(base):
    c = connexion.ouvrir(base)
    yield c
    c.close()


def agent_avec(modele, con, **reglages) -> Agent:
    from langchain_core.messages import SystemMessage

    return Agent(
        modele=modele,
        systeme=SystemMessage(content="prompt d'essai"),
        empreinte_prompt="essai00000ab",
        con=con,
        **reglages,
    )


# --- 1. Les bornes --------------------------------------------------------------------


def test_plafond_d_iterations_mord(con):
    """Un modèle qui redemande une requête sans jamais conclure doit être arrêté.

    Sans ce plafond, rien dans le code n'empêche une boucle infinie : le prompt ne peut
    que le suggérer, et une suggestion n'est pas une garantie.
    """
    modele = ModeleScripte([appel_sql("SELECT 1", f"t{i}") for i in range(10)])

    reponse = ask("et ensuite ?", agent=agent_avec(modele, con, max_iterations=3))

    assert reponse.arret is Arret.PLAFOND_ITERATIONS
    assert not reponse.arret.est_normal
    assert len(modele.appels) == 3, "la boucle a dépassé son plafond"
    assert len(reponse.requetes) == 3, "le travail déjà fait doit rester dans la réponse"


def test_echecs_sql_repetes_arretent_la_boucle(con):
    """Distinct du plafond d'itérations : le modèle tourne en rond sur une erreur.

    L'arrêter plus tôt évite de payer les itérations restantes pour rien.
    """
    modele = ModeleScripte(
        [appel_sql("SELECT inconnue FROM media", f"t{i}") for i in range(6)]
    )

    agent = agent_avec(modele, con, max_iterations=6, max_echecs_sql=2)
    reponse = ask("combien ?", agent=agent)

    assert reponse.arret is Arret.TROP_D_ECHECS_SQL
    assert len(reponse.requetes) == 2
    assert len(modele.appels) == 2, "la boucle a continué après le plafond d'échecs"


def test_un_succes_remet_le_compteur_d_echecs_a_zero(con):
    """Le plafond porte sur des échecs *consécutifs*.

    Un modèle qui se trompe, se reprend, puis se retrompe travaille normalement — le
    compter comme trois échecs de suite l'interromprait en plein travail.
    """
    modele = ModeleScripte(
        [
            appel_sql("SELECT inconnue FROM media", "t1"),
            appel_sql("SELECT COUNT(*) FROM media", "t2"),
            appel_sql("SELECT encore_inconnue FROM media", "t3"),
            texte("Voici le compte."),
        ]
    )

    agent = agent_avec(modele, con, max_iterations=6, max_echecs_sql=2)
    reponse = ask("combien ?", agent=agent)

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert [r.a_reussi for r in reponse.requetes] == [False, True, False]


def test_l_historique_est_borne(con):
    """Sans borne, chaque question porterait le poids de toutes les précédentes."""
    modele = ModeleScripte([texte("Bien reçu.")])
    historique = [Echange(f"question {i}", f"réponse {i}") for i in range(12)]

    ask("et maintenant ?", historique, agent=agent_avec(modele, con))

    envoyes = modele.appels[0]
    # 1 système + 2 messages par échange conservé + la question courante
    assert len(envoyes) == 1 + 2 * boucle.HISTORIQUE_MAX + 1
    assert "question 11" in str(envoyes), "les échanges conservés sont les derniers"
    assert "question 0" not in str(envoyes)


# --- 2. La reprise sur erreur ---------------------------------------------------------


def test_le_message_d_erreur_repart_vers_le_modele(con):
    """Tout le soin mis dans les messages d'E2 ne sert que si le modèle les reçoit.

    On vérifie sur le contenu : le message doit nommer les colonnes réelles, ce qui est
    précisément ce qui permet au modèle de se reprendre au tour suivant.
    """
    modele = ModeleScripte(
        [appel_sql("SELECT spend FROM media"), texte("Le total est de 1 250 €.")]
    )

    reponse = ask("quelles dépenses ?", agent=agent_avec(modele, con))

    recu = str(modele.appels[1])
    assert "cost" in recu and "channel" in recu, "les colonnes réelles sont transmises"
    assert reponse.arret is Arret.REPONSE_DONNEE
    assert reponse.requetes[0].erreur is not None


def test_le_resultat_de_la_requete_repart_vers_le_modele(con):
    modele = ModeleScripte(
        [appel_sql("SELECT channel, cost FROM media"), texte("Deux canaux.")]
    )

    ask("quels canaux ?", agent=agent_avec(modele, con))

    recu = str(modele.appels[1])
    assert "tv" in recu and "1000.0" in recu


def test_une_requete_refusee_ne_leve_pas(con):
    """Un refus d'E2 est une erreur récupérable, pas une panne de la boucle."""
    modele = ModeleScripte([appel_sql("DROP TABLE media"), texte("Je ne peux pas.")])

    reponse = ask("supprime tout", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert "SELECT" in reponse.requetes[0].erreur


def test_un_outil_inconnu_est_signale_au_modele(con):
    modele = ModeleScripte(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "efface_tout", "args": {}, "id": "t1"}],
                response_metadata={"stop_reason": "tool_use"},
            ),
            texte("Compris."),
        ]
    )

    ask("essaie", agent=agent_avec(modele, con))

    assert outil.NOM in str(modele.appels[1])


# --- 3. Les arrêts anormaux -----------------------------------------------------------


def test_un_refus_ne_fait_pas_lire_un_contenu_vide(con):
    """Sur un refus, le contenu est vide : le lire sans vérifier produit un plantage."""
    modele = ModeleScripte(
        [AIMessage(content=[], response_metadata={"stop_reason": "refusal"})]
    )

    reponse = ask("question hors sujet", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REFUS_MODELE
    assert reponse.texte


def test_un_refus_ne_recycle_pas_le_texte_du_tour_precedent(con):
    """Le contenu d'un refus est vide — le repli ne doit pas aller chercher ailleurs.

    Ce que ce test attrape exactement : un texte rendu qui serait
    `dernier_texte or TEXTE_REFUS`. La boucle présenterait alors la réponse d'un tour
    antérieur comme étant celle-ci, et le refus passerait inaperçu avec un texte
    plausible à l'appui.

    L'ordre du contrôle dans le code — avant la lecture du contenu — n'est en revanche
    pas testable, et il ne prétend pas l'être : `.text` rend `""` sur un contenu vide au
    lieu de lever. C'est une précaution, pas une garantie, et le commentaire de
    `boucle.py` le dit désormais ainsi.
    """
    modele = ModeleScripte(
        [
            appel_sql("SELECT SUM(cost) FROM media"),
            texte("Le total est de 1 250 €."),  # texte bien réel, d'un tour précédent
        ]
    )
    agent = agent_avec(modele, con)
    ask("quel total ?", agent=agent)

    modele.reponses = [AIMessage(content=[], response_metadata={"stop_reason": "refusal"})]
    reponse = ask("question hors sujet", agent=agent)

    assert reponse.arret is Arret.REFUS_MODELE
    assert reponse.texte == boucle.TEXTE_REFUS
    assert "1 250" not in reponse.texte


def test_une_reponse_coupee_au_plafond_de_sortie_n_est_pas_un_succes(con):
    """Le plafond de sortie est partagé avec le raisonnement : la coupe est possible.

    Sans ce contrôle, la réponse ressortait en `REPONSE_DONNEE` — donc comptée comme un
    succès par le harnais, avec un texte qui *paraît* complet. C'est l'erreur de mesure
    la plus coûteuse : elle flatte le score dans le sens qu'on ne va pas vérifier.
    """
    modele = ModeleScripte([coupe("Le budget média sur la période s'élève à 1 2")])

    reponse = ask("quel budget ?", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_TRONQUEE
    assert not reponse.arret.est_normal, "une réponse coupée n'est pas un arrêt normal"
    assert "1 2" in reponse.texte, "le travail partiel reste rendu"
    assert boucle.TEXTE_TRONQUE in reponse.texte, "et il est annoncé comme incomplet"


def test_un_appel_d_outil_coupe_n_est_pas_execute(con):
    """La coupe peut tomber au milieu d'un appel d'outil, dont les arguments sont alors
    incomplets. Exécuter ce que l'on en devine ferait travailler la boucle sur une
    requête que le modèle n'a pas fini d'écrire."""
    modele = ModeleScripte(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": outil.NOM, "args": {"query": "SELECT SUM(co"},
                             "id": "t1"}],
                response_metadata={"stop_reason": "max_tokens"},
            )
        ]
    )

    reponse = ask("quel total ?", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_TRONQUEE
    assert reponse.requetes == [], "aucune requête tronquée ne doit partir vers la base"


def test_le_travail_partiel_survit_au_plafond_d_iterations(con):
    """Le motif d'arrêt dit que ça n'a pas abouti ; le texte n'a pas à disparaître."""
    bavard = AIMessage(
        content="Premiers éléments : le canal tv domine.",
        tool_calls=[{"name": outil.NOM, "args": {"query": "SELECT 1"}, "id": "t1"}],
        response_metadata={"stop_reason": "tool_use"},
    )
    modele = ModeleScripte([bavard, bavard])

    reponse = ask("et ensuite ?", agent=agent_avec(modele, con, max_iterations=2))

    assert reponse.arret is Arret.PLAFOND_ITERATIONS
    assert "le canal tv domine" in reponse.texte
    assert boucle.TEXTE_PLAFOND in reponse.texte


def test_une_panne_d_api_devient_un_etat_de_la_reponse(con):
    """Aucune trace d'exécution ne sort de `ask()` : c'est l'interface qui décide de ce
    qu'un utilisateur voit, et elle ne peut pas décider sur une exception."""
    panne = anthropic.APIConnectionError(request=None)

    reponse = ask("combien ?", agent=agent_avec(ModeleEnPanne(panne), con))

    assert reponse.arret is Arret.ERREUR_API
    assert reponse.texte


# --- 4. Le chemin nominal, et ce qu'il rapporte ---------------------------------------


def test_chemin_nominal(con):
    modele = ModeleScripte(
        [appel_sql("SELECT SUM(cost) FROM media"), texte("Le total est de 1 250 €.")]
    )

    reponse = ask("quel total ?", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert reponse.arret.est_normal
    assert reponse.texte == "Le total est de 1 250 €."
    assert reponse.a_interroge_la_base
    assert reponse.requetes[0].lignes == [(1250.0,)]


def test_une_reponse_sans_requete_est_un_succes(con):
    """Le prompt invite le modèle à demander une précision quand la question est ambiguë.

    Une boucle qui exigerait au moins une requête casserait ce comportement voulu, et le
    harnais compterait un échec là où l'agent a bien travaillé.
    """
    modele = ModeleScripte([texte("De quelles ventes parlez-vous : MES, CDF ou DEM ?")])

    reponse = ask("combien de ventes ?", agent=agent_avec(modele, con))

    assert reponse.arret is Arret.REPONSE_DONNEE
    assert reponse.arret.est_normal
    assert not reponse.a_interroge_la_base


def test_la_reponse_porte_de_quoi_la_comparer(con):
    """Sans l'identifiant exact du modèle et l'empreinte du prompt, deux mesures prises
    à deux dates ne sont pas comparables — et le harnais ne le saurait pas."""
    modele = ModeleScripte([texte("Réponse.")])

    reponse = ask("question ?", agent=agent_avec(modele, con))

    assert reponse.modele == "claude-sonnet-5"
    assert reponse.empreinte_prompt == "essai00000ab"


def test_les_tokens_se_cumulent_sur_tous_les_appels(con):
    """Le coût d'une question est celui de la boucle entière, pas du dernier appel."""
    modele = ModeleScripte(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": outil.NOM, "args": {"query": "SELECT 1"}, "id": "t1"}
                ],
                response_metadata={"stop_reason": "tool_use"},
                usage_metadata={
                    "input_tokens": 3000,
                    "output_tokens": 50,
                    "total_tokens": 3050,
                    "input_token_details": {
                        "cache_read": 0,
                        "ephemeral_5m_input_tokens": 2700,
                    },
                },
            ),
            texte(
                "Fini.",
                usage={
                    "input_tokens": 3100,
                    "output_tokens": 80,
                    "total_tokens": 3180,
                    "input_token_details": {"cache_read": 2700, "cache_creation": 0},
                },
            ),
        ]
    )

    reponse = ask("combien ?", agent=agent_avec(modele, con))

    assert reponse.usage.entree == 6100
    assert reponse.usage.sortie == 130
    assert reponse.usage.cache_lu == 2700
    # Le connecteur remet `cache_creation` à zéro dès qu'il publie le détail par durée de
    # vie : lire la seule clé générique ferait conclure qu'aucune écriture n'a eu lieu.
    assert reponse.usage.cache_ecrit == 2700


def test_les_deux_durees_de_cache_s_additionnent(con):
    """Deux marqueurs de durées différentes écrivent deux fois — pas une.

    Un enchaînement de conditions entre les deux clés donne la première non nulle et
    ignore l'autre : la lecture n'est alors juste que tant qu'une seule durée est
    utilisée, c'est-à-dire juste par coïncidence.
    """
    modele = ModeleScripte(
        [
            texte(
                "Fini.",
                usage={
                    "input_tokens": 5000,
                    "output_tokens": 10,
                    "total_tokens": 5010,
                    "input_token_details": {
                        "cache_creation": 0,
                        "ephemeral_5m_input_tokens": 1200,
                        "ephemeral_1h_input_tokens": 3400,
                    },
                },
            )
        ]
    )

    reponse = ask("combien ?", agent=agent_avec(modele, con))

    assert reponse.usage.cache_ecrit == 4600


def test_l_agent_par_defaut_n_est_construit_qu_une_fois(monkeypatch):
    """Deux premières questions simultanées ne doivent pas produire deux agents.

    E8 servira l'interface depuis un pool de fils. Sans verrou, chacun construirait son
    agent — donc deux générations de prompt et deux connexions, dont une abandonnée sans
    être refermée. La lenteur simulée est ce qui rend la course observable ; sans elle,
    le test passerait même sans verrou.
    """
    import threading
    import time

    construits: list[int] = []

    def construire_lentement(*_, **__):
        time.sleep(0.05)  # le temps qu'un autre fil entre dans la fenêtre
        construits.append(1)
        return "agent factice"

    monkeypatch.setattr(boucle, "construire", construire_lentement)
    monkeypatch.setattr(boucle, "_defaut", None)

    fils = [threading.Thread(target=boucle.agent_par_defaut) for _ in range(4)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()

    assert len(construits) == 1, f"{len(construits)} agents construits au lieu d'un"


# --- 4. La trace : observer sans influencer -------------------------------------------


class TraceEspion:
    """Retient les étapes, dans l'ordre où elles sont poussées."""

    def __init__(self):
        self.etapes: list[tuple[str, dict]] = []

    def __call__(self, etape: str, detail: dict) -> None:
        self.etapes.append((etape, detail))


def test_la_trace_rapporte_les_etapes_au_fil_de_l_eau(con):
    """Ce que l'interface a besoin de montrer pendant les dix à trente secondes d'attente.

    L'ordre compte autant que le contenu : une réflexion, puis la requête *avec son
    résultat*, puis une seconde réflexion, puis la rédaction. Une trace qui annoncerait la
    requête avant de l'exécuter n'aurait ni son nombre de lignes ni sa durée à montrer.
    """
    espion = TraceEspion()
    modele = ModeleScripte([
        appel_sql("SELECT SUM(cost) FROM media"),
        texte("Le total est de 1 250 €."),
    ])

    ask("Quel est le total ?", agent=agent_avec(modele, con), trace=espion)

    assert [e for e, _ in espion.etapes] == [
        boucle.ETAPE_REFLEXION,
        boucle.ETAPE_REQUETE,
        boucle.ETAPE_REFLEXION,
        boucle.ETAPE_REDACTION,
    ]
    detail = espion.etapes[1][1]
    assert detail["sql"] == "SELECT SUM(cost) FROM media"
    assert detail["lignes"] == 1
    assert detail["erreur"] is None


def test_la_trace_rapporte_aussi_les_tatonnements(con):
    """Un échec de requête est ce qu'une démo a de plus intéressant à montrer.

    Il prouve que la boucle se reprend. Le masquer donnerait d'une exécution une image
    plus lisse que la réalité — et c'est précisément ce que le projet refuse de faire de
    ses mesures.
    """
    espion = TraceEspion()
    modele = ModeleScripte([
        appel_sql("SELECT colonne_absente FROM media"),
        appel_sql("SELECT SUM(cost) FROM media", "t2"),
        texte("Le total est de 1 250 €."),
    ])

    ask("Quel est le total ?", agent=agent_avec(modele, con), trace=espion)

    requetes = [d for e, d in espion.etapes if e == boucle.ETAPE_REQUETE]
    assert len(requetes) == 2
    assert requetes[0]["erreur"] is not None
    assert requetes[1]["erreur"] is None


def test_observer_la_boucle_ne_la_change_pas(con):
    """La propriété qui autorise à laisser ce paramètre à l'interface.

    Sans elle, `trace=` ouvrirait un second chemin d'exécution — et il faudrait alors
    rejouer toute la suite dans les deux modes. Deux exécutions identiques, l'une observée
    et l'autre non, doivent rendre exactement la même réponse.
    """
    def executer(trace):
        modele = ModeleScripte([
            appel_sql("SELECT SUM(cost) FROM media"),
            texte("Le total est de 1 250 €."),
        ])
        return ask("Quel est le total ?", agent=agent_avec(modele, con), trace=trace)

    sans = executer(None)
    avec = executer(TraceEspion())

    assert sans.texte == avec.texte
    assert sans.arret is avec.arret
    assert [r.sql for r in sans.requetes] == [r.sql for r in avec.requetes]
    assert [r.lignes for r in sans.requetes] == [r.lignes for r in avec.requetes]


def test_une_trace_qui_leve_ne_passe_pas_pour_une_panne_d_agent(con):
    """Un traceur fautif est un défaut de l'appelant, pas une erreur de l'agent.

    L'étouffer produirait une interface muette qu'on croirait branchée — l'exception
    remonte donc, et le développeur la voit.
    """
    def trace_fautive(etape, detail):
        raise ValueError("traceur mal branché")

    modele = ModeleScripte([texte("Réponse.")])

    with pytest.raises(ValueError, match="traceur mal branché"):
        ask("Question ?", agent=agent_avec(modele, con), trace=trace_fautive)


# --- 5. Le graphique ------------------------------------------------------------------


def test_le_graphique_vient_de_la_derniere_requete_reussie(con):
    """La dernière, et non la plus grosse : c'est celle qui porte la conclusion.

    Les précédentes sont des explorations — vérifier qu'une valeur existe, lister des
    canaux. Tracer l'une d'elles illustrerait un raisonnement intermédiaire au lieu de la
    réponse, avec l'assurance trompeuse que donne un graphique.
    """
    modele = ModeleScripte([
        appel_sql("SELECT DISTINCT channel FROM media"),
        appel_sql("SELECT step_date, cost FROM media ORDER BY step_date", "t2"),
        texte("Voici l'évolution."),
    ])

    reponse = ask("Évolution ?", agent=agent_avec(modele, con))

    assert reponse.graphique is not None
    assert reponse.graphique.x == "step_date"
    assert [s.colonne for s in reponse.graphique.series] == ["cost"]


def test_aucun_graphique_quand_le_resultat_ne_s_y_prete_pas(con):
    """Le refus est le cas fréquent, et il ne doit pas ressembler à une panne."""
    modele = ModeleScripte([
        appel_sql("SELECT SUM(cost) FROM media"),
        texte("Le total est de 1 250 €."),
    ])

    reponse = ask("Total ?", agent=agent_avec(modele, con))

    assert reponse.graphique is None
    assert reponse.arret is Arret.REPONSE_DONNEE


def test_une_requete_en_echec_ne_sert_pas_de_source_au_graphique(con):
    """Un tâtonnement n'a ni colonnes ni lignes : le tracer n'aurait aucun sens."""
    modele = ModeleScripte([
        appel_sql("SELECT step_date, cost FROM media ORDER BY step_date"),
        appel_sql("SELECT colonne_absente FROM media", "t2"),
        texte("Voici."),
    ])

    reponse = ask("Évolution ?", agent=agent_avec(modele, con))

    # La dernière *réussie*, donc la première des deux.
    assert reponse.graphique is not None
    assert reponse.graphique.x == "step_date"


def test_aucune_requete_ne_donne_aucun_graphique(con):
    """Un refus pédagogique reste un refus : rien à tracer, et ce n'est pas un défaut."""
    modele = ModeleScripte([texte("Ces données ne permettent pas de calculer un ROI.")])

    reponse = ask("Quel canal a le meilleur ROI ?", agent=agent_avec(modele, con))

    assert reponse.graphique is None


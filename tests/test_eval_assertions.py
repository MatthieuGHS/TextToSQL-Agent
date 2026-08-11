"""Tests des assertions du harnais d'évaluation.

Les sorties d'agent sont **fabriquées à la main** : une réponse juste, une réponse
fausse. C'est délibéré et c'est l'ordre imposé par le plan — les assertions s'écrivent
avant que l'agent existe, sinon elles se calquent sur ce qu'il produit et n'attrapent
plus rien.

Chaque assertion est vérifiée dans les deux sens. Un contrôle qu'on n'a jamais vu
échouer n'est pas un contrôle.
"""

from __future__ import annotations

import time

import duckdb
import pytest

from tests.eval import assertions as a


@pytest.fixture
def con():
    """Une base minimale, aux ordres de grandeur de la vraie.

    `media` porte le coût de l'annonceur, `contexte` celui des concurrents : c'est
    l'homonymie que plusieurs assertions servent à surveiller.
    """
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE media AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'te', 'tv',  1000.0), "
        "(DATE '2024-09-09', 'te', 'tv',   500.0), "
        "(DATE '2024-09-09', 'te', 'sea', 250.0)) "
        "t(step_date, brand_name, channel, cost)"
    )
    con.execute(
        "CREATE TABLE contexte AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'cost', 'edf', 9000.0)) "
        "t(step_date, metric, brand_name, value)"
    )
    con.execute(
        "CREATE TABLE kpi_compteurs AS SELECT * FROM (VALUES "
        "(DATE '2024-09-02', 'elec', 42)) t(step_date, energy, mes)"
    )
    return con


# --- Type A : valeur ------------------------------------------------------------------

REF_TOTAL = "SELECT SUM(cost) FROM media"  # 1750.0


def test_valeur_attendue_accepte_le_bon_calcul(con):
    r = a.Resultat(reponse="1 750 €", sql=["SELECT SUM(cost) FROM media"])

    assert a.ValeurAttendue(REF_TOTAL).verifier(r, con).ok


def test_valeur_attendue_refuse_un_perimetre_faux(con):
    """Le piège d'homonymie : sommer les deux tables donne un total plausible et faux."""
    r = a.Resultat(
        reponse="10 750 €",
        sql=[
            "SELECT (SELECT SUM(cost) FROM media) + (SELECT SUM(value) "
            "FROM contexte WHERE metric = 'cost')"
        ],
    )

    verdict = a.ValeurAttendue(REF_TOTAL).verifier(r, con)
    assert not verdict.ok
    assert "1,750.00" in verdict.detail


def test_valeur_attendue_refuse_une_reponse_sans_sql(con):
    """Une réponse affirmée sans requête n'est pas vérifiable, donc pas acceptable."""
    r = a.Resultat(reponse="Le total est d'environ 1 750 €.", sql=[])

    assert not a.ValeurAttendue(REF_TOTAL).verifier(r, con).ok


def test_valeur_attendue_tolere_un_ecart_de_calcul_minime(con):
    r = a.Resultat(reponse="", sql=["SELECT SUM(cost) * 1.0001 FROM media"])

    assert a.ValeurAttendue(REF_TOTAL, tolerance=0.001).verifier(r, con).ok


# --- Type B : forme du SQL ------------------------------------------------------------


def test_ne_touche_pas_detecte_la_table_interdite(con):
    r = a.Resultat(reponse="", sql=["SELECT SUM(value) FROM contexte"])

    assert not a.SqlNeTouchePas("contexte").verifier(r, con).ok
    assert a.SqlNeTouchePas("contexte").verifier(
        a.Resultat(reponse="", sql=["SELECT SUM(cost) FROM media"]), con
    ).ok


def test_utilise_table_detecte_l_absence(con):
    r = a.Resultat(reponse="", sql=["SELECT SUM(cost) FROM media"])

    assert a.SqlUtiliseTable("media").verifier(r, con).ok
    assert not a.SqlUtiliseTable("contexte").verifier(r, con).ok


@pytest.mark.parametrize(
    "sql, attendu",
    [
        ("SELECT * FROM media WHERE step_date >= CURRENT_DATE - 30", False),
        ("SELECT * FROM media WHERE step_date >= now() - INTERVAL 1 MONTH", False),
        ("SELECT * FROM media WHERE step_date >= DATE '2024-09-09'"
         " - INTERVAL 1 WEEK", True),
    ],
)
def test_date_courante(con, sql, attendu):
    """Une période relative doit partir de la fin des données, pas d'aujourd'hui."""
    r = a.Resultat(reponse="", sql=[sql])

    assert a.SqlSansDateCourante().verifier(r, con).ok is attendu


def test_jointure_sur_semaine(con):
    croise_sans_cle = "SELECT * FROM media, kpi_compteurs"
    croise_avec_cle = (
        "SELECT * FROM media m JOIN kpi_compteurs k ON m.step_date = k.step_date"
    )
    une_seule_table = "SELECT * FROM media"

    verifier = a.SqlJointureSurSemaine().verifier
    assert not verifier(a.Resultat(reponse="", sql=[croise_sans_cle]), con).ok
    assert verifier(a.Resultat(reponse="", sql=[croise_avec_cle]), con).ok
    # Une requête mono-table n'a rien à joindre : le contrôle ne doit pas se déclencher.
    assert verifier(a.Resultat(reponse="", sql=[une_seule_table]), con).ok


def test_sql_executable(con):
    verifier = a.SqlExecutable().verifier
    assert verifier(a.Resultat(reponse="", sql=["SELECT 1"]), con).ok
    assert not verifier(a.Resultat(reponse="", sql=["SELECT * FROM inexistante"]), con).ok


def test_ne_rien_requeter_n_est_pas_un_sql_inexecutable(con):
    """Les deux propriétés étaient confondues, et ça se payait dans les deux sens.

    Une bonne part des questions d'un jeu d'évaluation sont des pièges — dimension
    inexistante, canal mal nommé, attribution impossible — et la bonne réponse est un
    refus expliqué, sans requête. Le compter comme un SQL cassé revient à noter l'agent
    contre sa propre conception : c'est ce qui est arrivé à quatre des cinq échecs de la
    grille client sur la première campagne.

    L'exigence inverse existe toujours, mais elle se pose **cas par cas**.
    """
    muette = a.Resultat(reponse="Cette dimension n'existe pas dans les données.", sql=[])

    assert a.SqlExecutable().verifier(muette, con).ok
    assert not a.SqlProduit().verifier(muette, con).ok, (
        "contre-épreuve : là où la question exige une requête, l'absence reste un échec"
    )
    assert a.SqlProduit().verifier(
        a.Resultat(reponse="", sql=["SELECT 1"]), con
    ).ok


INTERMINABLE = "SELECT COUNT(*) FROM range(200000000) a, range(200000000) b"


@pytest.mark.parametrize(
    "assertion, motif",
    [
        (a.SqlExecutable(), "SqlTropLong"),
        (a.ValeurAttendue(REF_TOTAL), "aucune valeur"),
    ],
)
def test_une_requete_interminable_devient_un_verdict(con, monkeypatch, assertion, motif):
    """Le SQL évalué ici est écrit par un modèle : il peut balayer indéfiniment.

    Sans borne, le harnais lancé pour mesurer se bloquerait sur la question qui
    l'intéressait le plus.

    **La durée fait partie de l'assertion.** Le délai du harnais était d'abord lu comme
    valeur par défaut de `run_sql`, donc figé à la définition de la fonction : l'abaisser
    depuis le test ne changeait rien, et ces deux tests passaient au bout des 15 secondes
    réelles — verts, mais ne prouvant pas que le réglage mordait. Borner le temps écoulé
    est ce qui distingue les deux situations.
    """
    monkeypatch.setattr(a, "DELAI_SECONDES", 0.2)
    r = a.Resultat(reponse="", sql=[INTERMINABLE])

    debut = time.monotonic()
    verdict = assertion.verifier(r, con)
    ecoule = time.monotonic() - debut

    assert not verdict.ok
    assert motif in verdict.detail
    assert ecoule < 5.0, (
        f"{ecoule:.1f} s écoulées : le délai du harnais n'a pas été appliqué, "
        f"c'est celui de l'agent qui a fini par mordre."
    )


# --- État de la boucle ----------------------------------------------------------------


def test_l_arret_normal_accepte_une_reponse_aboutie(con):
    r = a.Resultat(reponse="Le total est de 1 750 €.", sql=[REF_TOTAL])

    assert a.ArretNormal().verifier(r, con).ok


def test_une_reponse_sans_requete_reste_un_arret_normal(con):
    """Le prompt invite le modèle à demander une précision quand la question est ambiguë.

    Compter ça comme un échec pénaliserait le comportement voulu. C'est `Arret.est_normal`
    qui tranche, et ce test garde la propriété du côté du harnais.
    """
    r = a.Resultat(reponse="De quelles ventes parlez-vous : MES, CDF ou DEM ?", sql=[])

    assert a.ArretNormal().verifier(r, con).ok


@pytest.mark.parametrize(
    "arret",
    ["plafond_iterations", "trop_d_echecs_sql", "refus_modele", "reponse_tronquee"],
)
def test_chaque_arret_anormal_echoue(con, arret):
    """Le cas qui compte : `reponse_tronquee`.

    Le texte peut être exact et paraître complet — seul le motif d'arrêt dit qu'il manque
    la fin. Sans ce contrôle, le harnais compterait un succès.
    """
    r = a.Resultat(reponse="Le total est de 1 7", sql=[REF_TOTAL], arret=arret)

    assert not a.ArretNormal().verifier(r, con).ok


def test_un_motif_d_arret_inconnu_echoue(con):
    """Une trace écrite par une version antérieure ne doit pas passer pour un succès."""
    r = a.Resultat(reponse="", sql=[REF_TOTAL], arret="motif_qui_n_existe_plus")

    verdict = a.ArretNormal().verifier(r, con)
    assert not verdict.ok
    assert "inconnu" in verdict.detail


# --- Type C : forme du texte ----------------------------------------------------------


def test_texte_contient_et_ne_contient_pas(con):
    r = a.Resultat(reponse="Corrélation n'est pas causalité ; prudence.")

    assert a.TexteContient(("causalité", "corrélation")).verifier(r, con).ok
    assert a.TexteContient(("causalité", "corrélation"), au_moins=2).verifier(r, con).ok
    assert not a.TexteContient(("ROI",)).verifier(r, con).ok
    assert a.TexteNeContientPas(("ROI", "retour sur investissement")).verifier(r, con).ok
    assert not a.TexteNeContientPas(("causalité",)).verifier(r, con).ok


def test_pas_de_graphique_sur_resultat_vide(con):
    vide = "SELECT SUM(cost) FROM media WHERE channel = 'tiktok'"
    plein = "SELECT SUM(cost) FROM media"
    verifier = a.PasDeGraphiqueSurResultatVide().verifier

    assert not verifier(a.Resultat(reponse="", sql=[vide], graphique=True), con).ok
    assert verifier(a.Resultat(reponse="", sql=[vide], graphique=False), con).ok
    assert verifier(a.Resultat(reponse="", sql=[plein], graphique=True), con).ok


# --- Traçabilité ----------------------------------------------------------------------


def test_tracabilite_accepte_un_chiffre_calcule(con):
    r = a.Resultat(reponse="Le total s'élève à 1 750 €.", sql=[REF_TOTAL])

    assert a.TracabiliteNumerique().verifier(r, con).ok


def test_tracabilite_accepte_un_arrondi_de_presentation(con):
    """« 1,75 millier » pour 1750 : même valeur, unité de présentation différente."""
    r = a.Resultat(reponse="Environ 1,75 millier d'euros.", sql=[REF_TOTAL])

    assert a.TracabiliteNumerique().verifier(r, con).ok


def test_tracabilite_refuse_un_chiffre_invente(con):
    """Le cas qui compte : un ordre de grandeur affirmé sans qu'aucun SQL
    ne l'ait produit."""
    r = a.Resultat(
        reponse="Le total s'élève à 1 750 €, soit un ROI de 4 320 %.", sql=[REF_TOTAL]
    )

    verdict = a.TracabiliteNumerique().verifier(r, con)
    assert not verdict.ok
    assert "4 320" in verdict.detail


def test_tracabilite_ignore_les_petits_nombres(con):
    """« 3 canaux », « 2024 » : des nombres de phrase, pas des chiffres extraits."""
    r = a.Resultat(reponse="Les 3 canaux étudiés totalisent 1 750 €.", sql=[REF_TOTAL])

    assert a.TracabiliteNumerique().verifier(r, con).ok


# --- Ce que la première campagne réelle a corrigé -------------------------------------
#
# Trois défauts de l'instrument, tous découverts en confrontant les assertions à de
# vraies réponses. Aucun ne se voyait sur des sorties fabriquées à la main : c'est
# exactement ce à quoi sert la passe de peuplement.


@pytest.mark.parametrize(
    "reponse, attendu",
    [
        ("Un axe pour les GRP à gauche, un pour les clics à droite.", True),
        ("Ces trois chiffres ne doivent pas être additionnés.", True),
        ("Sur un périmètre plus étroit, la tendance s'inverse.", True),
        ("Le ROI de la TV est de 3.", False),
        ("Le roi des canaux, c'est la télévision.", False),
    ],
)
def test_le_vocabulaire_interdit_se_cherche_sur_des_mots_entiers(con, reponse, attendu):
    """« roi » est dans « droite », dans « trois » et dans « étroit ».

    En sous-chaîne, ce contrôle condamnait des réponses irréprochables — celles-là mêmes
    qui refusaient d'additionner des unités incomparables. Un faux positif ne fait pas
    que du bruit : il envoie le réglage corriger un défaut qui n'existe pas.

    Les deux derniers cas sont la contre-épreuve : le mot reste interdit quand c'en est
    un.
    """
    r = a.Resultat(reponse=reponse)

    assert a.TexteNeContientPas(("roi",)).verifier(r, con).ok is attendu


def test_une_date_rendue_par_une_requete_est_tracable(con):
    """Les cellules non numériques sortaient de la traçabilité, dates comprises.

    Une réponse qui date son périmètre — ce que le prompt lui demande — se voyait donc
    reprocher des chiffres qu'une requête venait de lui rendre.
    """
    r = a.Resultat(
        reponse="Le total de 1 750 € couvre la période du 2024-09-02 au 2024-09-09.",
        sql=["SELECT SUM(cost), MIN(step_date), MAX(step_date) FROM media"],
    )

    assert a.TracabiliteNumerique().verifier(r, con).ok


def test_un_total_fait_de_tete_reste_non_tracable(con):
    """La contre-épreuve du précédent, et le cas qui justifie tout le contrôle.

    Deux montants rendus par la base, leur somme calculée dans la tête du modèle : le
    résultat est juste ici, mais rien ne le garantit, et c'est précisément ce que le
    prompt interdit. Sans ce test, l'assouplissement ci-dessus pourrait tout laisser
    passer sans qu'on s'en aperçoive.
    """
    r = a.Resultat(
        reponse="1 000 € sur tv et 250 € sur sea, soit un total de 1 250 €.",
        sql=["SELECT channel, SUM(cost) FROM media GROUP BY channel"],
    )

    verdict = a.TracabiliteNumerique().verifier(r, con)
    assert not verdict.ok
    assert "1 250" in verdict.detail


def test_les_chiffres_annonces_par_le_prompt_sont_tracables(con, monkeypatch):
    """L'agent a le droit de citer ce que la description des données lui dit.

    Volumétries, bornes, cardinalités : elles sont générées depuis la base et vérifiées
    contre elle par les tests d'E3. Exiger qu'il requête ce qu'on vient de lui annoncer
    coûterait des tokens pour rien.
    """
    a._nombres_du_prompt.cache_clear()
    monkeypatch.setattr(
        a, "_nombres_du_prompt", lambda _con: frozenset({"366"})
    )
    r = a.Resultat(reponse="La table couvre 366 semaines.", sql=[])

    assert a.TracabiliteNumerique().verifier(r, con).ok


@pytest.mark.parametrize(
    "reponse, attendu",
    [
        ("La corrélation vaut 0,06.", True),      # 0,0565 arrondi à 2 décimales
        ("La corrélation vaut 0,09.", False),     # aucun arrondi n'y mène
    ],
)
def test_l_ecriture_declare_sa_propre_precision(con, reponse, attendu):
    """« 0,06 » affirme une valeur entre 0,055 et 0,065, pas une exactitude à 2 %.

    Juger un coefficient écrit à deux décimales par une tolérance *relative* réclame une
    précision que son auteur n'a pas revendiquée : 0,0565 rendu « 0,06 » se voyait compté
    comme inventé. La contre-épreuve garde la règle utile — un arrondi n'est pas une
    invention, mais tout n'est pas un arrondi.
    """
    r = a.Resultat(reponse=reponse, sql=["SELECT 0.0565"])

    assert a.TracabiliteNumerique().verifier(r, con).ok is attendu


@pytest.mark.parametrize(
    "reponse, attendu",
    [
        ("Corrélation négative : -0,23.", True),
        ("Corrélation négative : -0,50.", False),
    ],
)
def test_la_comparaison_porte_sur_les_grandeurs(con, reponse, attendu):
    """Le signe n'est pas capté par l'expression régulière.

    « −0,23 » en donne « 0,23 », qu'on opposait ensuite à une corrélation de −0,2278 :
    l'écart apparent valait 200 %, et toute corrélation négative correctement calculée
    était déclarée inventée. Limite assumée, dite dans la docstring de l'assertion : une
    réponse qui inverserait un signe passerait ici — c'est un défaut de lecture, pas
    d'invention, et ce contrôle ne traite que l'invention.
    """
    r = a.Resultat(reponse=reponse, sql=["SELECT -0.2278"])

    assert a.TracabiliteNumerique().verifier(r, con).ok is attendu


def test_un_ordre_de_grandeur_lu_de_loin_reste_non_tracable(con):
    """La contre-épreuve de tout ce qui précède, prise sur un cas réel.

    Assouplir la comparaison aux arrondis ne doit pas ouvrir la porte aux chiffres lus
    « à peu près » : sur la campagne de référence, une réponse citait des totaux
    trimestriels à 18 % de toute valeur qu'elle avait calculée. C'est exactement ce que
    ce contrôle existe pour attraper, et il doit continuer de le faire.
    """
    r = a.Resultat(
        reponse="Le pic du T3 atteint environ 484 800 compteurs.",
        sql=["SELECT 396919"],
    )

    verdict = a.TracabiliteNumerique().verifier(r, con)
    assert not verdict.ok
    assert "484 800" in verdict.detail


def test_les_nombres_du_prompt_sont_lus_sur_la_vraie_base():
    """Le repli sur l'ensemble vide ne doit jamais être le chemin de production.

    `_nombres_du_prompt` retombe sur un ensemble vide quand le schéma est incomplet —
    c'est ce qui permet aux bases d'essai de fonctionner. Sans ce test, une régression
    dans la génération du prompt affaiblirait la traçabilité en silence : plus aucune
    source annoncée, et personne pour le voir.
    """
    from src.db import connexion

    try:
        vraie = connexion.ouvrir()
    except FileNotFoundError:
        pytest.skip("base absente : lancer `python -m src.etl.build_db`")

    try:
        a._nombres_du_prompt.cache_clear()
        nombres = a._nombres_du_prompt(vraie)
    finally:
        vraie.close()

    assert nombres, "le repli sur l'ensemble vide a été pris sur la vraie base"

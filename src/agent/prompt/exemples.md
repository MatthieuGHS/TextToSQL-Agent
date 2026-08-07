## Exemples de requêtes

Ces exemples montrent des **formes** de requête, pas des réponses à retenir. Adapte-les.

### Agréger sur une dimension

> *Sur quelles entités investit-on, et depuis combien de temps ?*

```sql
SELECT entity,
       COUNT(DISTINCT step_date) AS semaines_actives,
       ROUND(SUM(cost))          AS investissement
FROM media
WHERE cost > 0
GROUP BY entity
ORDER BY investissement DESC
```

Le filtre `cost > 0` écarte les semaines sans achat d'espace, qui gonfleraient le compte de
semaines actives sans décrire d'activité.

### Calculer une période relative

> *Quelle est la durée moyenne des formats diffusés sur la dernière année de données ?*

```sql
SELECT channel,
       ROUND(AVG(duree_sec), 1) AS duree_moyenne_sec,
       COUNT(*)                 AS n
FROM media
WHERE duree_sec IS NOT NULL
  AND step_date > (SELECT MAX(step_date) FROM media) - INTERVAL 12 MONTH
GROUP BY channel
ORDER BY channel
```

La borne vient de `MAX(step_date)`, jamais de la date du jour : les données s'arrêtent à
une date passée, et `CURRENT_DATE` renverrait un résultat vide.

### Croiser deux tables — agréger d'abord, joindre ensuite

> *Comment notre pression publicitaire se compare-t-elle à celle des concurrents ?*

```sql
WITH nous AS (
    SELECT YEAR(step_date) AS annee, SUM(performance) AS grp
    FROM media
    WHERE performance_metric = 'grp'
    GROUP BY annee
), marche AS (
    SELECT YEAR(step_date) AS annee, SUM(value) AS grp
    FROM contexte
    WHERE metric = 'grp'
    GROUP BY annee
)
SELECT n.annee,
       ROUND(n.grp) AS grp_annonceur,
       ROUND(m.grp) AS grp_concurrents
FROM nous n
JOIN marche m ON m.annee = n.annee
ORDER BY n.annee DESC
```

**C'est la forme à retenir, et la raison est importante.** Joindre les deux tables
directement sur `step_date` donnerait des totaux faux : `media` a plusieurs lignes par
semaine et `contexte` en a une par marque, donc chaque ligne de l'une se combine à
plusieurs lignes de l'autre et les sommes sont multipliées. L'erreur ne produit aucun
message — seulement des chiffres trop grands, d'apparence plausible.

Agréger chaque côté séparément avant de joindre supprime le problème : chaque sous-requête
renvoie une ligne par année, la jointure est alors une à une.

### Lire le format long de `contexte`

> *Comment se situent les prix des différents fournisseurs ?*

```sql
SELECT brand_name, channel, ROUND(AVG(value), 2) AS prix_moyen
FROM contexte
WHERE metric = 'price'
  AND step_date > (SELECT MAX(step_date) FROM contexte) - INTERVAL 12 MONTH
GROUP BY brand_name, channel
ORDER BY brand_name, channel
```

Dans `contexte`, une ligne porte **une seule variable** : le filtre sur `metric` n'est pas
optionnel. Sans lui, la requête moyennerait des prix avec des parts de marché et des
compteurs — des grandeurs sans rapport, dans des unités différentes.

Noter que la borne se calcule sur `MAX(step_date)` **de `contexte`**, pas de `media` : les
trois tables ne s'arrêtent pas à la même date, et prendre la borne de la mauvaise table
décale la fenêtre sans rien signaler.

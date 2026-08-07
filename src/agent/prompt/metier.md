## Ce que décrivent les données

Les investissements publicitaires hebdomadaires d'un fournisseur d'énergie et leurs
indicateurs d'exposition, ainsi que son environnement de marché. Elles constituent
la matière première d'un futur modèle de *Marketing Mix Modeling* (MMM), qui n'est pas
encore construit.

Trois niveaux se suivent dans la chaîne publicitaire, et les données couvrent les trois
**sans les relier** :

| Niveau | Ce que c'est | Où | Unité |
|---|---|---|---|
| Dépense | l'argent investi en achat d'espace | `media.cost` | euros |
| Exposition | la pression publicitaire obtenue | `media.performance` | voir ci-dessus |
| Résultat commercial | les clients gagnés | `kpi_compteurs` | nombre de compteurs |

Dans `contexte`, l'unité dépend de `metric` : euros pour `price`, `cost`, `fee_parrain` et
`fee_filleul` ; nombre de compteurs pour `compteurs` ; proportion entre 0 et 1 pour
`market_share`, `taux_switch` et `ratio_top10_requetes`.

## Vocabulaire

La liste complète des canaux figure plus haut. Seuls ceux dont le nom ne se suffit pas :
`ooh` = affichage extérieur ; `sea` = liens sponsorisés, achetés ; `seo` = référencement
naturel, non acheté ; `affiliation` = partenaires rémunérés à la performance ; `display` =
bannières ; `audio` = radio numérique et podcasts.

**Métriques d'exposition.**
- `grp` — *Gross Rating Point* : pression publicitaire d'une campagne, soit la part de la
  cible touchée multipliée par le nombre moyen de contacts. Un indice, pas un effectif.
- `impressions` — nombre d'affichages d'une publicité.
- `clicks` — nombre de clics.

**Catégories.** `paid` = espace acheté ; `owned` = canaux propres, non achetés. Le `seo`
est le seul canal `owned`, ce qui explique que son `cost` soit NULL.

**Entités.** Les valeurs de `entity` énumérées plus haut sont des périmètres de prise de
parole du **même** annonceur, pas des annonceurs distincts : une somme sur plusieurs
entités reste donc licite, à condition de dire lesquelles.

**Mesures de `kpi_compteurs`**, toutes exprimées en nombre de compteurs :
- `mes` — mises en service : raccordements d'un logement au réseau.
- `cdf` — changements de fournisseur : le client vient d'un concurrent.
- `dem` — déménagements : le client existait déjà et change de logement.
- `new_counters_without_dem` — nouveaux compteurs hors déménagement.
- `inbound` / `outbound` — souscriptions entrantes / sortantes.
- `web` / `partners` — souscriptions via le site / via des partenaires.

Une identité comptable est garantie sur toutes les lignes :
`mes + cdf = new_counters_without_dem + dem`.

Aucune de ces huit mesures n'est *la* définition des « ventes » : elles décrivent des
mouvements différents. Si une question parle de ventes sans préciser, demande laquelle est
visée, ou donne-les et explique ce qui les distingue.

**Variables de `contexte`.**
- `price` — prix de l'offre, par marque et par énergie.
- `market_share` — part de marché.
- `taux_switch` — taux de changement de fournisseur sur le marché.
- `compteurs` — parc de compteurs des fournisseurs du marché : `histo` désigne l'opérateur
  historique, `alter` les fournisseurs alternatifs.
- `cost`, `grp` — investissements et pression publicitaire **des concurrents**.
- `fee_parrain`, `fee_filleul`, `active` — primes de parrainage et parrainages actifs.
- `contentquantity`, `ratio_top10_requetes` — volume de contenu publié et part de requêtes
  positionnées dans les dix premiers résultats, pour le référencement naturel.

## Ce que les données ne contiennent pas

**Aucune attribution.** Rien ne relie un canal à une vente. Il n'existe donc ni retour sur
investissement, ni coût par acquisition, ni contribution d'un canal aux compteurs gagnés,
ni classement des canaux par efficacité. Ces grandeurs ne sont pas absentes par oubli :
les estimer est précisément l'objet du modèle MMM à venir, et cela demande une
modélisation statistique que ces tables ne permettent pas.

Quand une question demande ce type de résultat, explique-le plutôt que de proposer un
substitut. Un coût divisé par une exposition n'est pas un coût d'acquisition, et le
présenter comme tel serait trompeur.

**Aucune dimension géographique, démographique ou socio-professionnelle.** Ni région, ni
département, ni âge, ni type de logement. Une question qui porte sur l'une de ces
dimensions n'a pas de réponse dans ces données.

**Aucune donnée individuelle.** Tout est agrégé à la semaine.

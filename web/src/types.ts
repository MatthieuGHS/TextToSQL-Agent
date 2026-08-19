// Les types de la frontière, recopiés de `src/app/schemas.py`.
//
// Recopiés et non générés : un générateur de types depuis OpenAPI ajouterait une étape de
// construction et une dépendance, pour cinq interfaces qui tiennent dans un écran. Le jour
// où la frontière grossit, la génération deviendra le bon choix — pas avant.

export type Cellule = string | number | boolean | null | Cellule[]

export interface Requete {
  sql: string
  colonnes: string[]
  lignes: Cellule[][]
  tronque: boolean
  duree_ms: number
  /** Non nulle sur un tâtonnement : la boucle se reprend, on l'affiche sans le cacher. */
  erreur: string | null
}

export interface Serie {
  colonne: string
  /** Les `null` sont des trous, jamais des zéros : la courbe doit s'y interrompre. */
  valeurs: (number | null)[]
  axe_secondaire: boolean
}

/**
 * La spécification décidée par `src/charts`, côté serveur.
 *
 * L'interface ne choisit ni le type, ni les axes, ni ce qui se trace — elle rend. Toute
 * règle de lisibilité ajoutée ici serait au mauvais endroit : elle échapperait aux tests
 * en fonctions pures et se dédoublerait avec celles du serveur.
 */
export interface Graphique {
  /** `nuage` : deux mesures sans abscisse — les étiquettes sont alors numériques. */
  type: 'courbe' | 'barres' | 'nuage'
  x: string
  etiquettes: (string | number)[]
  series: Serie[]
}

export interface Usage {
  entree: number
  sortie: number
  cache_lu: number
  cache_ecrit: number
}

export interface Reponse {
  texte: string
  requetes: Requete[]
  /** `null` quand le résultat ne se trace pas — le cas fréquent, et un résultat en soi. */
  graphique: Graphique | null
  arret: string
  /** Calculé côté serveur. Ne jamais le redéduire de `arret` ici : ça divergerait. */
  arret_normal: boolean
  usage: Usage
  modele: string
}

export interface Sante {
  base_presente: boolean
  cle_chargee: boolean
  modele: string
  tables: string[]
}

/** Une étape poussée par le flux pendant que l'agent travaille. */
export type Etape =
  | { type: 'reflexion'; tour: number; sur: number }
  | { type: 'requete'; sql: string; lignes: number; duree_ms: number; erreur: string | null }
  | { type: 'redaction' }

export type Evenement =
  | Etape
  | { type: 'reponse'; reponse: Reponse }
  | { type: 'erreur'; message: string }

export interface Message {
  role: 'utilisateur' | 'agent'
  texte: string
  reponse?: Reponse
  /**
   * Les étapes traversées pour produire cette réponse, conservées après coup.
   *
   * Elles ne font pas double emploi avec `reponse.requetes` : celles-ci disent *ce qui a
   * été exécuté*, les étapes disent *dans quel ordre et en combien de tours*. Un
   * tâtonnement suivi d'une reprise se lit dans les secondes, pas dans la liste finale.
   */
  etapes?: Etape[]
  /** Renseigné quand la réponse n'a pas abouti normalement — tronquée, abandonnée. */
  avertissement?: string
}

// --- Page de chargement des données ---------------------------------------------------

export interface FichierSource {
  nom: string
  present: boolean
  octets: number | null
  modifie_le: string | null
  /** `features.csv` ne l'est pas : il sert à vérifier la couverture, pas à construire. */
  requis: boolean
}

export interface EtatDonnees {
  fichiers: FichierSource[]
  base_presente: boolean
  base_modifiee_le: string | null
  tables: string[]
}

export type EvenementEtl =
  | { type: 'journal'; niveau: string; message: string }
  | { type: 'termine'; fichiers: string[] }
  | { type: 'erreur'; message: string }


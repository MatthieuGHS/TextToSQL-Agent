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

export interface Usage {
  entree: number
  sortie: number
  cache_lu: number
  cache_ecrit: number
}

export interface Reponse {
  texte: string
  requetes: Requete[]
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

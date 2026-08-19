import type {
  Etape,
  EtatDonnees,
  Evenement,
  EvenementEtl,
  Message,
  Reponse,
  Sante,
} from './types'

/**
 * L'historique renvoyé au serveur : l'API est sans état, c'est l'interface qui détient
 * la conversation. Seuls les tours aboutis y entrent — réinjecter un message d'erreur
 * apprendrait à l'agent à imiter ses propres pannes.
 */
function historique(messages: Message[]) {
  const tours: { question: string; reponse: string }[] = []
  for (let i = 0; i < messages.length - 1; i++) {
    const question = messages[i]
    const reponse = messages[i + 1]
    if (question.role === 'utilisateur' && reponse.role === 'agent' && reponse.reponse) {
      tours.push({ question: question.texte, reponse: reponse.texte })
    }
  }
  return tours
}

export async function sante(): Promise<Sante> {
  const r = await fetch('/api/sante')
  if (!r.ok) throw new Error('service indisponible')
  return r.json()
}

/**
 * Pose une question et rapporte chaque événement au fur et à mesure.
 *
 * Le flux est du NDJSON : une ligne de JSON par événement. Le tampon est nécessaire —
 * une lecture réseau ne s'aligne pas sur les fins de ligne, et découper naïvement chaque
 * morceau reçu couperait un objet JSON en deux une fois sur dix.
 */
export async function demander(
  question: string,
  messages: Message[],
  // `Etape` et non `Evenement` : la réponse finale et l'erreur sont traitées ici, pas
  // rendues à l'appelant. Le type dit donc exactement ce qui sort de cette fonction.
  surEtape: (e: Etape) => void,
  signal?: AbortSignal,
): Promise<Reponse> {
  const r = await fetch('/api/question/flux', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, historique: historique(messages) }),
    signal,
  })
  if (!r.ok || !r.body) throw new Error(`le service a répondu ${r.status}`)

  const lecteur = r.body.getReader()
  const decodeur = new TextDecoder()
  let tampon = ''
  let finale: Reponse | null = null

  for (;;) {
    const { done, value } = await lecteur.read()
    if (done) break
    tampon += decodeur.decode(value, { stream: true })

    const lignes = tampon.split('\n')
    tampon = lignes.pop() ?? ''
    for (const ligne of lignes) {
      if (!ligne.trim()) continue
      const evenement = JSON.parse(ligne) as Evenement
      if (evenement.type === 'erreur') throw new Error(evenement.message)
      if (evenement.type === 'reponse') finale = evenement.reponse
      else surEtape(evenement)
    }
  }

  if (!finale) throw new Error("le flux s'est interrompu avant la réponse")
  return finale
}

export async function etatDonnees(): Promise<EtatDonnees> {
  const r = await fetch('/api/donnees')
  if (!r.ok) throw new Error('service indisponible')
  return r.json()
}

/**
 * Téléverse les fichiers et relance la pipeline, en rapportant le journal au fil de l'eau.
 *
 * Sans fichier, c'est une simple reconstruction depuis les sources en place — utile quand
 * le contrat de données se met à échouer sans qu'aucune source ait bougé.
 *
 * Un refus arrive en HTTP (422, 413) *avant* le flux : la liste des fichiers est validée
 * avant qu'aucun travail ne commence. Une fois le flux ouvert, le statut est déjà parti et
 * l'échec ne peut plus être qu'un événement.
 */
export async function recharger(
  fichiers: File[],
  surEvenement: (e: EvenementEtl) => void,
): Promise<void> {
  const corps = new FormData()
  for (const f of fichiers) corps.append('fichiers', f)

  const r = await fetch('/api/donnees/recharger', { method: 'POST', body: corps })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail ?? `le service a répondu ${r.status}`)
  }
  if (!r.body) throw new Error('réponse vide')

  const lecteur = r.body.getReader()
  const decodeur = new TextDecoder()
  let tampon = ''

  for (;;) {
    const { done, value } = await lecteur.read()
    if (done) break
    tampon += decodeur.decode(value, { stream: true })
    const lignes = tampon.split('\n')
    tampon = lignes.pop() ?? ''
    for (const ligne of lignes) {
      if (ligne.trim()) surEvenement(JSON.parse(ligne) as EvenementEtl)
    }
  }
}


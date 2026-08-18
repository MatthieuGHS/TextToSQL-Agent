import { useState } from 'react'
import type { Message as TypeMessage } from '../types'
import { BlocRequete } from './BlocRequete'
import { Markdown } from './Markdown'

/**
 * Un tour de conversation.
 *
 * L'avertissement d'arrêt anormal est affiché **au-dessus** de la réponse, pas en dessous :
 * une réponse tronquée ou rendue après abandon paraît complète, et l'avertissement placé
 * après aurait déjà été doublé par la lecture.
 *
 * `arret_normal` vient du serveur et n'est jamais redéduit ici — le rejouer côté navigateur
 * le ferait diverger un jour, et un abandon finirait par s'afficher comme un succès.
 */
function BoutonCopier({ texte }: { texte: string }) {
  const [copie, setCopie] = useState(false)

  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(texte)
        setCopie(true)
        setTimeout(() => setCopie(false), 1500)
      }}
      className="absolute -top-1 right-0 rounded-md px-2 py-1 text-xs text-slate-500
                 opacity-0 transition-opacity group-hover:opacity-100
                 hover:bg-slate-800 hover:text-slate-300"
    >
      {copie ? 'copié' : 'copier'}
    </button>
  )
}


export function Message({ message }: { message: TypeMessage }) {
  if (message.role === 'utilisateur') {
    return (
      <div className="flex justify-end">
        <div
          className="max-w-[80%] rounded-2xl rounded-br-md bg-sky-600/90 px-4 py-2.5
                     text-[15px] text-white whitespace-pre-wrap"
        >
          {message.texte}
        </div>
      </div>
    )
  }

  const requetes = message.reponse?.requetes ?? []

  return (
    <div className="flex flex-col gap-3">
      {message.avertissement && (
        <div
          className="rounded-lg border border-amber-800/60 bg-amber-950/30 px-3 py-2
                     text-sm text-amber-300"
        >
          {message.avertissement}
        </div>
      )}

      <div className="group relative">
        <Markdown texte={message.texte} />
        <BoutonCopier texte={message.texte} />
      </div>

      {requetes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <p className="text-xs uppercase tracking-wide text-slate-600">
            {requetes.length} requête{requetes.length > 1 ? 's' : ''} exécutée
            {requetes.length > 1 ? 's' : ''}
          </p>
          {requetes.map((r, i) => (
            <BlocRequete key={i} requete={r} />
          ))}
        </div>
      )}
    </div>
  )
}

import type { Message as TypeMessage } from '../types'
import { BlocRequete } from './BlocRequete'

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

      <div
        className="text-[15px] leading-relaxed text-slate-200 whitespace-pre-wrap"
      >
        {message.texte}
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

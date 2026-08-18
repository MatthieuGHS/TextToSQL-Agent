import { useState } from 'react'
import type { Requete } from '../types'
import { TableauResultat } from './TableauResultat'

/**
 * Une requête exécutée, dépliable.
 *
 * Les tâtonnements — requêtes refusées ou fautives — sont affichés au même titre que les
 * réussites, en rouge et repliés. Ce ne sont pas des fautes : la boucle est faite pour se
 * reprendre, et les masquer donnerait de l'exécution une image plus lisse que la réalité.
 */
export function BlocRequete({ requete }: { requete: Requete }) {
  const [ouvert, setOuvert] = useState(false)
  const enEchec = requete.erreur !== null

  return (
    <div
      className={`rounded-lg border overflow-hidden ${
        enEchec ? 'border-red-900/60 bg-red-950/20' : 'border-slate-800 bg-slate-900/40'
      }`}
    >
      <button
        onClick={() => setOuvert(!ouvert)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left
                   hover:bg-slate-800/40 transition-colors"
      >
        <span
          className={`text-xs transition-transform ${ouvert ? 'rotate-90' : ''}
                      text-slate-500`}
        >
          ▶
        </span>
        <span
          className={`text-xs font-medium uppercase tracking-wide ${
            enEchec ? 'text-red-400' : 'text-emerald-400'
          }`}
        >
          {enEchec ? 'requête en échec' : 'requête'}
        </span>
        <code className="flex-1 truncate text-xs font-mono text-slate-400">
          {requete.sql.replace(/\s+/g, ' ')}
        </code>
        {!enEchec && (
          <span className="text-xs text-slate-600 tabular-nums whitespace-nowrap">
            {requete.lignes.length} l · {requete.duree_ms} ms
          </span>
        )}
      </button>

      {ouvert && (
        <div className="border-t border-slate-800">
          <pre
            className="px-3 py-2 text-xs font-mono text-slate-300 whitespace-pre-wrap
                       bg-slate-950/50 overflow-x-auto"
          >
            {requete.sql}
          </pre>
          {enEchec ? (
            <p
              className="px-3 py-2 text-xs font-mono text-red-300 whitespace-pre-wrap
                         border-t border-slate-800"
            >
              {requete.erreur}
            </p>
          ) : (
            <div className="border-t border-slate-800">
              <TableauResultat
                colonnes={requete.colonnes}
                lignes={requete.lignes}
                tronque={requete.tronque}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

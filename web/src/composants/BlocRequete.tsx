import { useState } from 'react'
import type { Requete } from '../types'
import { Graphique } from './Graphique'
import { SqlColore } from './SqlColore'
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
  // À la demande seulement : le tracé automatique reste réservé à la dernière requête,
  // celle qui porte la conclusion. Les précédentes sont des explorations.
  const [trace, setTrace] = useState(false)
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
        {/* Dans l'en-tête, donc lisibles **sans déplier** : le client veut voir d'un coup
            d'œil quelles tables une requête a lues, et deux périmètres cohabitent dans ce
            jeu de données — `media` est l'annonceur seul, `contexte` le marché entier.
            Savoir laquelle a répondu est ce qui distingue un chiffre juste d'un chiffre
            crédible. */}
        {requete.tables.length > 0 && (
          <span className="hidden sm:flex gap-1 shrink-0">
            {requete.tables.map((t) => (
              <span
                key={t}
                className="rounded bg-slate-800/80 px-1.5 py-0.5 text-[11px]
                           font-mono text-slate-400"
              >
                {t}
              </span>
            ))}
          </span>
        )}
        {!enEchec && (
          <span className="text-xs text-slate-600 tabular-nums whitespace-nowrap">
            {requete.lignes.length} l · {requete.duree_ms} ms
          </span>
        )}
      </button>

      {ouvert && (
        <div className="border-t border-slate-800">
          {/* Au-dessus du SQL, et non en dessous : il a été écrit avant lui, et c'est
              ce qui explique la requête qu'on s'apprête à lire. */}
          {requete.raisonnement && (
            <p
              className="px-3 py-2 text-xs italic leading-relaxed text-slate-400
                         border-b border-slate-800/60"
            >
              {requete.raisonnement}
            </p>
          )}
          <SqlColore sql={requete.sql} />
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
              {requete.graphique && (
                <div className="border-t border-slate-800 p-2">
                  <button
                    onClick={() => setTrace(!trace)}
                    className="text-xs text-slate-500 hover:text-slate-300
                               transition-colors"
                  >
                    {trace ? '▾ masquer le graphique' : '▸ tracer ce résultat'}
                  </button>
                  {trace && (
                    <div className="mt-2">
                      <Graphique graphique={requete.graphique} />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

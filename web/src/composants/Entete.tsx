import type { Sante } from '../types'

/** L'état du service, visible en permanence : c'est ce qu'on regarde avant de démarrer. */
export function Entete({ sante, erreur }: { sante: Sante | null; erreur: string | null }) {
  const pret = sante?.base_presente && sante?.cle_chargee

  let etat = 'vérification…'
  if (erreur) etat = 'service injoignable'
  else if (sante && !sante.base_presente) etat = 'base absente'
  else if (sante && !sante.cle_chargee) etat = 'clé API absente'
  else if (sante) etat = `${sante.tables.length} tables · ${sante.modele}`

  return (
    <header
      className="shrink-0 border-b border-slate-800 bg-slate-950/80 backdrop-blur
                 px-6 py-3 flex items-center justify-between gap-4"
    >
      <div>
        <h1 className="text-[15px] font-semibold text-slate-100">Agent data — média</h1>
        <p className="text-xs text-slate-500">
          Exploration et audit des données avant modélisation
        </p>
      </div>
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span
          className={`size-2 rounded-full ${
            erreur || (sante && !pret)
              ? 'bg-red-500'
              : pret
                ? 'bg-emerald-500'
                : 'bg-slate-600 animate-pulse'
          }`}
        />
        <span className="font-mono">{etat}</span>
      </div>
    </header>
  )
}

import type { Sante } from '../types'
import { useTheme } from '../theme'

export type Page = 'conversation' | 'donnees'

/**
 * La bascule de thème. Un bouton, pas un menu : il n'y a que deux thèmes, et un
 * sélecteur à trois entrées — clair, sombre, système — demanderait de gérer un état
 * « aucun choix » que le défaut clair rend sans objet.
 *
 * L'icône montre **ce vers quoi on va**, pas l'état courant : c'est ce que fait un
 * interrupteur, et l'inverse se lit comme une décoration.
 */
function BasculeTheme() {
  const { theme, basculer } = useTheme()
  const vers = theme === 'clair' ? 'sombre' : 'clair'

  return (
    <button
      onClick={basculer}
      title={`Passer au thème ${vers}`}
      aria-label={`Passer au thème ${vers}`}
      className="rounded-lg border border-bordure p-1.5 text-texte-faible
                 hover:text-texte hover:border-bordure-appuyee transition-colors"
    >
      {theme === 'clair' ? (
        // Lune : on va vers le sombre.
        <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z" />
        </svg>
      ) : (
        // Soleil : on va vers le clair.
        <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20
                   12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
        </svg>
      )}
    </button>
  )
}

/** L'état du service, visible en permanence : c'est ce qu'on regarde avant de démarrer. */
export function Entete({
  sante,
  erreur,
  page,
  onPage,
}: {
  sante: Sante | null
  erreur: string | null
  page: Page
  onPage: (p: Page) => void
}) {
  const pret = sante?.base_presente && sante?.cle_chargee

  let etat = 'vérification…'
  if (erreur) etat = 'service injoignable'
  else if (sante && !sante.base_presente) etat = 'base absente'
  else if (sante && !sante.cle_chargee) etat = 'clé API absente'
  else if (sante) etat = `${sante.tables.length} tables · ${sante.modele}`

  return (
    <header
      className="shrink-0 border-b border-bordure bg-fond/80 backdrop-blur
                 px-6 py-3 flex items-center justify-between gap-4"
    >
      <div className="flex items-center gap-6">
        <div>
          <h1 className="text-[15px] font-semibold text-texte-fort">Agent data — média</h1>
          <p className="text-xs text-texte-faible">
            Exploration et audit des données avant modélisation
          </p>
        </div>
        {/* Deux pages : un routeur serait du mécanisme pour rien, et l'URL n'a pas à être
            partageable — l'application est mono-utilisateur et sans état serveur. */}
        <nav className="flex gap-1">
          {([
            ['conversation', 'Conversation'],
            ['donnees', 'Données'],
          ] as const).map(([cle, libelle]) => (
            <button
              key={cle}
              onClick={() => onPage(cle)}
              className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                page === cle
                  ? 'bg-surface-appuyee text-texte-fort'
                  : 'text-texte-faible hover:text-texte'
              }`}
            >
              {libelle}
            </button>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-3 text-xs text-texte-faible">
        <div className="flex items-center gap-2">
          <span
            className={`size-2 rounded-full ${
              erreur || (sante && !pret)
                ? 'bg-erreur'
                : pret
                  ? 'bg-succes'
                  : 'bg-texte-faible animate-pulse'
            }`}
          />
          <span className="font-mono">{etat}</span>
        </div>
        <BasculeTheme />
      </div>
    </header>
  )
}

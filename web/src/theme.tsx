import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

/**
 * Le thème courant, et de quoi en changer.
 *
 * **Un contexte et non une constante de module**, pour une raison précise : `Graphique`
 * est mémoïsé. Recharts veut des couleurs littérales — il dessine dans un `<svg>`, une
 * classe Tailwind n'y peindrait rien — donc sa palette ne peut pas venir de la feuille
 * de style. Si elle venait d'un module, changer de thème ne changerait aucune prop du
 * composant, `memo` court-circuiterait le rendu, et les courbes resteraient dans
 * l'ancienne palette pendant que tout le reste de la page a basculé. Un contexte, lui,
 * traverse `memo` : tout consommateur se redessine quand sa valeur change.
 *
 * Le reste de l'interface n'a pas besoin d'ici : ses couleurs sont des jetons CSS, et
 * l'attribut posé sur `<html>` suffit à les repeindre.
 */
export type Theme = 'clair' | 'sombre'

const CLE = 'theme'

/** Le défaut est le thème clair, sur demande du client — pas la préférence du système. */
export const THEME_PAR_DEFAUT: Theme = 'clair'

/**
 * Lit le choix retenu. Exportée pour que le script d'amorçage de `index.html` et React
 * ne puissent pas diverger sur ce que « retenu » veut dire.
 */
export function themeRetenu(): Theme {
  try {
    return localStorage.getItem(CLE) === 'sombre' ? 'sombre' : THEME_PAR_DEFAUT
  } catch {
    // Navigation privée, stockage refusé : le thème par défaut reste utilisable.
    return THEME_PAR_DEFAUT
  }
}

interface Valeur {
  theme: Theme
  basculer: () => void
}

const Contexte = createContext<Valeur>({ theme: THEME_PAR_DEFAUT, basculer: () => {} })

export function FournisseurTheme({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(themeRetenu)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(CLE, theme)
    } catch {
      // Sans stockage, la bascule vaut pour la session — c'est dégradé, pas cassé.
    }
  }, [theme])

  const basculer = useCallback(
    () => setTheme((t) => (t === 'clair' ? 'sombre' : 'clair')),
    [],
  )

  return <Contexte.Provider value={{ theme, basculer }}>{children}</Contexte.Provider>
}

export function useTheme(): Valeur {
  return useContext(Contexte)
}

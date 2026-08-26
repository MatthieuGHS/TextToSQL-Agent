import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Nettoyage entre deux cas, à faire à la main : testing-library ne l'enregistre tout seul
// que si `globals` est activé, ce qu'on ne veut pas — les imports explicites disent d'où
// vient chaque fonction. Sans ça le DOM d'un cas survit au suivant, et une recherche par
// texte trouve deux éléments là où le test en attend un. Le piège s'est déclenché sur le
// premier fichier de test à rendre plusieurs fois le même composant ; le fermer ici évite
// que le suivant y retombe.
afterEach(cleanup)

// jsdom ne fournit pas `localStorage` dans cette version : `typeof localStorage` y est
// `undefined`, là où tout navigateur le définit. Le code de thème s'en accommode — ses
// accès sont gardés, parce qu'un navigateur en navigation privée peut aussi le refuser —
// mais sans stub, la persistance du choix de thème ne serait couverte par rien du tout.
// On rapproche donc l'environnement du navigateur plutôt que d'amputer le test.
if (typeof globalThis.localStorage === 'undefined') {
  let contenu: Record<string, string> = {}
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (c: string) => (c in contenu ? contenu[c] : null),
      setItem: (c: string, v: string) => {
        contenu[c] = String(v)
      },
      removeItem: (c: string) => {
        delete contenu[c]
      },
      clear: () => {
        contenu = {}
      },
    },
  })
}

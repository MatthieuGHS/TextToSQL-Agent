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

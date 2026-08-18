import type { Cellule } from '../types'

/**
 * Les lignes brutes d'une requête.
 *
 * C'est ce qui rend une réponse vérifiable : le client voit la valeur, la requête qui l'a
 * produite, et le résultat de cette requête. Un chiffre sans sa provenance est exactement
 * ce que le projet cherche à éviter.
 *
 * L'affichage est borné à `MAX_LIGNES` — le serveur en renvoie jusqu'à 200, et dérouler
 * 200 lignes dans un fil de conversation le rend illisible. La troncature est **annoncée**,
 * comme celle du serveur : laisser croire qu'on a tout vu est le défaut à ne pas
 * reproduire ici.
 */
const MAX_LIGNES = 12

// Les dates traversent l'API en ISO — c'est le bon format pour un transport, et le
// mauvais pour un tableau lu par un humain : `2024-12-30T00:00:00` se déchiffre au lieu
// de se lire. La conversion est faite ici, à l'affichage, et jamais côté serveur : la
// frontière doit rester lisible par un client qui n'est pas cette interface.
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/

function formater(v: Cellule): string {
  if (v === null) return '∅'
  if (typeof v === 'number') return new Intl.NumberFormat('fr-FR').format(v)
  if (Array.isArray(v)) return v.map(formater).join(', ')
  if (typeof v === 'string') {
    const d = ISO_DATE.exec(v)
    // L'heure n'est affichée que si elle porte une information. Les dates de ce jeu de
    // données sont des semaines : afficher « 00:00 » sur chaque ligne remplirait une
    // colonne entière de bruit.
    if (d) {
      const jour = `${d[3]}/${d[2]}/${d[1]}`
      return d[4] && (d[4] !== '00' || d[5] !== '00') ? `${jour} ${d[4]}:${d[5]}` : jour
    }
  }
  return String(v)
}

/** Une colonne est numérique si toutes ses valeurs renseignées le sont. */
function estNumerique(lignes: Cellule[][], colonne: number): boolean {
  const valeurs = lignes.map((l) => l[colonne]).filter((v) => v !== null)
  return valeurs.length > 0 && valeurs.every((v) => typeof v === 'number')
}

export function TableauResultat({
  colonnes,
  lignes,
  tronque,
}: {
  colonnes: string[]
  lignes: Cellule[][]
  tronque: boolean
}) {
  if (lignes.length === 0) {
    return (
      <p className="px-3 py-2 text-sm text-slate-500 italic">
        Résultat vide : aucune ligne ne correspond.
      </p>
    )
  }

  const visibles = lignes.slice(0, MAX_LIGNES)
  const cachees = lignes.length - visibles.length
  // Calculé sur les lignes affichées, pour que l'en-tête suive l'alignement de ce qu'on
  // voit réellement en dessous. Un en-tête à gauche au-dessus de nombres alignés à droite
  // se lit comme deux colonnes différentes.
  const numeriques = colonnes.map((_, i) => estNumerique(visibles, i))

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-700">
              {colonnes.map((c, i) => (
                <th
                  key={c}
                  className={`px-3 py-2 font-medium text-slate-400 whitespace-nowrap ${
                    numeriques[i] ? 'text-right' : 'text-left'
                  }`}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibles.map((ligne, i) => (
              <tr key={i} className="border-b border-slate-800/60 last:border-0">
                {ligne.map((v, j) => (
                  <td
                    key={j}
                    className={`px-3 py-1.5 whitespace-nowrap ${
                      typeof v === 'number'
                        ? 'text-right font-mono tabular-nums text-slate-200'
                        : 'text-slate-300'
                    } ${v === null ? 'text-slate-600' : ''}`}
                  >
                    {formater(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(cachees > 0 || tronque) && (
        <p className="px-3 py-2 text-xs text-slate-500 border-t border-slate-800">
          {cachees > 0 && `${cachees} ligne${cachees > 1 ? 's' : ''} non affichée${
            cachees > 1 ? 's' : ''
          }`}
          {cachees > 0 && tronque && ' · '}
          {tronque && 'résultat tronqué par le plafond de lignes du serveur'}
        </p>
      )}
    </div>
  )
}

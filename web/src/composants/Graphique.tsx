import { memo, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Graphique as TypeGraphique } from '../types'
import { useTheme } from '../theme'

/**
 * Le rendu de la spécification produite par `src/charts`.
 *
 * **Ce composant ne décide de rien.** Le type de graphique, l'axe des abscisses, les
 * séries et leur répartition sur deux axes ont été choisis côté serveur, en code
 * déterministe et testé en fonctions pures. Ajouter ici une règle de lisibilité la
 * placerait hors de portée de ces tests et la dédoublerait ; s'il en manque une, elle va
 * dans `src/charts/specification.py`.
 */
// Douze couleurs : le maximum de séries qu'une spécification peut porter (pivot par
// catégorie, `MAX_SERIES_PIVOT` côté serveur). Écartées en teinte pour rester
// distinguables ; en élargir la liste sans élargir le plafond serveur ne servirait à
// rien — c'est lui qui décide.
//
// **Deux palettes, mêmes teintes, luminosités opposées.** Recharts peint dans un `<svg>`
// et veut des littéraux : une classe Tailwind n'y peindrait rien, donc ces couleurs ne
// peuvent pas venir de la feuille de style et échappent seules à la bascule par jetons.
// Les teintes claires du thème sombre virent au délavé sur fond blanc, et l'inverse est
// vrai aussi — d'où une dérivation par thème plutôt qu'une palette unique de compromis.
const PALETTES = {
  sombre: [
    '#38bdf8', '#f472b6', '#a78bfa', '#4ade80', '#fb923c', '#facc15',
    '#2dd4bf', '#f87171', '#818cf8', '#a3e635', '#e879f9', '#94a3b8',
  ],
  clair: [
    '#0284c7', '#db2777', '#7c3aed', '#16a34a', '#ea580c', '#ca8a04',
    '#0d9488', '#dc2626', '#4f46e5', '#65a30d', '#c026d3', '#475569',
  ],
} as const

// Le décor du graphique — grille, graduations, infobulle. Mêmes raisons, même forme.
const DECORS = {
  sombre: {
    grille: '#1e293b',
    texte: '#64748b',
    infobulleFond: '#0f172a',
    infobulleBordure: '#334155',
    infobulleTitre: '#94a3b8',
    zoom: '#334155',
  },
  clair: {
    grille: '#e2e8f0',
    texte: '#64748b',
    infobulleFond: '#ffffff',
    infobulleBordure: '#cbd5e1',
    infobulleTitre: '#475569',
    zoom: '#cbd5e1',
  },
} as const

// En deçà, la réglette de zoom serait du bruit : tout tient déjà à l'écran. Au-delà —
// une année hebdomadaire et plus — elle permet de resserrer sur une plage de dates.
const SEUIL_ZOOM = 24

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/

/** Les dates arrivent en ISO ; l'abscisse d'un graphique n'a pas la place d'un horodatage. */
function etiquette(v: string | number): string {
  if (typeof v === 'number') return String(v)
  const d = ISO_DATE.exec(v)
  return d ? `${d[3]}/${d[2]}/${d[1].slice(2)}` : v
}

const compact = new Intl.NumberFormat('fr-FR', { notation: 'compact' })
const complet = new Intl.NumberFormat('fr-FR')

/**
 * Transpose la spécification, orientée colonnes, vers le tableau d'objets que Recharts
 * consomme.
 *
 * Exportée pour être testée seule : c'est la seule logique de ce fichier, et la tester au
 * travers d'un rendu obligerait à faire cohabiter Recharts et jsdom pour vérifier une
 * transposition de tableau.
 *
 * Les `null` sont recopiés tels quels — c'est ce qui permet à la courbe de s'interrompre
 * au lieu de relier deux points de part et d'autre d'une valeur absente.
 */
export function donneesDe(graphique: TypeGraphique) {
  // Un nuage garde son abscisse **numérique** : la formater en étiquette en ferait un
  // axe catégoriel, et Recharts espacerait les points au rang au lieu de la valeur —
  // un nuage faussé qui aurait pourtant l'air juste.
  if (graphique.type === 'nuage') {
    return graphique.etiquettes.map((e, i) => ({
      x: e as number,
      [graphique.series[0].colonne]: graphique.series[0].valeurs[i],
    }))
  }
  return graphique.etiquettes.map((e, i) => {
    const point: Record<string, string | number | null> = { x: etiquette(e) }
    for (const s of graphique.series) point[s.colonne] = s.valeurs[i]
    return point
  })
}

/**
 * Mémoïsé, comme `Message` et pour la même raison : une spécification ne change pas une
 * fois la réponse rendue, et re-transposer plusieurs centaines de points à chaque frappe
 * dans la zone de saisie coûtait cher pour un résultat identique.
 */
export const Graphique = memo(function Graphique({
  graphique,
}: {
  graphique: TypeGraphique
}) {
  const donnees = useMemo(() => donneesDe(graphique), [graphique])

  // La bascule ne choisit que parmi ce que le serveur a déclaré licite (`variantes`,
  // `empilable`) : aucune règle de lisibilité ne naît ici.
  const [affiche, setAffiche] = useState(graphique.type)
  const [empile, setEmpile] = useState(false)
  const bascules = [graphique.type, ...graphique.variantes]
  const peutEmpiler =
    graphique.empilable && graphique.series.length > 1 && affiche === 'barres'

  const double = graphique.series.some((s) => s.axe_secondaire)
  // `useContext` traverse `memo` : sans lui, la bascule ne changeant aucune prop, les
  // courbes resteraient peintes dans l'ancienne palette pendant que le reste de la page
  // a déjà basculé.
  const { theme } = useTheme()
  const COULEURS = PALETTES[theme]
  const decor = DECORS[theme]
  const axes = { grid: decor.grille, texte: decor.texte }

  const communs = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke={axes.grid} vertical={false} />
      <XAxis
        dataKey="x"
        tick={{ fill: axes.texte, fontSize: 11 }}
        stroke={axes.grid}
        minTickGap={24}
      />
      <YAxis
        yAxisId="gauche"
        tick={{ fill: axes.texte, fontSize: 11 }}
        stroke={axes.grid}
        tickFormatter={(v) => compact.format(v as number)}
        width={52}
      />
      {double && (
        <YAxis
          yAxisId="droite"
          orientation="right"
          tick={{ fill: axes.texte, fontSize: 11 }}
          stroke={axes.grid}
          tickFormatter={(v) => compact.format(v as number)}
          width={52}
        />
      )}
      <Tooltip
        contentStyle={{
          background: decor.infobulleFond,
          border: `1px solid ${decor.infobulleBordure}`,
          borderRadius: 8,
          fontSize: 12,
        }}
        labelStyle={{ color: decor.infobulleTitre }}
        formatter={(v) => (v === null ? '—' : complet.format(v as number))}
      />
      <Legend
        wrapperStyle={{ fontSize: 12, color: axes.texte }}
        iconType="plainline"
        iconSize={14}
      />
    </>
  )

  if (graphique.type === 'nuage') {
    // Le nuage a ses propres axes : tous deux **numériques**, là où courbe et barres
    // portent une abscisse d'étiquettes. Réutiliser `communs` espacerait les points au
    // rang de la ligne au lieu de sa valeur.
    const serie = graphique.series[0]
    return (
      <figure className="rounded-lg border border-bordure bg-surface p-3">
        <ResponsiveContainer width="100%" height={280}>
          <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={axes.grid} />
            <XAxis
              type="number"
              dataKey="x"
              name={graphique.x}
              tick={{ fill: axes.texte, fontSize: 11 }}
              stroke={axes.grid}
              tickFormatter={(v) => compact.format(v as number)}
              domain={['auto', 'auto']}
            />
            <YAxis
              type="number"
              dataKey={serie.colonne}
              name={serie.colonne}
              tick={{ fill: axes.texte, fontSize: 11 }}
              stroke={axes.grid}
              tickFormatter={(v) => compact.format(v as number)}
              width={52}
              domain={['auto', 'auto']}
            />
            <Tooltip
              contentStyle={{
                background: decor.infobulleFond,
                border: `1px solid ${decor.infobulleBordure}`,
                borderRadius: 8,
                fontSize: 12,
              }}
              labelStyle={{ color: decor.infobulleTitre }}
              formatter={(v) => complet.format(v as number)}
              cursor={{ strokeDasharray: '3 3' }}
            />
            <Scatter
              data={donnees}
              fill={COULEURS[0]}
              fillOpacity={0.75}
              isAnimationActive={false}
            />
          </ScatterChart>
        </ResponsiveContainer>
        <figcaption className="mt-1 text-center text-xs text-texte-faible">
          {serie.colonne} selon {graphique.x} — chaque point est une ligne du résultat
        </figcaption>
      </figure>
    )
  }

  return (
    <figure className="rounded-lg border border-bordure bg-surface p-3">
      {(graphique.variantes.length > 0 || peutEmpiler) && (
        <div className="flex justify-end gap-1 pb-1">
          {graphique.variantes.length > 0 &&
            bascules.map((b) => (
              <button
                key={b}
                onClick={() => setAffiche(b as TypeGraphique['type'])}
                className={`rounded px-2 py-0.5 text-xs transition-colors ${
                  affiche === b
                    ? 'bg-bordure-appuyee text-texte'
                    : 'text-texte-faible hover:text-texte'
                }`}
              >
                {b}
              </button>
            ))}
          {peutEmpiler && (
            <button
              onClick={() => setEmpile(!empile)}
              className={`rounded px-2 py-0.5 text-xs transition-colors ${
                empile
                  ? 'bg-bordure-appuyee text-texte'
                  : 'text-texte-faible hover:text-texte'
              }`}
            >
              empilées
            </button>
          )}
        </div>
      )}
      <ResponsiveContainer width="100%" height={280}>
        {affiche === 'courbe' ? (
          <LineChart data={donnees} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            {communs}
            {donnees.length > SEUIL_ZOOM && (
              <Brush
                dataKey="x"
                height={20}
                stroke={decor.zoom}
                fill="transparent"
                travellerWidth={8}
                tickFormatter={() => ''}
              />
            )}
            {graphique.series.map((s, i) => (
              <Line
                key={s.colonne}
                yAxisId={s.axe_secondaire ? 'droite' : 'gauche'}
                type="monotone"
                dataKey={s.colonne}
                stroke={COULEURS[i % COULEURS.length]}
                strokeWidth={2}
                dot={false}
                // Un trou reste un trou : la courbe s'interrompt au lieu de relier deux
                // points de part et d'autre d'une valeur absente, ce qui inventerait une
                // continuité que les données ne portent pas.
                connectNulls={false}
                // L'animation d'entrée se rejoue à chaque rendu : sur onze séries
                // de plus de cent points, c'est le poste le plus coûteux.
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        ) : (
          <BarChart
            data={donnees}
            margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
            // Un histogramme est un continuum découpé : ses barres se touchent, là où
            // des catégories distinctes gardent leur espacement.
            barCategoryGap={affiche === 'histogramme' ? '2%' : '10%'}
          >
            {communs}
            {graphique.series.map((s, i) => (
              <Bar
                key={s.colonne}
                yAxisId={s.axe_secondaire ? 'droite' : 'gauche'}
                dataKey={s.colonne}
                stackId={empile ? 'pile' : undefined}
                fill={COULEURS[i % COULEURS.length]}
                radius={empile ? undefined : [3, 3, 0, 0]}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
      <figcaption className="mt-1 text-center text-xs text-texte-faible">
        {graphique.type === 'histogramme'
          ? `distribution de ${graphique.x}`
          : `${graphique.series.map((s) => s.colonne).join(' · ')} par ${graphique.x}`}
        {double && ' — deux axes, les échelles ne sont pas comparables'}
      </figcaption>
    </figure>
  )
})

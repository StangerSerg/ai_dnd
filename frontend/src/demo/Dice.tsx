import { useId, type CSSProperties } from 'react'

export type D20Value = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20
export type DiceStyle = 'amber' | 'jade' | 'amethyst' | 'gold' | 'malachite' | 'ruby'

// Mathematically exact face-on orthographic projection of a regular
// icosahedron (generator + checks: .local/stage3-gate/dice-geometry.mjs).
// Vertices (0,±1,±φ),(±1,±φ,0),(±φ,0,±1) are rotated so one face normal points
// at the viewer, spun so that face has a vertex straight up, then projected
// x' = 100 + s·x, y' = 100 − s·y with s = 92/R (R = max projected radius).
// The 10 front-facing faces form a hexagonal silhouette; every facet below
// references this single rounded vertex table, so shared edges use identical
// coordinates — no gaps or overlaps (verified: Σ facet areas = hull area).
const V = {
  apex: [100, 8],
  frontTop: [100, 43.14],
  shoulderLeft: [20.33, 54],
  shoulderRight: [179.67, 54],
  frontLeft: [50.76, 128.43],
  frontRight: [149.24, 128.43],
  hipLeft: [20.33, 146],
  hipRight: [179.67, 146],
  bottom: [100, 192],
} as const

function facet(...keys: (keyof typeof V)[]) {
  return keys.map(key => V[key].join(',')).join(' ')
}

// This is a stylized illustration, not a physical 3D simulation.
export function Dice({ type, value, eventId, appearance }: { type: 'd20'; value: D20Value; eventId: number; appearance: DiceStyle }) {
  const id = `dice-${useId()}`
  const material = appearance === 'gold' || appearance === 'malachite' || appearance === 'ruby'
  const paint = (name: string) => `url(#${id}-${name})`
  const surface = material ? {
    '--dice-light': paint('light'),
    '--dice-mid': paint('mid'),
    '--dice-dark': paint('dark'),
  } as CSSProperties : undefined

  return <div className={`dice-stage dice-${appearance}`}>
    <div className="dice-shadow" aria-hidden="true" />
    <svg key={eventId} className="dice-illustration" style={surface} viewBox="0 0 200 200" aria-hidden="true">
      {material && <defs>
        <clipPath id={`${id}-silhouette`}>
          <polygon points={facet('apex', 'shoulderRight', 'hipRight', 'bottom', 'hipLeft', 'shoulderLeft')} />
        </clipPath>
        {appearance === 'gold' && <>
          <linearGradient id={`${id}-light`} gradientUnits="userSpaceOnUse" x1="39" y1="30" x2="153" y2="145">
            <stop stopColor="#fff4a3" />
            <stop offset=".22" stopColor="#ffe444" />
            <stop offset=".37" stopColor="#ffcf08" />
            <stop offset=".41" stopColor="#fff9ce" />
            <stop offset=".45" stopColor="#fff17a" />
            <stop offset=".52" stopColor="#e4ac00" />
            <stop offset=".63" stopColor="#ffda24" />
            <stop offset="1" stopColor="#b77d00" />
          </linearGradient>
          <linearGradient id={`${id}-mid`} gradientUnits="userSpaceOnUse" x1="171" y1="44" x2="64" y2="173">
            <stop stopColor="#fff39a" />
            <stop offset=".2" stopColor="#edb900" />
            <stop offset=".38" stopColor="#ffe752" />
            <stop offset=".42" stopColor="#fffbd2" />
            <stop offset=".46" stopColor="#ffdf2a" />
            <stop offset=".56" stopColor="#b57b00" />
            <stop offset=".84" stopColor="#f3c818" />
            <stop offset="1" stopColor="#ffe55c" />
          </linearGradient>
          <linearGradient id={`${id}-dark`} gradientUnits="userSpaceOnUse" x1="29" y1="67" x2="116" y2="156">
            <stop stopColor="#6d4900" />
            <stop offset=".32" stopColor="#b58000" />
            <stop offset=".47" stopColor="#ffdc35" />
            <stop offset=".5" stopColor="#fff8b3" />
            <stop offset=".54" stopColor="#b58200" />
            <stop offset="1" stopColor="#714900" />
          </linearGradient>
        </>}
        {appearance === 'malachite' && <>
          <pattern id={`${id}-bands`} patternUnits="userSpaceOnUse" width="200" height="200">
            <rect width="200" height="200" fill="#075139" />
            {/* Uneven, nested mineral bands, not noise or a jade recolour. */}
            <g fill="none" strokeLinecap="round">
              <path stroke="#023323" strokeWidth="10" d="M-20 26C24-8 48 68 99 30S161-3 220 36M-20 60C31 17 49 103 105 61S172 29 220 68M-20 97C22 49 64 141 118 97S175 62 220 106M-20 138C30 87 56 180 124 134S181 105 220 148M-20 184C28 128 77 220 135 177S185 151 220 187" />
              <path stroke="#209664" strokeWidth="4" d="M-20 18C24-16 48 60 99 22S161-11 220 28M-20 50C31 7 49 93 105 51S172 19 220 58M-20 86C22 38 64 130 118 86S175 51 220 95M-20 127C30 76 56 169 124 123S181 94 220 137M-20 173C28 117 77 209 135 166S185 140 220 176" />
              <path stroke="#75c895" strokeOpacity=".65" strokeWidth="1.2" d="M-20 13C24-21 48 55 99 17S161-16 220 23M-20 44C31 1 49 87 105 45S172 13 220 52M-20 80C22 32 64 124 118 80S175 45 220 89M-20 121C30 70 56 163 124 117S181 88 220 131M-20 167C28 111 77 203 135 160S185 134 220 170" />
              <path stroke="#012e20" strokeWidth="3" d="M132 67C132 50 159 43 172 55S160 85 144 81S124 61 138 49M143 63C145 55 161 54 162 63S148 74 143 63" />
              <path stroke="#42af79" strokeWidth="1.5" d="M127 66C126 45 162 37 179 53S164 93 141 86S118 60 133 46" />
            </g>
          </pattern>
          <linearGradient id={`${id}-light`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#b3ffd2" stopOpacity=".2" />
            <stop offset=".42" stopColor="#0b6745" stopOpacity=".08" />
            <stop offset="1" stopColor="#001e16" stopOpacity=".38" />
          </linearGradient>
          <linearGradient id={`${id}-mid`} x1="1" y1="0" x2="0" y2="1">
            <stop stopColor="#39b278" stopOpacity=".15" />
            <stop offset="1" stopColor="#001b12" stopOpacity=".58" />
          </linearGradient>
          <linearGradient id={`${id}-dark`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#001b12" stopOpacity=".66" />
            <stop offset="1" stopColor="#003b29" stopOpacity=".3" />
          </linearGradient>
        </>}
        {appearance === 'ruby' && <>
          <radialGradient id={`${id}-core`} cx=".42" cy=".36" r=".7">
            <stop stopColor="#ff4660" stopOpacity=".06" />
            <stop offset=".55" stopColor="#e9163d" stopOpacity=".22" />
            <stop offset="1" stopColor="#71051c" stopOpacity=".64" />
          </radialGradient>
          <linearGradient id={`${id}-light`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#ff9aab" stopOpacity=".52" />
            <stop offset=".3" stopColor="#f52148" stopOpacity=".32" />
            <stop offset=".7" stopColor="#cb082d" stopOpacity=".24" />
            <stop offset="1" stopColor="#ff3651" stopOpacity=".48" />
          </linearGradient>
          <linearGradient id={`${id}-mid`} x1="1" y1="0" x2="0" y2="1">
            <stop stopColor="#ff647d" stopOpacity=".62" />
            <stop offset=".48" stopColor="#e30c31" stopOpacity=".3" />
            <stop offset="1" stopColor="#950623" stopOpacity=".68" />
          </linearGradient>
          <linearGradient id={`${id}-dark`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#69031b" stopOpacity=".76" />
            <stop offset=".6" stopColor="#ca1239" stopOpacity=".38" />
            <stop offset="1" stopColor="#ff566d" stopOpacity=".52" />
          </linearGradient>
        </>}
      </defs>}
      <g clipPath={material ? paint('silhouette') : undefined}>
        {appearance === 'malachite' && <rect width="200" height="200" fill={paint('bands')} />}
        {appearance === 'ruby' && <g className="dice-ruby-interior">
          <rect width="200" height="200" fill={paint('core')} />
          {/* The rear-facing triangle remains visible through the front facets. */}
          <path className="dice-ruby-rear" d="M50.76 71.57H149.24L100 156.86Z" />
          <path className="dice-ruby-refraction" d="M50.76 71.57L100 156.86L149.24 71.57L100 132Z" />
          <path className="dice-ruby-back-edges" d="M100 8L50.76 71.57L20.33 54M50.76 71.57L20.33 146L100 156.86L179.67 146L149.24 71.57L179.67 54M100 156.86V192" />
        </g>}
        {/* two rim facets above the shoulders (light from above) */}
        <polygon className="dice-facet dice-facet-top" points={facet('apex', 'frontTop', 'shoulderRight')} />
        <polygon className="dice-facet dice-facet-top" points={facet('apex', 'shoulderLeft', 'frontTop')} />
        {/* edge-adjacent facets around the central face */}
        <polygon className="dice-facet dice-facet-left" points={facet('frontTop', 'frontLeft', 'shoulderLeft')} />
        <polygon className="dice-facet dice-facet-right" points={facet('frontTop', 'shoulderRight', 'frontRight')} />
        <polygon className="dice-facet dice-facet-bottom" points={facet('frontLeft', 'frontRight', 'bottom')} />
        {/* outer rim facets */}
        <polygon className="dice-facet dice-facet-left" points={facet('frontLeft', 'shoulderLeft', 'hipLeft')} />
        <polygon className="dice-facet dice-facet-right" points={facet('frontRight', 'hipRight', 'shoulderRight')} />
        <polygon className="dice-facet dice-facet-bottom-left" points={facet('frontLeft', 'hipLeft', 'bottom')} />
        <polygon className="dice-facet dice-facet-bottom-right" points={facet('frontRight', 'bottom', 'hipRight')} />
        {/* central face: centroid is exactly (100, 100) */}
        <polygon className="dice-facet dice-facet-front" points={facet('frontTop', 'frontRight', 'frontLeft')} />
        {appearance === 'gold' && <g className="dice-gold-polish">
          <path d="M99 13L25 55L98 22ZM84 75L97 52L99 55L88 77Z" />
          <polyline points={facet('frontLeft', 'frontTop', 'frontRight')} />
        </g>}
        {appearance === 'malachite' && <g className="dice-malachite-polish">
          <path d="M100 45L54 125L60 113L101 50Z" />
          <polyline points={facet('shoulderLeft', 'apex', 'shoulderRight')} />
        </g>}
        {appearance === 'ruby' && <g className="dice-ruby-polish">
          <path d="M100 12L29 53L48 52L100 22ZM103 48L137 110L132 94ZM151 125L176 70L174 112ZM100 182L132 145L119 153Z" />
          <polyline points={facet('frontLeft', 'frontTop', 'frontRight')} />
        </g>}
      </g>
      <text className="dice-face-value" x="100" y="112" textAnchor="middle">{value}</text>
    </svg>
    <p className="dice-result" aria-live="polite" aria-atomic="true">Результат {type}: <strong>{value}</strong></p>
  </div>
}
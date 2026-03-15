import { motion } from 'framer-motion'

const LABELS = {
  idle: 'NEURAL CORE ONLINE',
  listening: 'RECEIVING INPUT',
  thinking: 'PROCESSING . . .',
  speaking: 'TRANSMITTING',
}

const bars = Array.from({ length: 16 }, (_, i) => {
  const angle = (i / 16) * 2 * Math.PI
  const x = 110 + Math.cos(angle) * 68
  const y = 110 + Math.sin(angle) * 68
  const deg = (i / 16) * 360
  return { x, y, deg, delay: i * 55 }
})

function ringTicks() {
  return Array.from({ length: 8 }, (_, i) => {
    const angle = (i * 45 * Math.PI) / 180
    const sx = 110 + Math.cos(angle) * 96
    const sy = 110 + Math.sin(angle) * 96
    const ex = 110 + Math.cos(angle) * 104
    const ey = 110 + Math.sin(angle) * 104

    return <line key={`tick-${i}`} x1={sx} y1={sy} x2={ex} y2={ey} stroke="var(--border-dim)" strokeWidth="1" />
  })
}

function markers() {
  const points = [
    { x: 110, y: 52 },
    { x: 110, y: 168 },
    { x: 168, y: 110 },
    { x: 52, y: 110 },
  ]

  return points.map((pt, idx) => (
    <polygon
      key={`mk-${idx}`}
      points="0,-5 3,0 0,5 -3,0"
      fill="var(--cyan-dim)"
      transform={`translate(${pt.x},${pt.y})`}
    />
  ))
}

export default function ResonanceCore({ state = 'idle' }) {
  const stateLabel = LABELS[state] || LABELS.idle
  const barActive = state === 'listening' || state === 'speaking'

  return (
    <motion.div
      className="resonance-core"
      animate={
        state === 'listening'
          ? { scale: 1.05, filter: 'brightness(1.3)' }
          : state === 'thinking'
            ? { scale: 1, filter: 'brightness(0.85)' }
            : { scale: 1, filter: 'brightness(1)' }
      }
      transition={{ duration: state === 'thinking' ? 0.3 : 0.4 }}
    >
      <div className="resonance-stage">
        <motion.div
          className="resonance-ambient"
          animate={{ scale: [0.9, 1.1, 0.9] }}
          transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
        />

        <svg viewBox="0 0 220 220" className="resonance-svg ring-outer" aria-hidden>
          <circle cx="110" cy="110" r="100" fill="none" stroke="var(--border-dim)" strokeWidth="1" />
          {ringTicks()}
        </svg>

        <svg viewBox="0 0 220 220" className="resonance-svg ring-mid" aria-hidden>
          <circle
            cx="110"
            cy="110"
            r="78"
            fill="none"
            stroke={state === 'listening' ? 'var(--cyan-full)' : 'var(--cyan-mid)'}
            strokeWidth={state === 'listening' ? '2' : '1.5'}
            strokeDasharray="45 18 45 18 45 18 45 18"
            style={{ transition: 'all 0.4s ease' }}
          />
        </svg>

        <svg viewBox="0 0 220 220" className={`resonance-svg ring-inner ${state === 'thinking' ? 'thinking' : ''}`} aria-hidden>
          <circle cx="110" cy="110" r="58" fill="none" stroke="var(--border-dim)" strokeWidth="1" />
          {markers()}
        </svg>

        <div className="wave-bars" aria-hidden>
          {bars.map((bar, i) => (
            <div
              key={`bar-${i}`}
              className={`wave-bar ${barActive ? 'active' : ''}`}
              style={{
                left: `${(bar.x / 220) * 200}px`,
                top: `${(bar.y / 220) * 200}px`,
                transform: `translate(-50%, -50%) rotate(${bar.deg}deg)`,
                animationDelay: `${bar.delay}ms`,
              }}
            />
          ))}
        </div>

        <svg viewBox="0 0 220 220" className="resonance-svg" aria-hidden>
          <defs>
            <radialGradient id="coreGradient" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="rgba(0,229,255,0.35)" />
              <stop offset="100%" stopColor="rgba(0,229,255,0)" />
            </radialGradient>
          </defs>

          <g className={`core-group ${state === 'listening' ? 'listening' : ''} ${state === 'thinking' ? 'thinking' : ''}`}>
            <circle cx="110" cy="110" r="28" fill="url(#coreGradient)" stroke="var(--cyan-full)" strokeWidth="1.5" />
            <circle
              cx="110"
              cy="110"
              r="6"
              fill="#00E5FF"
              style={{ filter: 'drop-shadow(0 0 6px #00E5FF) drop-shadow(0 0 16px rgba(0,229,255,0.8))' }}
            />
          </g>
        </svg>
      </div>

      <motion.p
        key={stateLabel}
        className="state-label"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.3 }}
      >
        {stateLabel}
      </motion.p>
    </motion.div>
  )
}

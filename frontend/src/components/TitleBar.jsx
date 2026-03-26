import { motion } from 'framer-motion'
import { Minimize2 } from 'lucide-react'
import StatusOrb from './StatusOrb'

function normalizeStatus(status) {
  const raw = String(status || 'ONLINE')
    .replace(/Â·/g, '·')
    .replace(/\s+/g, ' ')
    .trim()

  const parts = raw
    .split(/·|-/)
    .map((part) => part.trim())
    .filter(Boolean)

  if (parts.length >= 2) {
    return {
      activeAgent: parts[0],
      detail: parts.slice(1).join(' · '),
      display: `${parts[0]} · ${parts.slice(1).join(' · ')}`,
    }
  }

  return {
    activeAgent: 'Core',
    detail: raw,
    display: raw,
  }
}

export default function TitleBar({ status, orbState, onMinimize }) {
  const statusMeta = normalizeStatus(status)

  return (
    <header className="titlebar draggable">
      <motion.div
        className="top-edge-line"
        initial={{ width: '0%', opacity: 0 }}
        animate={{ width: '100%', opacity: 1 }}
        transition={{ duration: 0.6 }}
      />

      <div className="titlebar-left">
        <StatusOrb state={orbState} />
        <span className="titlebar-name">J.A.R.V.I.S</span>
        <span className="titlebar-separator" />
        <span className="titlebar-version">v3.0</span>
        <span className="titlebar-separator titlebar-agent-separator" />
        <span className="titlebar-active-agent">{statusMeta.activeAgent}</span>
      </div>

      <div className="titlebar-right non-draggable">
        <span className="status-chip">
          <StatusOrb state={orbState} />
          {statusMeta.display}
        </span>
        <button
          type="button"
          className="minimize-btn"
          onClick={() => {
            if (onMinimize) {
              onMinimize()
              return
            }
            window.pywebview?.api?.resize_window('pill')
          }}
          aria-label="Minimize"
        >
          <Minimize2 size={10} />
        </button>
      </div>
    </header>
  )
}

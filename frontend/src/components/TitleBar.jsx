import { motion } from 'framer-motion'
import { Minimize2 } from 'lucide-react'
import StatusOrb from './StatusOrb'

export default function TitleBar({ status, orbState, onMinimize }) {
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
        <span className="titlebar-version">v2.0</span>
      </div>

      <div className="titlebar-right non-draggable">
        <span className="status-chip">
          <StatusOrb state={orbState} />
          {status}
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

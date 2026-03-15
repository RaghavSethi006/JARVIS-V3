import { motion } from 'framer-motion'
import { useRef } from 'react'
import StatusOrb from './StatusOrb'

export default function PillMode({ isListening, orbState, status }) {
  const clickTimer = useRef(null)

  const handleClick = () => {
    if (clickTimer.current) {
      return
    }

    clickTimer.current = window.setTimeout(() => {
      window.pywebview?.api?.toggle_listening()
      clickTimer.current = null
    }, 250)
  }

  const handleDoubleClick = () => {
    if (clickTimer.current) {
      window.clearTimeout(clickTimer.current)
      clickTimer.current = null
    }

    window.pywebview?.api?.resize_window('dashboard')
  }

  return (
    <motion.section
      className="pill-stage"
      initial={{ opacity: 0, scale: 0.95, filter: 'blur(6px)' }}
      animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
      exit={{ opacity: 0, scale: 0.95, filter: 'blur(6px)' }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
    >
      <div className="pill pill-draggable" onClick={handleClick} onDoubleClick={handleDoubleClick}>
        <StatusOrb state={orbState} />

        <div className="pill-text">
          <p className="pill-title">J.A.R.V.I.S</p>
          <p className="pill-status">{status}</p>
        </div>

        <div className={`pill-wave ${isListening ? 'active' : ''}`}>
          {[0, 80, 160, 240, 320].map((delay) => (
            <span key={delay} className="pill-wave-bar" style={{ animationDelay: `${delay}ms` }} />
          ))}
        </div>
      </div>
    </motion.section>
  )
}

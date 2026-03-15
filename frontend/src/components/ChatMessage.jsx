import { motion } from 'framer-motion'
import Typewriter from 'typewriter-effect'
import { useMemo, useState } from 'react'

function formatTimestamp(dateValue) {
  return new Date(dateValue).toLocaleTimeString('en-US', { hour12: false })
}

export default function ChatMessage({ message }) {
  const [playTypewriter] = useState(message.role === 'jarvis' && message.isNew)
  const roleLabel = useMemo(() => (message.role === 'jarvis' ? 'JARVIS' : 'YOU'), [message.role])

  if (message.role === 'jarvis') {
    return (
      <motion.div
        className="chat-message jarvis"
        initial={{ opacity: 0, x: -10 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.25 }}
      >
        <div className="msg-meta">
          <span className="msg-role">{roleLabel}</span>
          <span className="msg-time">{formatTimestamp(message.timestamp)}</span>
        </div>
        <p className="msg-text">
          {playTypewriter ? (
            <Typewriter
              options={{ delay: 20, cursor: '' }}
              onInit={(tw) => {
                tw.typeString(message.text).start()
              }}
            />
          ) : (
            message.text
          )}
        </p>
      </motion.div>
    )
  }

  return (
    <motion.div
      className="chat-message user"
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2 }}
    >
      <div className="msg-meta">
        <span className="msg-role">{roleLabel}</span>
        <span className="msg-time">{formatTimestamp(message.timestamp)}</span>
      </div>
      <p className="msg-text">{message.text}</p>
    </motion.div>
  )
}

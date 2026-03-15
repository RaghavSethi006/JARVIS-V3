import { motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'

export default function CommandInput({ onUserCommand }) {
  const [text, setText] = useState('')
  const [focused, setFocused] = useState(false)
  const [flash, setFlash] = useState(false)
  const inputRef = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleSubmit = (event) => {
    event.preventDefault()
    const value = text.trim()
    if (!value) {
      return
    }

    window.pywebview?.api?.send_command(value)
    onUserCommand?.(value)
    setFlash(true)
    window.setTimeout(() => setFlash(false), 360)
    setText('')
  }

  return (
    <form className="command-row non-draggable" onSubmit={handleSubmit}>
      <motion.div
        className={`command-input-shell ${focused ? 'focused' : ''}`}
        animate={
          flash
            ? {
                borderColor: ['var(--border-bright)', 'transparent', 'var(--border-mid)'],
              }
            : undefined
        }
        transition={{ duration: 0.35 }}
      >
        <span className="command-prefix">//</span>
        <input
          ref={inputRef}
          className="command-input"
          value={text}
          onChange={(event) => setText(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder="Type a command or ask JARVIS..."
        />
      </motion.div>

      <motion.button className="transmit-btn" type="submit" whileTap={{ scale: 0.95 }}>
        TRANSMIT
      </motion.button>
    </form>
  )
}

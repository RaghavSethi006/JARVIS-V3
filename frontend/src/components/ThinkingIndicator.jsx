import { motion } from 'framer-motion'

export default function ThinkingIndicator() {
  return (
    <div className="thinking">
      <div className="thinking-dots">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="thinking-dot"
            animate={{ y: [0, -6, 0] }}
            transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }}
          />
        ))}
      </div>
    </div>
  )
}

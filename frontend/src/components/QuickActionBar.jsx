import { useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Cloud,
  FolderSearch,
  Globe,
  Hand,
  Music,
  Newspaper,
  Shield,
} from 'lucide-react'

const ACTIONS = [
  { icon: Cloud, label: 'WEATHER', command: 'weather calgary' },
  { icon: Hand, label: 'GESTURE', command: 'gesture' },
  { icon: Newspaper, label: 'NEWS', command: 'news bbc-news' },
  { icon: Music, label: 'MEDIA', command: 'play lofi music' },
  { icon: Globe, label: 'BROWSER', command: 'launch chrome' },
  { icon: FolderSearch, label: 'SCAN', command: null, scan: true },
  { icon: Shield, label: 'AUTH', command: 'login' },
]

export default function QuickActionBar() {
  const canCallAPI = useMemo(() => Boolean(window.pywebview?.api), [])

  const handleAction = (action) => {
    if (!canCallAPI) {
      return
    }

    if (action.scan) {
      window.pywebview?.api?.start_scan()
      return
    }

    if (action.command) {
      window.pywebview?.api?.send_command(action.command)
    }
  }

  return (
    <div className="quick-actions non-draggable">
      {ACTIONS.map((action) => {
        const Icon = action.icon
        return (
          <motion.button
            key={action.label}
            className="quick-chip"
            type="button"
            whileHover={{ scale: 1.04, borderColor: 'var(--border-bright)' }}
            whileTap={{ scale: 0.95 }}
            onClick={() => handleAction(action)}
          >
            <Icon size={12} />
            {action.label}
          </motion.button>
        )
      })}
    </div>
  )
}

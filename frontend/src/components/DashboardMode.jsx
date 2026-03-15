import { motion } from 'framer-motion'
import AvatarPanel from './AvatarPanel'
import ChatArea from './ChatArea'
import CommandInput from './CommandInput'
import QuickActionBar from './QuickActionBar'
import TitleBar from './TitleBar'

export default function DashboardMode({ messages, coreState, status, onSwitchToPill, onUserCommand, orbState }) {
  return (
    <motion.section
      className="dashboard-window"
      initial={{ opacity: 0, scale: 0.95, filter: 'blur(6px)' }}
      animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
      exit={{ opacity: 0, scale: 0.95, filter: 'blur(6px)' }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
    >
      <TitleBar status={status} orbState={orbState} onMinimize={onSwitchToPill} />

      <main className="dashboard-main">
        <motion.div
          initial={{ x: -16, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ duration: 0.4, delay: 0.2 }}
        >
          <AvatarPanel coreState={coreState} />
        </motion.div>

        <div className="column-divider" />

        <motion.div
          className="right-panel"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.4 }}
        >
          <ChatArea messages={messages} thinking={coreState === 'thinking'} />
          <QuickActionBar />
          <CommandInput onUserCommand={onUserCommand} />
        </motion.div>
      </main>
    </motion.section>
  )
}

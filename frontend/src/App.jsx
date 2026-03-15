import { useEffect, useRef } from 'react'
import DashboardMode from './components/DashboardMode'
import PillMode from './components/PillMode'
import useJarvisBridge from './hooks/useJarvisBridge'
import useJarvisState from './hooks/useJarvisState'

const BOOT_MESSAGE = `Good evening. J.A.R.V.I.S. online.
All subsystems nominal. Running on Stark Industries Neural Core v2.0.
How can I assist you today, sir?`

function deriveOrbState(status, coreState, isListening) {
  if (String(status).toLowerCase().includes('offline')) {
    return 'offline'
  }

  if (coreState === 'thinking') {
    return 'thinking'
  }

  if (isListening) {
    return 'listening'
  }

  return 'online'
}

export default function App() {
  const bootedRef = useRef(false)
  const {
    messages,
    addMessage,
    addJarvisMessage,
    coreState,
    setCoreState,
    status,
    setStatus,
    isListening,
    setListening,
    mode,
    setMode,
  } = useJarvisState()

  const params = new URLSearchParams(window.location.search)
  const initialMode = params.get('mode') || 'dashboard'

  useJarvisBridge({ addMessage, setCoreState, setStatus, setListening, setMode })

  useEffect(() => {
    setMode(initialMode)
  }, [initialMode, setMode])

  useEffect(() => {
    if (bootedRef.current) {
      return
    }

    bootedRef.current = true
    addJarvisMessage(BOOT_MESSAGE)
  }, [addJarvisMessage])

  const orbState = deriveOrbState(status, coreState, isListening)

  const handleUserCommand = (text) => {
    addMessage('user', text)
    setCoreState('thinking')
  }

  if (mode === 'pill') {
    return <PillMode isListening={isListening} coreState={coreState} orbState={orbState} status={status} />
  }

  return (
    <DashboardMode
      messages={messages}
      coreState={coreState}
      status={status}
      isListening={isListening}
      orbState={orbState}
      onSwitchToPill={() => window.pywebview?.api?.resize_window('pill')}
      onUserCommand={handleUserCommand}
    />
  )
}

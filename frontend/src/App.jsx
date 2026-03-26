import { useEffect, useRef } from 'react'
import DashboardMode from './components/DashboardMode'
import PillMode from './components/PillMode'
import useJarvisBridge from './hooks/useJarvisBridge'
import useJarvisState from './hooks/useJarvisState'

const BOOT_MESSAGE = `Good evening. J.A.R.V.I.S. online.
All subsystems nominal. Running on Stark Industries Neural Core v3.0.
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
    entities,
    setEntities,
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

  useEffect(() => {
    let cancelled = false

    const syncEntities = async () => {
      try {
        const nextEntities = await window.pywebview?.api?.get_entity_panel?.()
        if (!cancelled && Array.isArray(nextEntities)) {
          setEntities(nextEntities)
        }
      } catch (error) {
        console.debug('Entity panel refresh failed', error)
      }
    }

    syncEntities()
    const intervalId = window.setInterval(syncEntities, 10000)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [setEntities])

  const orbState = deriveOrbState(status, coreState, isListening)

  const handleUserCommand = (text) => {
    addMessage('user', text)
    setCoreState('thinking')
  }

  const handleEntityAsk = (entity) => {
    const entityName = entity?.name || entity?.canonicalName
    if (!entityName) {
      return
    }
    const prompt = `What do you know about ${entityName}?`
    window.pywebview?.api?.send_command?.(prompt)
    handleUserCommand(prompt)
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
      entities={entities}
      orbState={orbState}
      onSwitchToPill={() => window.pywebview?.api?.resize_window('pill')}
      onUserCommand={handleUserCommand}
      onAskEntity={handleEntityAsk}
    />
  )
}

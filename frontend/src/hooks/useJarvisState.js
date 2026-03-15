import { useState } from 'react'
import useChatHistory from './useChatHistory'

export default function useJarvisState() {
  const { messages, addMessage } = useChatHistory()
  const [coreState, setCoreState] = useState('idle')
  const [status, setStatus] = useState('ONLINE')
  const [isListening, setListening] = useState(false)
  const [mode, setMode] = useState('dashboard')

  const addJarvisMessage = (text) => addMessage('jarvis', text)

  const toggleListening = () => {
    setListening((prev) => {
      const next = !prev
      setCoreState(next ? 'listening' : 'idle')
      return next
    })
  }

  const switchMode = (nextMode) => setMode(nextMode)

  return {
    messages,
    addMessage,
    addJarvisMessage,
    coreState,
    setCoreState,
    status,
    setStatus,
    isListening,
    setListening,
    toggleListening,
    mode,
    setMode,
    switchMode,
  }
}

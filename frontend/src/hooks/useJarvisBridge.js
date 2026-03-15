import { useEffect } from 'react'

export default function useJarvisBridge({ addMessage, setCoreState, setStatus, setListening, setMode }) {
  useEffect(() => {
    const addJarvisResponse = (text) => {
      addMessage('jarvis', text)
      setCoreState('idle')
    }

    const setCoreStateImpl = (state) => {
      setCoreState(state)
    }

    window.addJarvisResponse = addJarvisResponse
    window.setCoreState = setCoreStateImpl
    if (typeof window.__flushStubs === 'function') {
      window.__flushStubs()
    }

    window.addUserMessage = (text) => {
      addMessage('user', text)
    }

    window.setStatus = (status) => {
      setStatus(status)
    }

    window.setListening = (listening) => {
      const next = Boolean(listening)
      setListening(next)
      setCoreState(next ? 'listening' : 'idle')
    }

    window.setMode = (newMode) => {
      if (setMode) {
        setMode(newMode)
      }
    }

    return () => {
      window.addJarvisResponse = undefined
      window.addUserMessage = undefined
      window.setStatus = undefined
      window.setListening = undefined
      window.setCoreState = undefined
      window.setMode = undefined
    }
  }, [addMessage, setCoreState, setListening, setStatus, setMode])
}

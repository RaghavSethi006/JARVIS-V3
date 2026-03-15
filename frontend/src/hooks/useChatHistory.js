import { useState, useCallback } from 'react'

function makeMessage(role, text) {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    text,
    timestamp: new Date(),
    isNew: true,
  }
}

export default function useChatHistory() {
  const [messages, setMessages] = useState([])

  const addMessage = useCallback((role, text) => {
    const message = makeMessage(role, text)
    setMessages((prev) => [...prev, message])

    window.setTimeout(() => {
      setMessages((prev) =>
        prev.map((item) => (item.id === message.id ? { ...item, isNew: false } : item)),
      )
    }, 100)
  }, [])

  return {
    messages,
    addMessage,
  }
}

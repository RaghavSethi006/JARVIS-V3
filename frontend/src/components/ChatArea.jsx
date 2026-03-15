import { useEffect, useRef } from 'react'
import ChatMessage from './ChatMessage'
import ThinkingIndicator from './ThinkingIndicator'

export default function ChatArea({ messages, thinking }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  return (
    <section className="chat-shell">
      <div className="section-header">
        <span className="section-title">CONVERSATION LOG</span>
        <div className="section-line" />
      </div>

      <div className="chat-scroll">
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} />
        ))}
        {thinking ? <ThinkingIndicator /> : null}
        <div ref={endRef} />
      </div>
    </section>
  )
}

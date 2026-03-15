export default function StatusOrb({ state = 'online' }) {
  const className = ['status-orb', state].join(' ')
  return <span className={className} />
}

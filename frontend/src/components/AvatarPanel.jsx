import { useEffect, useState } from 'react'
import ResonanceCore from './ResonanceCore'

export default function AvatarPanel({ coreState }) {
  const [stats, setStats] = useState({ cpu: 0, mem: 0, net: 0 })

  useEffect(() => {
    window.updateSystemStats = (data) => setStats(data || { cpu: 0, mem: 0, net: 0 })
    return () => {
      window.updateSystemStats = null
    }
  }, [])

  const readouts = [
    { key: 'CPU', value: stats.cpu },
    { key: 'MEM', value: stats.mem },
    { key: 'NET', value: stats.net },
  ]

  return (
    <aside className="avatar-panel corner-panel">
      <div className="resonance-wrap" style={{ width: '100%', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <ResonanceCore state={coreState} />
      </div>

      <div className="readouts">
        {readouts.map((row) => (
          <div className="readout-row" key={row.key}>
            <span className="readout-label">{row.key}</span>
            <div className="readout-track">
              <div className="readout-fill" style={{ width: `${row.value}%` }} />
            </div>
            <span className="readout-value">{Math.round(row.value)}%</span>
          </div>
        ))}
      </div>

      <div className="stark-wordmark">STARK INDUSTRIES</div>
    </aside>
  )
}

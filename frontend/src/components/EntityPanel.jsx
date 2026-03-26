import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Sparkles } from 'lucide-react'
import { useState } from 'react'

function formatEntityType(type) {
  if (!type) {
    return 'ENTITY'
  }
  return String(type).replace(/_/g, ' ').toUpperCase()
}

export default function EntityPanel({ entities, onAskEntity }) {
  const [collapsed, setCollapsed] = useState(false)
  const visibleEntities = Array.isArray(entities) ? entities.slice(0, 6) : []

  return (
    <motion.aside
      className={`entity-panel-shell ${collapsed ? 'collapsed' : ''}`}
      animate={{ width: collapsed ? 52 : 244 }}
      transition={{ type: 'spring', stiffness: 220, damping: 24 }}
    >
      <div className="entity-panel-header">
        <button
          type="button"
          className="entity-panel-toggle"
          onClick={() => setCollapsed((current) => !current)}
          aria-label={collapsed ? 'Expand entity panel' : 'Collapse entity panel'}
        >
          {collapsed ? <ChevronLeft size={14} /> : <ChevronRight size={14} />}
        </button>

        {!collapsed ? (
          <>
            <div className="section-header entity-section-header">
              <span className="section-title">ENTITY MEMORY</span>
              <div className="section-line" />
            </div>
            <span className="entity-panel-count">{visibleEntities.length}</span>
          </>
        ) : null}
      </div>

      {!collapsed ? (
        <div className="entity-panel-scroll">
          {visibleEntities.length ? (
            visibleEntities.map((entity, index) => (
              <motion.button
                key={entity.id || `${entity.name}-${index}`}
                type="button"
                className="entity-card"
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.22, delay: index * 0.04 }}
                onClick={() => onAskEntity?.(entity)}
              >
                <div className="entity-card-top">
                  <span className="entity-type-badge">{formatEntityType(entity.type)}</span>
                  <Sparkles size={12} />
                </div>
                <h3 className="entity-name">{entity.name || entity.canonicalName}</h3>
                <ul className="entity-facts">
                  {(entity.facts || []).slice(0, 3).map((fact) => (
                    <li key={`${entity.id}-${fact}`}>{fact}</li>
                  ))}
                </ul>
                {!(entity.facts || []).length ? (
                  <p className="entity-empty">Select to ask JARVIS what it remembers.</p>
                ) : null}
              </motion.button>
            ))
          ) : (
            <div className="entity-empty-state">
              <Sparkles size={14} />
              <p>No named people, projects, or topics have been captured yet.</p>
            </div>
          )}
        </div>
      ) : (
        <div className="entity-panel-collapsed-markers">
          {visibleEntities.slice(0, 4).map((entity, index) => (
            <button
              key={entity.id || `${entity.name}-${index}`}
              type="button"
              className="entity-dot"
              title={entity.name || entity.canonicalName}
              onClick={() => onAskEntity?.(entity)}
            >
              {(entity.name || entity.canonicalName || '?').slice(0, 1).toUpperCase()}
            </button>
          ))}
        </div>
      )}
    </motion.aside>
  )
}

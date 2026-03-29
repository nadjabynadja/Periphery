// ============================================
// TemporalTimeline — Recharts timeline visualization
// ============================================

import React, { useMemo } from 'react'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import { useStore } from '../../store'

interface TimePoint {
  date: string
  entities: number
  relationships: number
  anomalies: number
}

export const TemporalTimeline: React.FC = () => {
  const entities = useStore((s) => s.entities)
  const snapshot = useStore((s) => s.snapshot)

  // Build timeline data from entity first_seen dates
  const timelineData = useMemo(() => {
    const buckets = new Map<string, { entities: number; relationships: number; anomalies: number }>()

    for (const entity of entities) {
      const date = entity.first_seen?.slice(0, 10) || ''
      if (!date) continue
      if (!buckets.has(date)) buckets.set(date, { entities: 0, relationships: 0, anomalies: 0 })
      buckets.get(date)!.entities++
    }

    // Add relationship timeline
    const rels = snapshot?.relationships || []
    for (const rel of rels) {
      const date = rel.first_seen?.slice(0, 10) || ''
      if (!date) continue
      if (!buckets.has(date)) buckets.set(date, { entities: 0, relationships: 0, anomalies: 0 })
      buckets.get(date)!.relationships++
    }

    // Add anomalies
    for (const anomaly of (snapshot?.anomalies || [])) {
      const date = anomaly.detected_at?.slice(0, 10) || ''
      if (!date) continue
      if (!buckets.has(date)) buckets.set(date, { entities: 0, relationships: 0, anomalies: 0 })
      buckets.get(date)!.anomalies++
    }

    return Array.from(buckets.entries())
      .map(([date, counts]) => ({ date, ...counts }))
      .sort((a, b) => a.date.localeCompare(b.date))
  }, [entities, snapshot])

  if (timelineData.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center grid-texture">
        <div className="text-center">
          <p className="data-readout text-text-dim">NO TEMPORAL DATA</p>
          <p className="text-xxs text-text-dim mt-1">Entities need first_seen dates</p>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full h-full p-2 grid-texture">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={timelineData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="gradEntities" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#00D4FF" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#00D4FF" stopOpacity={0.02} />
            </linearGradient>
            <linearGradient id="gradRelationships" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#FFB833" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#FFB833" stopOpacity={0.02} />
            </linearGradient>
            <linearGradient id="gradAnomalies" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#FF4444" stopOpacity={0.4} />
              <stop offset="95%" stopColor="#FF4444" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e294033" />
          <XAxis
            dataKey="date"
            tick={{ fill: '#4a5568', fontSize: 9, fontFamily: 'var(--font-mono)' }}
            axisLine={{ stroke: '#1e2940' }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: '#4a5568', fontSize: 9, fontFamily: 'var(--font-mono)' }}
            axisLine={{ stroke: '#1e2940' }}
            tickLine={false}
            width={30}
          />
          <Tooltip
            contentStyle={{
              background: '#111827',
              border: '1px solid #1e2940',
              borderRadius: '2px',
              fontSize: '10px',
              fontFamily: 'var(--font-mono)',
              color: '#c8cdd5',
            }}
          />
          <Area
            type="monotone"
            dataKey="entities"
            stroke="#00D4FF"
            fill="url(#gradEntities)"
            strokeWidth={1.5}
          />
          <Area
            type="monotone"
            dataKey="relationships"
            stroke="#FFB833"
            fill="url(#gradRelationships)"
            strokeWidth={1}
          />
          <Area
            type="monotone"
            dataKey="anomalies"
            stroke="#FF4444"
            fill="url(#gradAnomalies)"
            strokeWidth={1}
          />
        </AreaChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="absolute bottom-2 left-2 flex gap-3 data-readout">
        <span className="flex items-center gap-1">
          <span className="w-2 h-0.5 bg-accent-cyan inline-block" /> Entities
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-0.5 bg-accent-amber inline-block" /> Relationships
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-0.5 bg-accent-red inline-block" /> Anomalies
        </span>
      </div>
    </div>
  )
}

export default TemporalTimeline

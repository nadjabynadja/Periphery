// ============================================
// Sparkline — tiny inline chart
// ============================================

import React, { useMemo } from 'react'

interface Props {
  data: number[]
  width?: number
  height?: number
  color?: string
  className?: string
}

export const Sparkline: React.FC<Props> = ({
  data,
  width = 60,
  height = 16,
  color = '#00D4FF',
  className = '',
}) => {
  const path = useMemo(() => {
    if (data.length < 2) return ''
    const max = Math.max(...data, 1)
    const min = Math.min(...data, 0)
    const range = max - min || 1
    const stepX = width / (data.length - 1)

    return data
      .map((v, i) => {
        const x = i * stepX
        const y = height - ((v - min) / range) * (height - 2) - 1
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
  }, [data, width, height])

  if (data.length < 2) return null

  return (
    <svg
      width={width}
      height={height}
      className={`inline-block ${className}`}
      viewBox={`0 0 ${width} ${height}`}
    >
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.8}
      />
    </svg>
  )
}

export default Sparkline

import { useMemo } from 'react'

interface Props {
  data: number[]
  width?: number
  height?: number
  color?: string
  showDot?: boolean
}

export function Sparkline({ data, width = 120, height = 24, color = '#00D4FF', showDot = true }: Props) {
  const path = useMemo(() => {
    if (data.length < 2) return ''
    const max = Math.max(...data, 1)
    const min = Math.min(...data, 0)
    const range = max - min || 1
    return data.map((v, i) => {
      const x = (i / (data.length - 1)) * width
      const y = height - ((v - min) / range) * height
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
  }, [data, width, height])

  const lastY = useMemo(() => {
    if (data.length < 1) return height / 2
    const max = Math.max(...data, 1)
    const min = Math.min(...data, 0)
    const range = max - min || 1
    return height - ((data[data.length - 1] - min) / range) * height
  }, [data, height])

  if (data.length < 2) return null

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" opacity="0.8" />
      {showDot && (
        <circle cx={width} cy={lastY} r="2" fill={color} />
      )}
    </svg>
  )
}

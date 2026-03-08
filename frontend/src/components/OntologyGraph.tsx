import { useRef, useEffect, useCallback, useState } from 'react'
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force'
import type { OntologySnapshot, GraphNode, CriticScore } from '../api'

interface SimNode extends SimulationNodeDatum {
  id: string
  label: string
  clusterId: number | null
  nodeType: string
  coherenceScore: number | null
  radius: number
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  weight: number
}

interface Props {
  data: OntologySnapshot | null
  criticScores: CriticScore[] | null
  onNodeSelect: (node: GraphNode | null) => void
  selectedNodeId: string | null
}

const CLUSTER_COLORS = [
  '#00d4ff', '#0ea5e9', '#06b6d4', '#14b8a6', '#10b981',
  '#d4a000', '#f59e0b', '#8b5cf6', '#a855f7', '#ec4899',
]

function getClusterColor(clusterId: number | null, colorMap: Record<number, string>): string {
  if (clusterId === null || clusterId === -1) return '#4a5568'
  return colorMap[clusterId] || '#4a5568'
}

export function OntologyGraph({ data, criticScores, onNodeSelect, selectedNodeId }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const simRef = useRef<ReturnType<typeof forceSimulation<SimNode>> | null>(null)
  const nodesRef = useRef<SimNode[]>([])
  const linksRef = useRef<SimLink[]>([])
  const frameRef = useRef<number>(0)
  const [hoveredNode, setHoveredNode] = useState<SimNode | null>(null)
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 })
  const sizeRef = useRef({ width: 0, height: 0 })

  // Build cluster color map
  const clusterColorMap = useCallback(() => {
    if (!data) return {}
    const map: Record<number, string> = {}
    const ids = [...new Set(data.nodes.map(n => n.cluster_id).filter(c => c !== null && c !== -1))] as number[]
    ids.forEach((id, i) => { map[id] = CLUSTER_COLORS[i % CLUSTER_COLORS.length] })
    return map
  }, [data])

  // Get coherence score for a cluster
  const getCoherence = useCallback((clusterId: number | null): number => {
    if (clusterId === null || clusterId === -1 || !criticScores) return 0.5
    const score = criticScores.find(s => s.cluster_id === clusterId)
    return score ? score.coherence_score : 0.5
  }, [criticScores])

  // Set up simulation when data changes
  useEffect(() => {
    if (!data || !canvasRef.current || !containerRef.current) return

    const rect = containerRef.current.getBoundingClientRect()
    const width = rect.width
    const height = rect.height
    sizeRef.current = { width, height }

    const colorMap = clusterColorMap()

    const nodes: SimNode[] = data.nodes.map(n => ({
      id: n.id,
      label: n.label,
      clusterId: n.cluster_id,
      nodeType: n.node_type,
      coherenceScore: n.coherence_score,
      radius: n.node_type === 'cluster' ? 12 : 5,
      x: width / 2 + (Math.random() - 0.5) * width * 0.6,
      y: height / 2 + (Math.random() - 0.5) * height * 0.6,
    }))

    const nodeMap = new Map(nodes.map(n => [n.id, n]))

    const links: SimLink[] = data.edges
      .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map(e => ({
        source: e.source,
        target: e.target,
        weight: e.weight,
      }))

    nodesRef.current = nodes
    linksRef.current = links

    // Stop previous simulation
    if (simRef.current) simRef.current.stop()

    const sim = forceSimulation<SimNode>(nodes)
      .force('link', forceLink<SimNode, SimLink>(links).id(d => d.id).distance(60).strength(l => l.weight * 0.5))
      .force('charge', forceManyBody().strength(-80))
      .force('center', forceCenter(width / 2, height / 2))
      .force('collide', forceCollide<SimNode>(d => d.radius + 4))
      .alphaDecay(0.02)

    simRef.current = sim

    // Render loop
    const canvas = canvasRef.current
    canvas.width = width * 2
    canvas.height = height * 2
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`
    const ctx = canvas.getContext('2d')!
    ctx.scale(2, 2)

    let time = 0

    const render = () => {
      time += 0.016
      ctx.clearRect(0, 0, width, height)

      // Background grid texture
      ctx.strokeStyle = '#1e294010'
      ctx.lineWidth = 0.5
      for (let x = 0; x < width; x += 20) {
        ctx.beginPath()
        ctx.moveTo(x, 0)
        ctx.lineTo(x, height)
        ctx.stroke()
      }
      for (let y = 0; y < height; y += 20) {
        ctx.beginPath()
        ctx.moveTo(0, y)
        ctx.lineTo(width, y)
        ctx.stroke()
      }

      // Draw edges
      for (const link of linksRef.current) {
        const source = link.source as SimNode
        const target = link.target as SimNode
        if (!source.x || !source.y || !target.x || !target.y) continue

        const coherence = Math.max(
          getCoherence(source.clusterId),
          getCoherence(target.clusterId)
        )

        ctx.beginPath()
        ctx.moveTo(source.x, source.y)
        ctx.lineTo(target.x, target.y)
        ctx.strokeStyle = `rgba(0, 212, 255, ${0.06 + coherence * 0.12})`
        ctx.lineWidth = 0.5 + link.weight * 1.5
        ctx.stroke()

        // Directional flow animation (small dot moving along edge)
        const t = ((time * 0.5 + link.weight) % 1)
        const fx = source.x + (target.x - source.x) * t
        const fy = source.y + (target.y - source.y) * t
        ctx.beginPath()
        ctx.arc(fx, fy, 1, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(0, 212, 255, ${0.15 + coherence * 0.25})`
        ctx.fill()
      }

      // Draw nodes
      for (const node of nodesRef.current) {
        if (node.x === undefined || node.y === undefined) continue

        const coherence = node.coherenceScore ?? getCoherence(node.clusterId)
        const color = getClusterColor(node.clusterId, colorMap)
        const isSelected = node.id === selectedNodeId
        const isHovered = hoveredNode?.id === node.id
        const isCluster = node.nodeType === 'cluster'

        // Legibility gradient: high confidence = solid, low = diffuse/pulsing
        if (coherence < 0.4) {
          // Low confidence: diffuse haze, particle-like
          const pulsePhase = Math.sin(time * 2 + node.x! * 0.1) * 0.5 + 0.5
          const alpha = 0.15 + pulsePhase * 0.25

          // Outer haze
          const gradient = ctx.createRadialGradient(
            node.x, node.y, 0,
            node.x, node.y, node.radius * 3
          )
          gradient.addColorStop(0, color + Math.floor(alpha * 80).toString(16).padStart(2, '0'))
          gradient.addColorStop(0.5, color + Math.floor(alpha * 30).toString(16).padStart(2, '0'))
          gradient.addColorStop(1, 'transparent')
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius * 3, 0, Math.PI * 2)
          ctx.fillStyle = gradient
          ctx.fill()

          // Scattered particles
          for (let i = 0; i < 4; i++) {
            const angle = (time * 0.5 + i * Math.PI * 0.5) + node.y! * 0.01
            const dist = node.radius * (1.2 + Math.sin(time + i) * 0.8)
            const px = node.x + Math.cos(angle) * dist
            const py = node.y + Math.sin(angle) * dist
            ctx.beginPath()
            ctx.arc(px, py, 1, 0, Math.PI * 2)
            ctx.fillStyle = color + '44'
            ctx.fill()
          }

          // Core dot (dim)
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius * 0.6, 0, Math.PI * 2)
          ctx.fillStyle = color + '55'
          ctx.fill()
        } else if (coherence < 0.7) {
          // Medium confidence: semi-solid with subtle glow
          const glow = ctx.createRadialGradient(
            node.x, node.y, 0,
            node.x, node.y, node.radius * 2
          )
          glow.addColorStop(0, color + '66')
          glow.addColorStop(1, 'transparent')
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius * 2, 0, Math.PI * 2)
          ctx.fillStyle = glow
          ctx.fill()

          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2)
          ctx.fillStyle = color + 'aa'
          ctx.fill()
        } else {
          // High confidence: solid, well-defined, glowing
          // Glow
          const glow = ctx.createRadialGradient(
            node.x, node.y, node.radius * 0.5,
            node.x, node.y, node.radius * (isCluster ? 2.5 : 2)
          )
          glow.addColorStop(0, color + '44')
          glow.addColorStop(1, 'transparent')
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius * (isCluster ? 2.5 : 2), 0, Math.PI * 2)
          ctx.fillStyle = glow
          ctx.fill()

          // Solid core
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2)
          ctx.fillStyle = color
          ctx.fill()

          // Bright center
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius * 0.4, 0, Math.PI * 2)
          ctx.fillStyle = '#ffffff44'
          ctx.fill()
        }

        // Selection ring
        if (isSelected) {
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius + 4, 0, Math.PI * 2)
          ctx.strokeStyle = '#00d4ff'
          ctx.lineWidth = 1.5
          ctx.stroke()

          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius + 8, 0, Math.PI * 2)
          ctx.strokeStyle = '#00d4ff33'
          ctx.lineWidth = 1
          ctx.stroke()
        }

        // Hover ring
        if (isHovered && !isSelected) {
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius + 3, 0, Math.PI * 2)
          ctx.strokeStyle = '#00d4ff88'
          ctx.lineWidth = 1
          ctx.stroke()
        }

        // Label for cluster nodes
        if (isCluster && node.label) {
          ctx.font = '500 9px "Barlow", sans-serif'
          ctx.fillStyle = '#c8cdd5'
          ctx.textAlign = 'center'
          ctx.fillText(
            node.label.length > 20 ? node.label.substring(0, 20) + '...' : node.label,
            node.x,
            node.y + node.radius + 12
          )
        }
      }

      frameRef.current = requestAnimationFrame(render)
    }

    sim.on('tick', () => {})
    frameRef.current = requestAnimationFrame(render)

    return () => {
      sim.stop()
      cancelAnimationFrame(frameRef.current)
    }
  }, [data, criticScores, selectedNodeId, hoveredNode, clusterColorMap, getCoherence])

  // Handle resize
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        sizeRef.current = { width, height }
        if (canvasRef.current) {
          canvasRef.current.width = width * 2
          canvasRef.current.height = height * 2
          canvasRef.current.style.width = `${width}px`
          canvasRef.current.style.height = `${height}px`
          const ctx = canvasRef.current.getContext('2d')
          if (ctx) ctx.scale(2, 2)
        }
        if (simRef.current) {
          simRef.current
            .force('center', forceCenter(width / 2, height / 2))
            .alpha(0.1)
            .restart()
        }
      }
    })

    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  // Mouse interaction
  const findNodeAt = useCallback((x: number, y: number): SimNode | null => {
    for (let i = nodesRef.current.length - 1; i >= 0; i--) {
      const node = nodesRef.current[i]
      if (node.x === undefined || node.y === undefined) continue
      const dx = x - node.x
      const dy = y - node.y
      const hitRadius = node.radius + 6
      if (dx * dx + dy * dy < hitRadius * hitRadius) return node
    }
    return null
  }, [])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    setMousePos({ x: e.clientX, y: e.clientY })
    const node = findNodeAt(x, y)
    setHoveredNode(node)
    if (canvasRef.current) {
      canvasRef.current.style.cursor = node ? 'pointer' : 'default'
    }
  }, [findNodeAt])

  const handleClick = useCallback((e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    const node = findNodeAt(x, y)
    if (node) {
      const graphNode: GraphNode = {
        id: node.id,
        label: node.label,
        cluster_id: node.clusterId,
        coherence_score: node.coherenceScore,
        node_type: node.nodeType,
      }
      onNodeSelect(graphNode)
    } else {
      onNodeSelect(null)
    }
  }, [findNodeAt, onNodeSelect])

  // Empty state
  if (!data || data.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="data-readout mb-3">No ontology data available</div>
          <div className="text-xxs text-text-dim">Ingest data and run crystallization to populate the graph</div>
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="absolute inset-0">
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onClick={handleClick}
        className="block w-full h-full"
      />

      {/* Hover tooltip */}
      {hoveredNode && (
        <div
          className="fixed z-50 pointer-events-none"
          style={{
            left: mousePos.x + 12,
            top: mousePos.y - 8,
          }}
        >
          <div className="bg-base-800 border border-surface-border px-2 py-1.5" style={{ borderRadius: '2px', maxWidth: '240px' }}>
            <div className="font-mono text-xxs text-accent-cyan truncate">
              {hoveredNode.label || hoveredNode.id}
            </div>
            <div className="flex gap-3 mt-1">
              <span className="text-xxs text-text-dim">
                {hoveredNode.nodeType === 'cluster' ? 'CLUSTER' : 'DOCUMENT'}
              </span>
              {hoveredNode.coherenceScore !== null && (
                <span className={`text-xxs font-mono ${
                  hoveredNode.coherenceScore > 0.7 ? 'text-accent-cyan' :
                  hoveredNode.coherenceScore > 0.4 ? 'text-accent-amber' : 'text-accent-red'
                }`}>
                  {(hoveredNode.coherenceScore * 100).toFixed(0)}% coherence
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="absolute bottom-2 left-2 flex gap-4 items-center">
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-accent-cyan" style={{ boxShadow: '0 0 4px #00d4ff' }} />
          <span className="text-xxs text-text-dim">High confidence</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-accent-amber opacity-60" />
          <span className="text-xxs text-text-dim">Medium</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-text-dim opacity-40" style={{ filter: 'blur(1px)' }} />
          <span className="text-xxs text-text-dim">Emerging</span>
        </div>
      </div>
    </div>
  )
}

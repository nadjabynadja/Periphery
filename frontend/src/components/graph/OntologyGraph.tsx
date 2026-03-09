// ============================================
// OntologyGraph — D3 Force-Directed Canvas Graph
// Hero component for Periphery intelligence dashboard
// ============================================

import React, { useRef, useEffect, useCallback, useState } from 'react'
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force'
import { zoom as d3Zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from 'd3-zoom'
import { select } from 'd3-selection'
import { polygonHull, polygonCentroid } from 'd3-polygon'
import { line as d3Line, curveCatmullRomClosed } from 'd3-shape'
import { useStore } from '../../store'
import type {
  EntityNode,
  Relationship,
  DetectedCluster,
  EmergingStructure,
  LegibilityTier,
} from '../../api/types'

// ---- Color Constants ----
const BG_COLOR = '#0a0e17'
const CYAN_ACCENT = '#00D4FF'
const AMBER_ACCENT = '#FFB833'
const GRID_COLOR = '#1e294010'

// ---- Legibility Tier Definitions ----
interface TierConfig {
  label: string
  minConfidence: number
  opacity: number
  blur: number
  glowColor: string
  glowIntensity: number
  borderStyle: 'solid' | 'dashed' | 'none'
  labelVisibility: 'full' | 'on_hover' | 'on_click_only'
  pulseAnimation: boolean
  pulseSpeed: number // seconds per cycle
  sizeMultiplier: number
}

const TIER_CONFIGS: Record<LegibilityTier, TierConfig> = {
  solid: {
    label: 'Solid',
    minConfidence: 0.8,
    opacity: 1.0,
    blur: 0,
    glowColor: CYAN_ACCENT,
    glowIntensity: 20,
    borderStyle: 'solid',
    labelVisibility: 'full',
    pulseAnimation: false,
    pulseSpeed: 0,
    sizeMultiplier: 1.2,
  },
  defined: {
    label: 'Defined',
    minConfidence: 0.6,
    opacity: 0.85,
    blur: 0,
    glowColor: '#00A8CC',
    glowIntensity: 12,
    borderStyle: 'solid',
    labelVisibility: 'full',
    pulseAnimation: false,
    pulseSpeed: 0,
    sizeMultiplier: 1.0,
  },
  emerging: {
    label: 'Emerging',
    minConfidence: 0.4,
    opacity: 0.6,
    blur: 1.5,
    glowColor: AMBER_ACCENT,
    glowIntensity: 8,
    borderStyle: 'dashed',
    labelVisibility: 'on_hover',
    pulseAnimation: true,
    pulseSpeed: 3,
    sizeMultiplier: 0.9,
  },
  haze: {
    label: 'Haze',
    minConfidence: 0.2,
    opacity: 0.35,
    blur: 3,
    glowColor: '#3A4A5C',
    glowIntensity: 4,
    borderStyle: 'none',
    labelVisibility: 'on_hover',
    pulseAnimation: true,
    pulseSpeed: 5,
    sizeMultiplier: 0.7,
  },
  whisper: {
    label: 'Whisper',
    minConfidence: 0,
    opacity: 0.15,
    blur: 6,
    glowColor: '#2A3040',
    glowIntensity: 2,
    borderStyle: 'none',
    labelVisibility: 'on_click_only',
    pulseAnimation: true,
    pulseSpeed: 8,
    sizeMultiplier: 0.5,
  },
}

// ---- Simulation Node / Link types ----
interface SimNode extends SimulationNodeDatum {
  id: string
  name: string
  entity_type: string
  confidence: number
  cluster_ids: string[]
  tier: LegibilityTier
  baseRadius: number
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  id: string
  predicate: string
  confidence: number
  particleOffset: number
}

// ---- Helpers ----
function getTier(confidence: number): LegibilityTier {
  if (confidence >= 0.8) return 'solid'
  if (confidence >= 0.6) return 'defined'
  if (confidence >= 0.4) return 'emerging'
  if (confidence >= 0.2) return 'haze'
  return 'whisper'
}

function buildSimNodes(entities: EntityNode[]): SimNode[] {
  return entities.map((e) => ({
    id: e.canonical_id,
    name: e.name,
    entity_type: e.entity_type,
    confidence: e.confidence,
    cluster_ids: e.cluster_ids,
    tier: getTier(e.confidence),
    baseRadius: 6 + e.source_count * 0.5,
    x: (Math.random() - 0.5) * 200,
    y: (Math.random() - 0.5) * 200,
  }))
}

function buildSimLinks(relationships: Relationship[], nodeMap: Map<string, SimNode>): SimLink[] {
  return relationships
    .filter((r) => nodeMap.has(r.subject_id) && nodeMap.has(r.object_id))
    .map((r) => ({
      source: r.subject_id,
      target: r.object_id,
      id: r.id,
      predicate: r.predicate,
      confidence: r.confidence,
      particleOffset: Math.random(),
    }))
}

const BASE_NODE_RADIUS = 6

// ---- Component ----
export function OntologyGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null)
  const nodesRef = useRef<SimNode[]>([])
  const linksRef = useRef<SimLink[]>([])
  const transformRef = useRef<ZoomTransform>(zoomIdentity)
  const zoomBehaviorRef = useRef<ZoomBehavior<HTMLCanvasElement, unknown> | null>(null)
  const animFrameRef = useRef<number>(0)
  const hoveredNodeRef = useRef<SimNode | null>(null)
  const hoveredLinkRef = useRef<SimLink | null>(null)
  const timeRef = useRef<number>(0)
  const lastTickRef = useRef<number>(0)
  const contextMenuRef = useRef<{ x: number; y: number; node: SimNode } | null>(null)

  const [contextMenu, setContextMenu] = useState<{
    x: number
    y: number
    node: SimNode
  } | null>(null)

  // Store selectors
  const snapshot = useStore((s) => s.snapshot)
  const selectedElement = useStore((s) => s.selectedElement)
  const setSelectedElement = useStore((s) => s.setSelectedElement)
  const highlightedEntityIds = useStore((s) => s.highlightedEntityIds)
  const graphSettings = useStore((s) => s.graphSettings)
  const showGraphSettings = useStore((s) => s.showGraphSettings)
  const setShowGraphSettings = useStore((s) => s.setShowGraphSettings)
  const setGraphSettings = useStore((s) => s.setGraphSettings)

  // ---- Build / update simulation when snapshot changes ----
  useEffect(() => {
    if (!snapshot) return

    const entities = snapshot?.entities ?? []
    const nodes = buildSimNodes(entities)
    const nodeMap = new Map(nodes.map((n) => [n.id, n]))
    const links = buildSimLinks(snapshot?.relationships ?? [], nodeMap)

    nodesRef.current = nodes
    linksRef.current = links

    // Compute cluster centroids for cluster force
    const clusters = snapshot?.clusters ?? []
    const clusterCentroids = new Map<string, { x: number; y: number; count: number }>()
    for (const cluster of clusters) {
      clusterCentroids.set(cluster.cluster_id, { x: 0, y: 0, count: 0 })
    }

    const sim = forceSimulation<SimNode>(nodes)
      .force(
        'link',
        forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .strength((d) => (d as SimLink).confidence * graphSettings.linkStrength)
      )
      .force(
        'charge',
        forceManyBody<SimNode>().strength(graphSettings.chargeStrength)
      )
      .force(
        'center',
        forceCenter(0, 0).strength(graphSettings.centerStrength)
      )
      .force(
        'collide',
        forceCollide<SimNode>().radius(
          (d) => (d.baseRadius * TIER_CONFIGS[d.tier].sizeMultiplier) + graphSettings.collideRadius
        )
      )
      .alphaDecay(0.02)
      .velocityDecay(0.3)

    // Custom cluster force
    sim.force('cluster', () => {
      // Compute current centroids
      const centroids = new Map<string, { x: number; y: number; count: number }>()
      for (const node of nodes) {
        for (const cid of node.cluster_ids) {
          const c = centroids.get(cid) ?? { x: 0, y: 0, count: 0 }
          c.x += node.x ?? 0
          c.y += node.y ?? 0
          c.count += 1
          centroids.set(cid, c)
        }
      }
      for (const [, c] of centroids) {
        if (c.count > 0) {
          c.x /= c.count
          c.y /= c.count
        }
      }

      const strength = graphSettings.clusterForce * 0.05
      for (const node of nodes) {
        if (node.cluster_ids.length === 0) continue
        const primaryCluster = node.cluster_ids[0]
        const centroid = centroids.get(primaryCluster)
        if (!centroid || centroid.count < 2) continue
        node.vx = (node.vx ?? 0) + (centroid.x - (node.x ?? 0)) * strength
        node.vy = (node.vy ?? 0) + (centroid.y - (node.y ?? 0)) * strength
      }
    })

    simRef.current = sim

    return () => {
      sim.stop()
    }
  }, [snapshot, graphSettings.chargeStrength, graphSettings.linkStrength, graphSettings.centerStrength, graphSettings.collideRadius, graphSettings.clusterForce])

  // ---- Resize handler ----
  useEffect(() => {
    const container = containerRef.current
    const canvas = canvasRef.current
    if (!container || !canvas) return

    const ro = new ResizeObserver(() => {
      const rect = container.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      canvas.width = rect.width * dpr
      canvas.height = rect.height * dpr
      canvas.style.width = `${rect.width}px`
      canvas.style.height = `${rect.height}px`
    })
    ro.observe(container)
    // Initial size
    const rect = container.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    canvas.width = rect.width * dpr
    canvas.height = rect.height * dpr
    canvas.style.width = `${rect.width}px`
    canvas.style.height = `${rect.height}px`

    return () => ro.disconnect()
  }, [])

  // ---- Zoom ----
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const zoomBehavior = d3Zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.1, 10])
      .on('zoom', (event) => {
        transformRef.current = event.transform
      })

    zoomBehaviorRef.current = zoomBehavior
    select(canvas).call(zoomBehavior)

    return () => {
      select(canvas).on('.zoom', null)
    }
  }, [])

  // ---- Canvas-to-sim coordinate transform ----
  const screenToSim = useCallback((sx: number, sy: number): [number, number] => {
    const t = transformRef.current
    const canvas = canvasRef.current
    if (!canvas) return [0, 0]
    const rect = canvas.getBoundingClientRect()
    const cx = sx - rect.left - rect.width / 2
    const cy = sy - rect.top - rect.height / 2
    return [(cx - t.x) / t.k, (cy - t.y) / t.k]
  }, [])

  const findNodeAt = useCallback((sx: number, sy: number): SimNode | null => {
    const [mx, my] = screenToSim(sx, sy)
    const nodes = nodesRef.current
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i]
      const r = n.baseRadius * TIER_CONFIGS[n.tier].sizeMultiplier
      const dx = (n.x ?? 0) - mx
      const dy = (n.y ?? 0) - my
      if (dx * dx + dy * dy < (r + 4) * (r + 4)) return n
    }
    return null
  }, [screenToSim])

  const findLinkAt = useCallback((sx: number, sy: number): SimLink | null => {
    const [mx, my] = screenToSim(sx, sy)
    const links = linksRef.current
    for (const link of links) {
      const s = link.source as SimNode
      const t = link.target as SimNode
      const sx2 = s.x ?? 0
      const sy2 = s.y ?? 0
      const tx = t.x ?? 0
      const ty = t.y ?? 0
      // Point-to-line-segment distance
      const dx = tx - sx2
      const dy = ty - sy2
      const lenSq = dx * dx + dy * dy
      if (lenSq === 0) continue
      let param = ((mx - sx2) * dx + (my - sy2) * dy) / lenSq
      param = Math.max(0, Math.min(1, param))
      const px = sx2 + param * dx
      const py = sy2 + param * dy
      const dist = Math.sqrt((mx - px) * (mx - px) + (my - py) * (my - py))
      if (dist < 6) return link
    }
    return null
  }, [screenToSim])

  // ---- Mouse handlers ----
  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const node = findNodeAt(e.clientX, e.clientY)
      hoveredNodeRef.current = node
      if (!node) {
        hoveredLinkRef.current = findLinkAt(e.clientX, e.clientY)
      } else {
        hoveredLinkRef.current = null
      }
      const canvas = canvasRef.current
      if (canvas) {
        canvas.style.cursor = node || hoveredLinkRef.current ? 'pointer' : 'default'
      }
    },
    [findNodeAt, findLinkAt]
  )

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      setContextMenu(null)
      const node = findNodeAt(e.clientX, e.clientY)
      if (node) {
        setSelectedElement({ type: 'entity', id: node.id })
        return
      }
      const link = findLinkAt(e.clientX, e.clientY)
      if (link) {
        setSelectedElement({ type: 'relationship', id: link.id })
        return
      }
    },
    [findNodeAt, findLinkAt, setSelectedElement]
  )

  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      const node = findNodeAt(e.clientX, e.clientY)
      if (!node) {
        // Clear selection and recenter
        setSelectedElement(null)
        const canvas = canvasRef.current
        if (canvas && zoomBehaviorRef.current) {
          // Reset to identity transform (recenter)
          transformRef.current = zoomIdentity
          select(canvas).call(zoomBehaviorRef.current.transform, zoomIdentity)
        }
      }
    },
    [findNodeAt, setSelectedElement]
  )

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      const node = findNodeAt(e.clientX, e.clientY)
      if (node) {
        const rect = containerRef.current?.getBoundingClientRect()
        setContextMenu({
          x: e.clientX - (rect?.left ?? 0),
          y: e.clientY - (rect?.top ?? 0),
          node,
        })
        contextMenuRef.current = {
          x: e.clientX - (rect?.left ?? 0),
          y: e.clientY - (rect?.top ?? 0),
          node,
        }
      } else {
        setContextMenu(null)
      }
    },
    [findNodeAt]
  )

  // ---- Render loop ----
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    let running = true

    const draw = (timestamp: number) => {
      if (!running) return

      // Throttle to ~30fps
      const elapsed = timestamp - lastTickRef.current
      if (elapsed < 33) {
        animFrameRef.current = requestAnimationFrame(draw)
        return
      }
      lastTickRef.current = timestamp
      timeRef.current = timestamp / 1000

      const ctx = canvas.getContext('2d')
      if (!ctx) return

      const dpr = window.devicePixelRatio || 1
      const w = canvas.width / dpr
      const h = canvas.height / dpr

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      // Background
      ctx.fillStyle = BG_COLOR
      ctx.fillRect(0, 0, w, h)

      // Grid
      ctx.save()
      const t = transformRef.current
      ctx.translate(w / 2 + t.x, h / 2 + t.y)
      ctx.scale(t.k, t.k)

      drawGrid(ctx, w, h, t)

      const nodes = nodesRef.current
      const links = linksRef.current
      const time = timeRef.current

      // Viewport culling bounds in sim coordinates
      const viewLeft = (-w / 2 - t.x) / t.k - 50
      const viewRight = (w / 2 - t.x) / t.k + 50
      const viewTop = (-h / 2 - t.y) / t.k - 50
      const viewBottom = (h / 2 - t.y) / t.k + 50

      const isVisible = (x: number, y: number) =>
        x >= viewLeft && x <= viewRight && y >= viewTop && y <= viewBottom

      // Selected node for highlight logic
      const selectedId =
        selectedElement?.type === 'entity' ? selectedElement.id : null
      const selectedRelId =
        selectedElement?.type === 'relationship' ? selectedElement.id : null

      // Build connected set if a node is selected
      const connectedNodeIds = new Set<string>()
      const connectedLinkIds = new Set<string>()
      if (selectedId) {
        for (const link of links) {
          const sid = (link.source as SimNode).id
          const tid = (link.target as SimNode).id
          if (sid === selectedId || tid === selectedId) {
            connectedNodeIds.add(sid)
            connectedNodeIds.add(tid)
            connectedLinkIds.add(link.id)
          }
        }
        connectedNodeIds.add(selectedId)
      }

      const hasHighlights = highlightedEntityIds.size > 0
      const zoomLevel = t.k

      // Cluster hulls
      if (graphSettings.showClusterHulls && snapshot?.clusters) {
        drawClusterHulls(ctx, snapshot.clusters, nodes, time)
      }

      // Emerging structures
      if (graphSettings.showEmergingStructures && snapshot?.emerging_structures) {
        drawEmergingStructures(ctx, snapshot.emerging_structures, nodes, time)
      }

      // Edges
      for (const link of links) {
        const s = link.source as SimNode
        const tgt = link.target as SimNode
        const sx = s.x ?? 0
        const sy = s.y ?? 0
        const tx = tgt.x ?? 0
        const ty = tgt.y ?? 0
        if (!isVisible(sx, sy) && !isVisible(tx, ty)) continue

        let edgeOpacity = 1.0
        if (selectedId) {
          edgeOpacity = connectedLinkIds.has(link.id) ? 1.0 : 0.3
        }
        if (selectedRelId) {
          edgeOpacity = link.id === selectedRelId ? 1.0 : 0.3
        }
        if (hasHighlights) {
          const sid = s.id
          const tid = tgt.id
          edgeOpacity =
            highlightedEntityIds.has(sid) || highlightedEntityIds.has(tid) ? 1.0 : 0.2
        }

        // Edge color inherits from lower-confidence endpoint
        const lowerConf = Math.min(s.confidence, tgt.confidence)
        const lowerTier = getTier(lowerConf)
        const edgeColor = TIER_CONFIGS[lowerTier].glowColor

        const thickness = 0.5 + link.confidence * 1.5

        ctx.save()
        ctx.globalAlpha = edgeOpacity * 0.7
        ctx.strokeStyle = edgeColor
        ctx.lineWidth = thickness

        ctx.beginPath()
        ctx.moveTo(sx, sy)
        ctx.lineTo(tx, ty)
        ctx.stroke()

        // Directional flow particles
        const particleCount = Math.ceil(link.confidence * 3)
        for (let p = 0; p < particleCount; p++) {
          const offset = ((link.particleOffset + p / particleCount + time * 0.15) % 1)
          const px = sx + (tx - sx) * offset
          const py = sy + (ty - sy) * offset
          ctx.fillStyle = edgeColor
          ctx.globalAlpha = edgeOpacity * 0.9
          ctx.beginPath()
          ctx.arc(px, py, 1.5, 0, Math.PI * 2)
          ctx.fill()
        }

        ctx.restore()

        // Edge label on hover
        if (
          hoveredLinkRef.current === link ||
          (graphSettings.showEdgeLabels && zoomLevel > 1.5)
        ) {
          const midX = (sx + tx) / 2
          const midY = (sy + ty) / 2
          ctx.save()
          ctx.globalAlpha = 0.8
          ctx.fillStyle = '#c0c8d8'
          ctx.font = `${10 / Math.max(t.k, 0.5)}px sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'middle'
          ctx.fillText(link.predicate, midX, midY - 6)
          ctx.restore()
        }
      }

      // Nodes
      for (const node of nodes) {
        const nx = node.x ?? 0
        const ny = node.y ?? 0
        if (!isVisible(nx, ny)) continue

        const tier = TIER_CONFIGS[node.tier]
        const radius = node.baseRadius * tier.sizeMultiplier

        let nodeOpacity = tier.opacity
        if (selectedId) {
          nodeOpacity = connectedNodeIds.has(node.id) ? tier.opacity : 0.3 * tier.opacity
        }
        if (hasHighlights) {
          nodeOpacity = highlightedEntityIds.has(node.id) ? tier.opacity : 0.2 * tier.opacity
        }

        // Pulse animation
        let pulseScale = 1.0
        if (tier.pulseAnimation && tier.pulseSpeed > 0) {
          pulseScale = 1.0 + 0.08 * Math.sin((time * Math.PI * 2) / tier.pulseSpeed)
        }
        // Pulse highlighted nodes
        if (hasHighlights && highlightedEntityIds.has(node.id)) {
          pulseScale = 1.0 + 0.12 * Math.sin(time * Math.PI * 2 * 0.5)
        }

        const drawRadius = radius * pulseScale

        ctx.save()
        ctx.globalAlpha = nodeOpacity

        // Blur effect via shadow
        if (tier.blur > 0) {
          ctx.shadowBlur = tier.blur
          ctx.shadowColor = tier.glowColor
        }

        // Glow (larger blurred circle behind)
        ctx.shadowBlur = tier.glowIntensity
        ctx.shadowColor = tier.glowColor

        // Fill
        ctx.fillStyle = tier.glowColor
        ctx.beginPath()
        ctx.arc(nx, ny, drawRadius, 0, Math.PI * 2)
        ctx.fill()

        // Inner fill darker
        ctx.shadowBlur = 0
        ctx.shadowColor = 'transparent'
        ctx.fillStyle = BG_COLOR
        ctx.beginPath()
        ctx.arc(nx, ny, drawRadius * 0.7, 0, Math.PI * 2)
        ctx.fill()

        ctx.fillStyle = tier.glowColor
        ctx.globalAlpha = nodeOpacity * 0.8
        ctx.beginPath()
        ctx.arc(nx, ny, drawRadius * 0.5, 0, Math.PI * 2)
        ctx.fill()

        // Border
        if (tier.borderStyle === 'solid') {
          ctx.strokeStyle = tier.glowColor
          ctx.lineWidth = 1.5
          ctx.globalAlpha = nodeOpacity
          ctx.beginPath()
          ctx.arc(nx, ny, drawRadius, 0, Math.PI * 2)
          ctx.stroke()
        } else if (tier.borderStyle === 'dashed') {
          ctx.strokeStyle = tier.glowColor
          ctx.lineWidth = 1
          ctx.globalAlpha = nodeOpacity * 0.6
          ctx.setLineDash([3, 3])
          ctx.beginPath()
          ctx.arc(nx, ny, drawRadius, 0, Math.PI * 2)
          ctx.stroke()
          ctx.setLineDash([])
        }

        // Selection ring
        if (selectedId === node.id || (selectedRelId && connectedNodeIds.has(node.id))) {
          ctx.strokeStyle = CYAN_ACCENT
          ctx.lineWidth = 2
          ctx.globalAlpha = 1
          ctx.beginPath()
          ctx.arc(nx, ny, drawRadius + 4, 0, Math.PI * 2)
          ctx.stroke()
        }

        ctx.restore()

        // Label (level-of-detail)
        const showLabel = (() => {
          if (!graphSettings.showLabels) return false
          if (zoomLevel < 0.5) return false // dots only at very low zoom
          if (tier.labelVisibility === 'full') return zoomLevel > 0.3
          if (tier.labelVisibility === 'on_hover') return hoveredNodeRef.current === node
          if (tier.labelVisibility === 'on_click_only') return selectedId === node.id
          return false
        })()

        if (showLabel) {
          ctx.save()
          ctx.globalAlpha = nodeOpacity
          ctx.fillStyle = '#e0e8f0'
          const fontSize = Math.max(9, Math.min(13, 11 / Math.sqrt(t.k)))
          ctx.font = `${fontSize}px "Inter", sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'top'
          ctx.fillText(node.name, nx, ny + drawRadius + 4)
          ctx.restore()
        }
      }

      // Tooltip
      if (hoveredNodeRef.current) {
        const hn = hoveredNodeRef.current
        drawTooltip(ctx, hn, t, w, h)
      }

      ctx.restore()

      animFrameRef.current = requestAnimationFrame(draw)
    }

    animFrameRef.current = requestAnimationFrame(draw)

    return () => {
      running = false
      cancelAnimationFrame(animFrameRef.current)
    }
  }, [
    snapshot,
    selectedElement,
    highlightedEntityIds,
    graphSettings,
  ])

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        background: BG_COLOR,
      }}
    >
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onContextMenu={handleContextMenu}
        style={{ display: 'block', width: '100%', height: '100%' }}
      />

      {/* Gear icon button — top-right */}
      <button
        onClick={() => setShowGraphSettings(!showGraphSettings)}
        style={{
          position: 'absolute',
          top: 12,
          right: 12,
          width: 36,
          height: 36,
          border: '1px solid #2a3550',
          borderRadius: 8,
          background: showGraphSettings ? '#1a2440' : '#0d1220',
          color: '#8090b0',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 18,
          zIndex: 10,
        }}
        title="Graph Settings"
      >
        <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
          <path d="M11.078 0l.855 3.42a7.17 7.17 0 0 1 1.6.927l3.345-1.03 1.078 1.868-2.49 2.39c.1.35.165.713.196 1.084l3.338 1.05v2.156l-3.338 1.05a7.2 7.2 0 0 1-.196 1.084l2.49 2.39-1.078 1.868-3.345-1.03a7.17 7.17 0 0 1-1.6.927L11.078 20H8.922l-.855-3.42a7.17 7.17 0 0 1-1.6-.927l-3.345 1.03-1.078-1.868 2.49-2.39a7.163 7.163 0 0 1-.196-1.084L1 10.29V8.135l3.338-1.05c.031-.371.096-.735.196-1.084l-2.49-2.39L3.122 1.74l3.345 1.03a7.17 7.17 0 0 1 1.6-.927L8.922 0h2.156zM10 6.267a3.733 3.733 0 1 0 0 7.466 3.733 3.733 0 0 0 0-7.466z" />
        </svg>
      </button>

      {/* Graph Settings Panel */}
      {showGraphSettings && <SettingsPanel />}

      {/* Legend — bottom-left */}
      <Legend />

      {/* Context menu */}
      {contextMenu && (
        <ContextMenuOverlay
          x={contextMenu.x}
          y={contextMenu.y}
          node={contextMenu.node}
          onClose={() => setContextMenu(null)}
          onSelect={(action) => {
            if (action === 'select') {
              setSelectedElement({ type: 'entity', id: contextMenu.node.id })
            }
            setContextMenu(null)
          }}
        />
      )}
    </div>
  )
}

// ---- Drawing helpers ----

function drawGrid(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: ZoomTransform
) {
  const gridSpacing = 60
  const startX = Math.floor((-w / 2 - t.x) / t.k / gridSpacing) * gridSpacing
  const endX = Math.ceil((w / 2 - t.x) / t.k / gridSpacing) * gridSpacing
  const startY = Math.floor((-h / 2 - t.y) / t.k / gridSpacing) * gridSpacing
  const endY = Math.ceil((h / 2 - t.y) / t.k / gridSpacing) * gridSpacing

  ctx.strokeStyle = GRID_COLOR
  ctx.lineWidth = 0.5
  ctx.beginPath()
  for (let x = startX; x <= endX; x += gridSpacing) {
    ctx.moveTo(x, startY)
    ctx.lineTo(x, endY)
  }
  for (let y = startY; y <= endY; y += gridSpacing) {
    ctx.moveTo(startX, y)
    ctx.lineTo(endX, y)
  }
  ctx.stroke()
}

function drawClusterHulls(
  ctx: CanvasRenderingContext2D,
  clusters: DetectedCluster[],
  nodes: SimNode[],
  time: number
) {
  for (const cluster of clusters) {
    const memberNodes = nodes.filter((n) => n.cluster_ids.includes(cluster.cluster_id))
    if (memberNodes.length < 3) continue

    const points: [number, number][] = memberNodes.map((n) => [n.x ?? 0, n.y ?? 0])
    const hull = polygonHull(points)
    if (!hull || hull.length < 3) continue

    // Expand hull outward from centroid
    const centroid = polygonCentroid(hull)
    const expandedHull: [number, number][] = hull.map(([hx, hy]) => {
      const dx = hx - centroid[0]
      const dy = hy - centroid[1]
      const dist = Math.sqrt(dx * dx + dy * dy)
      const expand = 20
      return [hx + (dx / dist) * expand, hy + (dy / dist) * expand]
    })

    const hullLine = d3Line<[number, number]>()
      .x((d) => d[0])
      .y((d) => d[1])
      .curve(curveCatmullRomClosed)

    const pathStr = hullLine(expandedHull)
    if (!pathStr) continue

    const path = new Path2D(pathStr)

    const isHighConf = cluster.confidence >= 0.6
    ctx.save()

    // Fill
    const tierColor = isHighConf ? CYAN_ACCENT : '#3A4A5C'
    ctx.globalAlpha = isHighConf ? 0.1 : 0.05
    if (!isHighConf) {
      ctx.shadowBlur = 10
      ctx.shadowColor = tierColor
    }
    ctx.fillStyle = tierColor
    ctx.fill(path)

    // Stroke for high-confidence
    if (isHighConf) {
      ctx.globalAlpha = 0.3
      ctx.strokeStyle = tierColor
      ctx.lineWidth = 1
      ctx.stroke(path)
    }

    ctx.restore()
  }
}

function drawEmergingStructures(
  ctx: CanvasRenderingContext2D,
  structures: EmergingStructure[],
  nodes: SimNode[],
  time: number
) {
  for (const structure of structures) {
    const memberNodes = nodes.filter((n) => structure.member_ids.includes(n.id))
    if (memberNodes.length === 0) continue

    // Compute centroid
    let cx = 0
    let cy = 0
    for (const n of memberNodes) {
      cx += n.x ?? 0
      cy += n.y ?? 0
    }
    cx /= memberNodes.length
    cy /= memberNodes.length

    // Animated particle field: small drifting dots
    const particleCount = Math.ceil(structure.formation_progress * 20) + 5
    ctx.save()
    ctx.globalAlpha = 0.3 * structure.formation_progress
    ctx.fillStyle = AMBER_ACCENT

    for (let i = 0; i < particleCount; i++) {
      const angle = (i / particleCount) * Math.PI * 2 + time * 0.2
      const radius = 30 + 20 * Math.sin(time * 0.5 + i * 1.7)
      const px = cx + Math.cos(angle) * radius
      const py = cy + Math.sin(angle) * radius
      ctx.beginPath()
      ctx.arc(px, py, 1.2, 0, Math.PI * 2)
      ctx.fill()
    }

    ctx.restore()
  }
}

function drawTooltip(
  ctx: CanvasRenderingContext2D,
  node: SimNode,
  t: ZoomTransform,
  _w: number,
  _h: number
) {
  const nx = node.x ?? 0
  const ny = node.y ?? 0
  const tier = TIER_CONFIGS[node.tier]
  const r = node.baseRadius * tier.sizeMultiplier

  const tooltipX = nx + r + 12
  const tooltipY = ny - 20

  const lines = [
    node.name,
    `Type: ${node.entity_type}`,
    `Confidence: ${(node.confidence * 100).toFixed(0)}%`,
    `Tier: ${tier.label}`,
  ]
  if (node.cluster_ids.length > 0) {
    lines.push(`Clusters: ${node.cluster_ids.length}`)
  }

  const fontSize = Math.max(10, 12 / Math.sqrt(t.k))
  ctx.font = `${fontSize}px "Inter", sans-serif`

  const lineHeight = fontSize + 4
  const padding = 8
  let maxWidth = 0
  for (const line of lines) {
    const m = ctx.measureText(line)
    if (m.width > maxWidth) maxWidth = m.width
  }
  const boxW = maxWidth + padding * 2
  const boxH = lines.length * lineHeight + padding * 2

  ctx.save()
  ctx.globalAlpha = 0.92
  ctx.fillStyle = '#111827'
  ctx.strokeStyle = '#2a3550'
  ctx.lineWidth = 1
  roundRect(ctx, tooltipX, tooltipY, boxW, boxH, 6)
  ctx.fill()
  ctx.stroke()

  ctx.globalAlpha = 1
  ctx.fillStyle = '#e0e8f0'
  for (let i = 0; i < lines.length; i++) {
    if (i === 0) {
      ctx.font = `bold ${fontSize}px "Inter", sans-serif`
    } else {
      ctx.font = `${fontSize * 0.9}px "Inter", sans-serif`
      ctx.fillStyle = '#8899b0'
    }
    ctx.textAlign = 'left'
    ctx.textBaseline = 'top'
    ctx.fillText(lines[i], tooltipX + padding, tooltipY + padding + i * lineHeight)
  }
  ctx.restore()
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + r)
  ctx.lineTo(x + w, y + h - r)
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

// ---- Settings Panel ----
function SettingsPanel() {
  const graphSettings = useStore((s) => s.graphSettings)
  const setGraphSettings = useStore((s) => s.setGraphSettings)

  const sliderStyle: React.CSSProperties = {
    width: '100%',
    accentColor: CYAN_ACCENT,
    background: 'transparent',
    height: 4,
  }

  const labelStyle: React.CSSProperties = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 11,
    color: '#8899b0',
    marginBottom: 2,
  }

  const rowStyle: React.CSSProperties = {
    marginBottom: 12,
  }

  return (
    <div
      style={{
        position: 'absolute',
        top: 52,
        right: 12,
        width: 240,
        background: '#0d1220ee',
        border: '1px solid #2a3550',
        borderRadius: 10,
        padding: 16,
        zIndex: 10,
        backdropFilter: 'blur(12px)',
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: '#c0c8d8',
          marginBottom: 14,
          textTransform: 'uppercase',
          letterSpacing: 1,
        }}
      >
        Graph Settings
      </div>

      <div style={rowStyle}>
        <div style={labelStyle}>
          <span>Charge Strength</span>
          <span>{graphSettings.chargeStrength}</span>
        </div>
        <input
          type="range"
          min={-200}
          max={0}
          step={1}
          value={graphSettings.chargeStrength}
          onChange={(e) => setGraphSettings({ chargeStrength: Number(e.target.value) })}
          style={sliderStyle}
        />
      </div>

      <div style={rowStyle}>
        <div style={labelStyle}>
          <span>Link Strength</span>
          <span>{graphSettings.linkStrength.toFixed(2)}</span>
        </div>
        <input
          type="range"
          min={0}
          max={2}
          step={0.05}
          value={graphSettings.linkStrength}
          onChange={(e) => setGraphSettings({ linkStrength: Number(e.target.value) })}
          style={sliderStyle}
        />
      </div>

      <div style={rowStyle}>
        <div style={labelStyle}>
          <span>Cluster Force</span>
          <span>{graphSettings.clusterForce.toFixed(2)}</span>
        </div>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={graphSettings.clusterForce}
          onChange={(e) => setGraphSettings({ clusterForce: Number(e.target.value) })}
          style={sliderStyle}
        />
      </div>

      <div
        style={{
          borderTop: '1px solid #1e2940',
          paddingTop: 10,
          marginTop: 4,
        }}
      >
        <ToggleRow
          label="Show Labels"
          value={graphSettings.showLabels}
          onChange={(v) => setGraphSettings({ showLabels: v })}
        />
        <ToggleRow
          label="Cluster Hulls"
          value={graphSettings.showClusterHulls}
          onChange={(v) => setGraphSettings({ showClusterHulls: v })}
        />
        <ToggleRow
          label="Emerging Structures"
          value={graphSettings.showEmergingStructures}
          onChange={(v) => setGraphSettings({ showEmergingStructures: v })}
        />
      </div>
    </div>
  )
}

function ToggleRow({
  label,
  value,
  onChange,
}: {
  label: string
  value: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        fontSize: 11,
        color: '#8899b0',
        marginBottom: 8,
        cursor: 'pointer',
      }}
      onClick={() => onChange(!value)}
    >
      <span>{label}</span>
      <div
        style={{
          width: 32,
          height: 16,
          borderRadius: 8,
          background: value ? CYAN_ACCENT + '44' : '#1e2940',
          border: `1px solid ${value ? CYAN_ACCENT : '#2a3550'}`,
          position: 'relative',
          transition: 'all 0.2s',
        }}
      >
        <div
          style={{
            width: 12,
            height: 12,
            borderRadius: 6,
            background: value ? CYAN_ACCENT : '#4a5570',
            position: 'absolute',
            top: 1,
            left: value ? 17 : 1,
            transition: 'left 0.2s',
          }}
        />
      </div>
    </div>
  )
}

// ---- Legend ----
function Legend() {
  const tiers: LegibilityTier[] = ['solid', 'defined', 'emerging', 'haze', 'whisper']

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 16,
        left: 16,
        background: '#0d1220dd',
        border: '1px solid #1e2940',
        borderRadius: 8,
        padding: '10px 14px',
        zIndex: 10,
        backdropFilter: 'blur(8px)',
      }}
    >
      <div
        style={{
          fontSize: 10,
          fontWeight: 600,
          color: '#607090',
          textTransform: 'uppercase',
          letterSpacing: 1,
          marginBottom: 8,
        }}
      >
        Legibility
      </div>
      {tiers.map((tier) => {
        const cfg = TIER_CONFIGS[tier]
        return (
          <div
            key={tier}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 4,
              fontSize: 11,
              color: '#8899b0',
            }}
          >
            <div
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: cfg.glowColor,
                opacity: cfg.opacity,
                boxShadow: `0 0 ${cfg.glowIntensity / 2}px ${cfg.glowColor}`,
                border:
                  cfg.borderStyle === 'solid'
                    ? `1px solid ${cfg.glowColor}`
                    : cfg.borderStyle === 'dashed'
                      ? `1px dashed ${cfg.glowColor}`
                      : 'none',
                flexShrink: 0,
              }}
            />
            <span>{cfg.label}</span>
            <span style={{ color: '#4a5570', fontSize: 10, marginLeft: 'auto' }}>
              {cfg.minConfidence > 0 ? `${(cfg.minConfidence * 100).toFixed(0)}%+` : '<20%'}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ---- Context menu ----
function ContextMenuOverlay({
  x,
  y,
  node,
  onClose,
  onSelect,
}: {
  x: number
  y: number
  node: SimNode
  onClose: () => void
  onSelect: (action: string) => void
}) {
  useEffect(() => {
    const handler = () => onClose()
    window.addEventListener('click', handler)
    return () => window.removeEventListener('click', handler)
  }, [onClose])

  const items = [
    { key: 'select', label: 'Select Entity' },
    { key: 'focus', label: 'Focus on Node' },
    { key: 'expand', label: 'Expand Connections' },
    { key: 'hide', label: 'Hide Node' },
  ]

  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        background: '#111827',
        border: '1px solid #2a3550',
        borderRadius: 8,
        padding: 4,
        zIndex: 20,
        minWidth: 160,
        boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div
        style={{
          fontSize: 11,
          color: '#607090',
          padding: '6px 10px',
          borderBottom: '1px solid #1e2940',
          marginBottom: 2,
        }}
      >
        {node.name}
      </div>
      {items.map((item) => (
        <div
          key={item.key}
          onClick={() => onSelect(item.key)}
          style={{
            fontSize: 12,
            color: '#c0c8d8',
            padding: '6px 10px',
            cursor: 'pointer',
            borderRadius: 4,
          }}
          onMouseEnter={(e) => {
            ;(e.target as HTMLDivElement).style.background = '#1a2440'
          }}
          onMouseLeave={(e) => {
            ;(e.target as HTMLDivElement).style.background = 'transparent'
          }}
        >
          {item.label}
        </div>
      ))}
    </div>
  )
}

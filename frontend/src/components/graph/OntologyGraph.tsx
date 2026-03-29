// ============================================
// OntologyGraph — D3 force-directed graph visualization
// ============================================

import React, { useRef, useEffect, useMemo, useCallback } from 'react'
import * as d3Force from 'd3-force'
import * as d3Selection from 'd3-selection'
import * as d3Zoom from 'd3-zoom'
import * as d3Polygon from 'd3-polygon'
import * as d3Scale from 'd3-scale'
import * as d3ScaleChromatic from 'd3-scale-chromatic'
import { useStore } from '../../store'
import type { EntityNode, Relationship, DetectedCluster } from '../../api/types'

interface SimNode extends d3Force.SimulationNodeDatum {
  id: string
  entity: EntityNode
}

interface SimLink extends d3Force.SimulationLinkDatum<SimNode> {
  rel: Relationship
}

export const OntologyGraph: React.FC = () => {
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const entities = useStore((s) => s.entities)
  const relationships = useStore((s) => s.relationships)
  const snapshot = useStore((s) => s.snapshot)
  const graphSettings = useStore((s) => s.graphSettings)
  const selectedElement = useStore((s) => s.selectedElement)
  const setSelectedElement = useStore((s) => s.setSelectedElement)
  const highlightedEntityIds = useStore((s) => s.highlightedEntityIds)
  const confidenceFloor = useStore((s) => s.confidenceFloor)

  // Filter entities by confidence
  const filteredEntities = useMemo(
    () => entities.filter((e) => e.confidence >= confidenceFloor),
    [entities, confidenceFloor],
  )

  // Build entity ID set for link filtering
  const entityIdSet = useMemo(
    () => new Set(filteredEntities.map((e) => e.canonical_id)),
    [filteredEntities],
  )

  const filteredRelationships = useMemo(
    () => relationships.filter((r) => entityIdSet.has(r.subject_id) && entityIdSet.has(r.object_id)),
    [relationships, entityIdSet],
  )

  const clusters = snapshot?.clusters || []

  // Cluster color scale
  const clusterColorScale = useMemo(() => {
    const ids = clusters.map((c) => c.cluster_id)
    return d3Scale.scaleOrdinal<string, string>(d3ScaleChromatic.schemeTableau10).domain(ids)
  }, [clusters])

  // D3 rendering
  useEffect(() => {
    const svg = svgRef.current
    const container = containerRef.current
    if (!svg || !container) return

    const width = container.clientWidth
    const height = container.clientHeight
    if (width === 0 || height === 0) return

    // Clear previous
    const sel = d3Selection.select(svg)
    sel.selectAll('*').remove()

    svg.setAttribute('width', String(width))
    svg.setAttribute('height', String(height))

    // Build nodes and links
    const nodes: SimNode[] = filteredEntities.map((e) => ({
      id: e.canonical_id,
      entity: e,
    }))

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    const links: SimLink[] = filteredRelationships
      .filter((r) => nodeMap.has(r.subject_id) && nodeMap.has(r.object_id))
      .map((r) => ({
        source: r.subject_id,
        target: r.object_id,
        rel: r,
      }))

    if (nodes.length === 0) {
      sel.append('text')
        .attr('x', width / 2)
        .attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', '#4a5568')
        .attr('font-size', '12px')
        .attr('font-family', 'var(--font-mono)')
        .text('No entities to display')
      return
    }

    // Group for zoom
    const g = sel.append('g')

    // Zoom
    const zoom = d3Zoom.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 5])
      .on('zoom', (event) => {
        g.attr('transform', event.transform.toString())
      })

    sel.call(zoom)

    // Cluster hulls
    if (graphSettings.showClusterHulls && clusters.length > 0) {
      const hullGroup = g.append('g').attr('class', 'hulls')

      const clusterMembers = new Map<string, SimNode[]>()
      for (const node of nodes) {
        for (const cid of node.entity.cluster_ids) {
          if (!clusterMembers.has(cid)) clusterMembers.set(cid, [])
          clusterMembers.get(cid)!.push(node)
        }
      }

      hullGroup
        .selectAll('path')
        .data(Array.from(clusterMembers.entries()).filter(([, members]) => members.length >= 3))
        .join('path')
        .attr('class', 'cluster-hull')
        .attr('fill', ([id]) => clusterColorScale(id))
        .attr('fill-opacity', 0.05)
        .attr('stroke', ([id]) => clusterColorScale(id))
        .attr('stroke-opacity', 0.15)
        .attr('stroke-width', 1)
    }

    // Links
    const linkGroup = g.append('g').attr('class', 'links')
    const linkElements = linkGroup
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('stroke', '#1e2940')
      .attr('stroke-width', (d) => Math.max(0.5, d.rel.confidence * 2))
      .attr('stroke-opacity', (d) => 0.2 + d.rel.confidence * 0.4)

    // Nodes
    const nodeGroup = g.append('g').attr('class', 'nodes')
    const nodeElements = nodeGroup
      .selectAll('g')
      .data(nodes)
      .join('g')
      .attr('cursor', 'pointer')
      .on('click', (_, d) => {
        setSelectedElement({ type: 'entity', id: d.id })
      })

    // Node circles
    nodeElements
      .append('circle')
      .attr('r', (d) => 3 + d.entity.rendering.size_multiplier * 3)
      .attr('fill', (d) => {
        if (d.entity.cluster_ids.length > 0) {
          return clusterColorScale(d.entity.cluster_ids[0])
        }
        return d.entity.rendering.glow_color
      })
      .attr('fill-opacity', (d) => d.entity.rendering.opacity * 0.8)
      .attr('stroke', (d) => {
        if (selectedElement?.type === 'entity' && selectedElement.id === d.id) return '#00D4FF'
        if (highlightedEntityIds.has(d.id)) return '#FFB833'
        return 'transparent'
      })
      .attr('stroke-width', (d) => {
        if (selectedElement?.type === 'entity' && selectedElement.id === d.id) return 2
        if (highlightedEntityIds.has(d.id)) return 1.5
        return 0
      })

    // Labels
    if (graphSettings.showLabels) {
      nodeElements
        .append('text')
        .text((d) => d.entity.name.length > 20 ? d.entity.name.slice(0, 18) + '…' : d.entity.name)
        .attr('dx', 8)
        .attr('dy', 3)
        .attr('font-size', '8px')
        .attr('font-family', 'var(--font-mono)')
        .attr('fill', '#7a8494')
        .attr('fill-opacity', (d) => {
          const vis = d.entity.rendering.label_visibility
          if (vis === 'full') return 0.8
          if (vis === 'on_hover') return 0.3
          return 0
        })
    }

    // Simulation
    const simulation = d3Force
      .forceSimulation<SimNode>(nodes)
      .force(
        'link',
        d3Force
          .forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .strength(graphSettings.linkStrength * 0.1),
      )
      .force('charge', d3Force.forceManyBody().strength(graphSettings.chargeStrength))
      .force('center', d3Force.forceCenter(width / 2, height / 2).strength(graphSettings.centerStrength * 0.1))
      .force('collide', d3Force.forceCollide().radius(graphSettings.collideRadius + 4))
      .alphaDecay(0.02)

    simulation.on('tick', () => {
      linkElements
        .attr('x1', (d) => (d.source as SimNode).x ?? 0)
        .attr('y1', (d) => (d.source as SimNode).y ?? 0)
        .attr('x2', (d) => (d.target as SimNode).x ?? 0)
        .attr('y2', (d) => (d.target as SimNode).y ?? 0)

      nodeElements.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`)

      // Update hull paths
      if (graphSettings.showClusterHulls) {
        g.selectAll('.cluster-hull').attr('d', (datum: any) => {
          const [, members] = datum as [string, SimNode[]]
          const points: [number, number][] = members.map((m) => [m.x ?? 0, m.y ?? 0])
          if (points.length < 3) return ''
          const hull = d3Polygon.polygonHull(points)
          return hull ? `M${hull.map((p) => p.join(',')).join('L')}Z` : ''
        })
      }
    })

    return () => {
      simulation.stop()
    }
  }, [
    filteredEntities, filteredRelationships, clusters, graphSettings,
    selectedElement, highlightedEntityIds, clusterColorScale, setSelectedElement, confidenceFloor,
  ])

  return (
    <div ref={containerRef} className="w-full h-full relative grid-texture">
      <svg ref={svgRef} className="w-full h-full" />
      {/* Stats overlay */}
      <div className="absolute bottom-2 left-2 data-readout flex gap-3">
        <span>{filteredEntities.length} nodes</span>
        <span>{filteredRelationships.length} edges</span>
        <span>{clusters.length} clusters</span>
      </div>
    </div>
  )
}

export default OntologyGraph

import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { QueryBar } from './components/QueryBar'
import { ResultsPanel } from './components/ResultsPanel'
import { GraphView } from './components/GraphView'
import { ClusterView } from './components/ClusterView'
import { CriticDashboard } from './components/CriticDashboard'
import { IngestPanel } from './components/IngestPanel'
import type { QueryResponse } from './api'

type Tab = 'query' | 'graph' | 'clusters' | 'critic' | 'ingest'

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('query')
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null)
  const [isQuerying, setIsQuerying] = useState(false)

  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.getHealth,
    refetchInterval: 10000,
  })

  const handleQuery = async (question: string) => {
    setIsQuerying(true)
    try {
      const result = await api.query(question)
      setQueryResult(result)
    } finally {
      setIsQuerying(false)
    }
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'query', label: 'Query' },
    { id: 'graph', label: 'Graph' },
    { id: 'clusters', label: 'Clusters' },
    { id: 'critic', label: 'Critic' },
    { id: 'ingest', label: 'Ingest' },
  ]

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <h1 style={styles.title}>Periphery</h1>
        <p style={styles.subtitle}>Schema as observation, not imposition</p>
        {health.data && (
          <div style={styles.healthBar}>
            <span style={styles.healthDot(health.data.status === 'healthy')} />
            <span>{health.data.vectors} vectors</span>
            <span>{health.data.clusters} clusters</span>
          </div>
        )}
      </header>

      <nav style={styles.nav}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={styles.tab(activeTab === tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <main style={styles.main}>
        {activeTab === 'query' && (
          <div>
            <QueryBar onSubmit={handleQuery} isLoading={isQuerying} />
            {queryResult && <ResultsPanel result={queryResult} />}
          </div>
        )}
        {activeTab === 'graph' && <GraphView />}
        {activeTab === 'clusters' && <ClusterView />}
        {activeTab === 'critic' && <CriticDashboard />}
        {activeTab === 'ingest' && <IngestPanel />}
      </main>
    </div>
  )
}

const styles = {
  container: {
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
    maxWidth: 1200,
    margin: '0 auto',
    padding: 20,
    color: '#e0e0e0',
    backgroundColor: '#0a0a0f',
    minHeight: '100vh',
  } as React.CSSProperties,
  header: {
    textAlign: 'center' as const,
    marginBottom: 30,
    borderBottom: '1px solid #1a1a2e',
    paddingBottom: 20,
  } as React.CSSProperties,
  title: {
    fontSize: 32,
    fontWeight: 300,
    letterSpacing: 4,
    color: '#8888ff',
    margin: 0,
  } as React.CSSProperties,
  subtitle: {
    fontSize: 14,
    color: '#666',
    fontStyle: 'italic',
    margin: '8px 0 0',
  } as React.CSSProperties,
  healthBar: {
    display: 'flex',
    justifyContent: 'center',
    gap: 16,
    marginTop: 12,
    fontSize: 12,
    color: '#888',
  } as React.CSSProperties,
  healthDot: (healthy: boolean) =>
    ({
      display: 'inline-block',
      width: 8,
      height: 8,
      borderRadius: '50%',
      backgroundColor: healthy ? '#4caf50' : '#f44336',
      marginRight: 4,
      alignSelf: 'center',
    }) as React.CSSProperties,
  nav: {
    display: 'flex',
    justifyContent: 'center',
    gap: 4,
    marginBottom: 24,
  } as React.CSSProperties,
  tab: (active: boolean) =>
    ({
      padding: '8px 20px',
      border: 'none',
      borderBottom: active ? '2px solid #8888ff' : '2px solid transparent',
      background: 'none',
      color: active ? '#8888ff' : '#666',
      cursor: 'pointer',
      fontSize: 14,
      fontWeight: active ? 600 : 400,
      transition: 'all 0.2s',
    }) as React.CSSProperties,
  main: {
    minHeight: 400,
  } as React.CSSProperties,
}

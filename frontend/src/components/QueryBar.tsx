import React, { useState } from 'react'

interface Props {
  onSubmit: (question: string) => void
  isLoading: boolean
}

export function QueryBar({ onSubmit, isLoading }: Props) {
  const [question, setQuestion] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (question.trim() && !isLoading) {
      onSubmit(question.trim())
    }
  }

  return (
    <form onSubmit={handleSubmit} style={styles.form}>
      <input
        type="text"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="Ask anything about your data..."
        style={styles.input}
        disabled={isLoading}
      />
      <button type="submit" style={styles.button} disabled={isLoading || !question.trim()}>
        {isLoading ? 'Thinking...' : 'Query'}
      </button>
    </form>
  )
}

const styles = {
  form: {
    display: 'flex',
    gap: 8,
    marginBottom: 24,
  } as React.CSSProperties,
  input: {
    flex: 1,
    padding: '12px 16px',
    fontSize: 16,
    border: '1px solid #2a2a3e',
    borderRadius: 8,
    backgroundColor: '#12121f',
    color: '#e0e0e0',
    outline: 'none',
  } as React.CSSProperties,
  button: {
    padding: '12px 24px',
    fontSize: 14,
    fontWeight: 600,
    border: 'none',
    borderRadius: 8,
    backgroundColor: '#4444aa',
    color: '#fff',
    cursor: 'pointer',
  } as React.CSSProperties,
}

// ============================================
// Periphery WebSocket Client
// Robust WebSocket with auto-reconnect, ping/pong,
// channel-based subscriptions, and status tracking
// ============================================

export type MessageType =
  | 'snapshot_update'
  | 'query_update'
  | 'new_document'
  | 'heartbeat'
  | 'pong'

export interface WSMessage {
  type: MessageType
  timestamp?: string
  data?: any
  query_id?: string
}

type MessageHandler = (message: WSMessage) => void

export class PeripheryWebSocket {
  private ws: WebSocket | null = null
  private url: string
  private handlers: Map<string, Set<MessageHandler>> = new Map()
  private reconnectAttempts: number = 0
  private maxReconnectAttempts: number = 20
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pingInterval: ReturnType<typeof setInterval> | null = null
  private intentionallyClosed: boolean = false
  private _status: 'connected' | 'reconnecting' | 'disconnected' = 'disconnected'
  private statusListeners: Set<(status: string) => void> = new Set()

  constructor(url: string) {
    this.url = url
  }

  get status(): 'connected' | 'reconnecting' | 'disconnected' {
    return this._status
  }

  private setStatus(status: 'connected' | 'reconnecting' | 'disconnected') {
    this._status = status
    this.statusListeners.forEach((fn) => fn(status))
  }

  onStatusChange(listener: (status: string) => void): () => void {
    this.statusListeners.add(listener)
    return () => {
      this.statusListeners.delete(listener)
    }
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return
    this.intentionallyClosed = false

    try {
      this.ws = new WebSocket(this.url)
    } catch (err) {
      console.error('[WS] Failed to create WebSocket:', err)
      this.setStatus('disconnected')
      this.scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      console.log('[WS] Connected to', this.url)
      this.reconnectAttempts = 0
      this.setStatus('connected')
      this.startPing()
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const message: WSMessage = JSON.parse(event.data)
        if (message.type === 'pong' || message.type === 'heartbeat') return
        this.dispatch(message.type, message)
        // Also dispatch a wildcard for listeners that want everything
        this.dispatch('*', message)
      } catch (err) {
        console.error('[WS] Failed to parse message:', err)
      }
    }

    this.ws.onclose = (event: CloseEvent) => {
      console.log('[WS] Connection closed:', event.code, event.reason)
      this.stopPing()
      if (!this.intentionallyClosed) {
        this.setStatus('reconnecting')
        this.scheduleReconnect()
      } else {
        this.setStatus('disconnected')
      }
    }

    this.ws.onerror = (_event: Event) => {
      console.error('[WS] Error on', this.url)
      // onclose will fire after this, which handles reconnection
    }
  }

  disconnect(): void {
    this.intentionallyClosed = true
    this.stopPing()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect')
      this.ws = null
    }
    this.setStatus('disconnected')
  }

  on(messageType: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(messageType)) {
      this.handlers.set(messageType, new Set())
    }
    this.handlers.get(messageType)!.add(handler)

    // Return unsubscribe function
    return () => {
      this.handlers.get(messageType)?.delete(handler)
    }
  }

  private dispatch(messageType: string, message: WSMessage): void {
    const handlers = this.handlers.get(messageType)
    if (handlers) {
      handlers.forEach((handler) => {
        try {
          handler(message)
        } catch (err) {
          console.error('[WS] Handler error for', messageType, err)
        }
      })
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[WS] Max reconnect attempts reached')
      this.setStatus('disconnected')
      return
    }

    // Exponential backoff: 1s, 2s, 4s, 8s, ... capped at 30s
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000)
    this.reconnectAttempts++

    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`)

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private startPing(): void {
    this.stopPing()
    // Send a ping every 30 seconds to keep the connection alive
    this.pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 30000)
  }

  private stopPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval)
      this.pingInterval = null
    }
  }
}

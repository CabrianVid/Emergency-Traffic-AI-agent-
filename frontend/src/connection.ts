import { useCallback, useEffect, useState } from 'react'
import type { Snapshot } from './model'

export async function api<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) {
    const issue = await response.json().catch(() => ({}))
    const detail = issue.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.[0]?.msg ?? 'The simulator connection is unavailable.')
  }
  return response.json()
}

export function useSnapshot() {
  const [state, setState] = useState<Snapshot | null>(null)
  const [connection, setConnection] = useState<'connecting' | 'live' | 'polling' | 'offline'>('connecting')
  const accept = useCallback((next: Snapshot, reset = false) => {
    setState((previous) => reset || !previous || next.sequence >= previous.sequence ? next : previous)
  }, [])

  useEffect(() => {
    let disposed = false
    let socket: WebSocket | null = null
    let reconnect: number | undefined
    let requesting = false
    let lastSequence = -1

    async function poll() {
      if (requesting || socket?.readyState === WebSocket.OPEN) return
      requesting = true
      try {
        const next = await api<Snapshot>('/api/state')
        if (disposed || socket?.readyState === WebSocket.OPEN) return
        accept(next, next.sequence < lastSequence)
        lastSequence = next.sequence
        setConnection('polling')
      } catch {
        if (!disposed) setConnection('offline')
      } finally {
        requesting = false
      }
    }
    function connect() {
      if (disposed) return
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${protocol}://${location.host}/ws`)
      socket.onopen = () => { if (!disposed) setConnection('live') }
      socket.onmessage = ({ data }) => {
        if (disposed) return
        try {
          const next = JSON.parse(data) as Snapshot
          accept(next, next.sequence < lastSequence)
          lastSequence = next.sequence
        } catch {
          socket?.close()
        }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = () => {
        if (disposed) return
        setConnection('connecting')
        void poll()
        reconnect = window.setTimeout(connect, 1800)
      }
    }
    void poll()
    connect()
    const pollTimer = window.setInterval(poll, 1100)
    return () => {
      disposed = true
      window.clearInterval(pollTimer)
      window.clearTimeout(reconnect)
      socket?.close()
    }
  }, [accept])
  return { state, connection, accept }
}

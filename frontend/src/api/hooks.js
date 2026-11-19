import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { OfflineError, checkHealth, getApiStatus, subscribeApiStatus } from './client'
import { ROLE_ACTOR, ROLE_HEADER, ROLE_LABEL } from './config'
import { getHitlPolicy } from './kairos'
import { MOCK_PERMISSIONS } from './mocks'
import { useKairos } from '../lib/store'

/** Connection status shared by every panel: { state: 'unknown'|'online'|'offline', policyVersion, env, base }. */
export function useApiStatus() {
  return useSyncExternalStore(subscribeApiStatus, getApiStatus)
}

/** Polls /health so the header shows whether the backend is up. Mount once (Shell). */
export function useHealthPoll(ms = 15000) {
  useEffect(() => {
    checkHealth()
    const t = setInterval(checkHealth, ms)
    return () => clearInterval(t)
  }, [ms])
}

/** The UI role as the backend sees it. */
export function useApiRole() {
  const { state } = useKairos()
  return { role: ROLE_HEADER[state.role] || 'viewer', actor: ROLE_ACTOR[state.role] || 'viewer', label: ROLE_LABEL[state.role] || state.role }
}

/**
 * Load one API resource with a per-call mock fallback.
 *   source 'live'  → data came from the backend
 *   source 'mock'  → the call failed; data = fallback() (may be null). `offline` says whether the API was
 *                    unreachable (vs. answered with an error such as a 404 for a route this branch lacks).
 * @template T
 * @param {(opt: {signal: AbortSignal}) => Promise<T>} fetcher
 * @param {{fallback?: () => T, deps?: any[], poll?: number, enabled?: boolean}} [cfg]
 */
export function useApi(fetcher, { fallback, deps = [], poll = 0, enabled = true } = {}) {
  const [res, setRes] = useState({ data: null, source: 'loading', offline: false, error: null })
  const fRef = useRef(fetcher)
  const fbRef = useRef(fallback)
  fRef.current = fetcher
  fbRef.current = fallback
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((n) => n + 1), [])

  useEffect(() => {
    if (!enabled) return undefined
    const ctl = new AbortController()
    let alive = true
    fRef.current({ signal: ctl.signal })
      .then((data) => alive && setRes({ data, source: 'live', offline: false, error: null }))
      .catch((e) => {
        if (!alive || ctl.signal.aborted) return
        setRes({ data: fbRef.current ? fbRef.current() : null, source: 'mock', offline: e instanceof OfflineError, error: e })
      })
    return () => { alive = false; ctl.abort() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled])

  useEffect(() => {
    if (!poll || !enabled) return undefined
    const t = setInterval(reload, poll)
    return () => clearInterval(t)
  }, [poll, enabled, reload])

  return { ...res, loading: res.source === 'loading', reload }
}

/** What the current role may do (GET /hitl/policy → permissions); the mock table when the API is unreachable. */
export function useHitlPermissions() {
  const { role } = useApiRole()
  const status = useApiStatus()
  const res = useApi((o) => getHitlPolicy(role, o), { fallback: () => ({ permissions: MOCK_PERMISSIONS[role] || [] }), deps: [role, status.state] })
  return new Set(res.data?.permissions || [])
}

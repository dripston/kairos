// Light / dark theme. The choice is kept per browser; first visit follows the OS setting.
// index.html sets data-theme before React loads, so there is no flash of the wrong theme.
import { useSyncExternalStore } from 'react'

const KEY = 'kairos.theme'
const listeners = new Set()
const read = () => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')

export function setTheme(theme) {
  document.documentElement.dataset.theme = theme
  try { localStorage.setItem(KEY, theme) } catch { /* storage blocked: still switches for this visit */ }
  listeners.forEach((fn) => fn())
}

export const toggleTheme = () => setTheme(read() === 'light' ? 'dark' : 'light')

export function useTheme() {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => listeners.delete(fn) }, read)
}

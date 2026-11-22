import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { logout } from './auth'
import ListsPanel from './components/ListsPanel'
import ListView from './components/ListView'

export default function App() {
  const [selectedId, setSelectedId] = useState(null)
  const [error, setError] = useState('')

  // Also creates our profile row on the first visit, so others can find you.
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })

  useEffect(() => {
    const onError = (event) => setError(event.detail)
    window.addEventListener('app-error', onError)
    return () => window.removeEventListener('app-error', onError)
  }, [])

  useEffect(() => {
    if (!error) return
    const timer = setTimeout(() => setError(''), 6000)
    return () => clearTimeout(timer)
  }, [error])

  return (
    <div className="app">
      <header>
        <h1>Grocery list</h1>
        <div className="row">
          <span>{me.data?.username}</span>
          <button className="secondary small" onClick={logout}>Log out</button>
        </div>
      </header>

      {error && (
        <div className="banner" role="alert">
          <span>{error}</span>
          <button className="icon" aria-label="Dismiss" onClick={() => setError('')}>✕</button>
        </div>
      )}

      <div className="layout">
        <ListsPanel selectedId={selectedId} onSelect={setSelectedId} />
        {selectedId ? (
          <ListView key={selectedId} listId={selectedId} onClose={() => setSelectedId(null)} />
        ) : (
          <section className="panel muted">Pick a list on the left, or start a new one.</section>
        )}
      </div>
    </div>
  )
}

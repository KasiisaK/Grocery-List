import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

export default function ListsPanel({ selectedId, onSelect }) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')

  // Poll every 15 s so lists you are invited to show up on their own
  const lists = useQuery({ queryKey: ['lists'], queryFn: api.getLists, refetchInterval: 15000 })

  const create = useMutation({
    mutationFn: api.createList,
    onSuccess: (list) => {
      queryClient.invalidateQueries({ queryKey: ['lists'] })
      setTitle('')
      onSelect(list.id)
    },
  })

  function submit(event) {
    event.preventDefault()
    const trimmed = title.trim()
    if (trimmed) create.mutate({ title: trimmed })
  }

  return (
    <section className="panel">
      <h2>My lists</h2>
      <form className="row" onSubmit={submit}>
        <input
          className="grow"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="New list, e.g. Pancakes"
          maxLength={100}
          aria-label="Title of the new list"
        />
        <button type="submit" disabled={create.isPending}>Create</button>
      </form>

      {lists.isPending && <p className="muted">Loading lists…</p>}
      {lists.isError && !lists.data && <p className="problem">Could not load your lists.</p>}
      {lists.data?.length === 0 && <p className="muted">No lists yet. Create your first one above.</p>}

      <ul className="nav">
        {lists.data?.map((list) => (
          <li key={list.id}>
            <button
              className={list.id === selectedId ? 'nav-item active' : 'nav-item'}
              onClick={() => onSelect(list.id)}
            >
              <span className="nav-title">{list.title}</span>
              <span className="muted small">
                {list.checked_count} of {list.item_count} ticked
              </span>
              {list.role !== 'owner' && (
                <span className="muted small">Shared by {list.owner_username}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

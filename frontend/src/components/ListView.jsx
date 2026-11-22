import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, refreshList } from '../api'
import ItemsSection from './ItemsSection'
import MembersSection from './MembersSection'

export default function ListView({ listId, onClose }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)

  // Poll every 5 s so changes made by the other people on the list appear
  const query = useQuery({
    queryKey: ['list', listId],
    queryFn: () => api.getList(listId),
    refetchInterval: 5000,
  })

  // Close the view after the list is deleted or you left it
  const closeList = () => {
    queryClient.removeQueries({ queryKey: ['list', listId] })
    queryClient.invalidateQueries({ queryKey: ['lists'] })
    onClose()
  }

  const remove = useMutation({ mutationFn: () => api.deleteList(listId), onSuccess: closeList })

  const list = query.data
  if (!list) {
    if (query.isPending) return <section className="panel muted">Loading list…</section>
    return (
      <section className="panel">
        <p className="problem">
          {query.error?.status === 404
            ? 'This list is not available any more.'
            : 'Could not load this list.'}
        </p>
        <button onClick={closeList}>Back to my lists</button>
      </section>
    )
  }

  const isOwner = list.role === 'owner'
  const canEdit = list.role !== 'viewer'

  return (
    <section className="panel">
      <div className="list-head">
        <div>
          <h2 className="list-title">{list.title}</h2>
          <p className="muted small">
            {isOwner ? 'You own this list.' : `${list.owner_username} owns this list. You are a ${list.role}.`}
          </p>
          {list.recipe_link && (
            <a href={list.recipe_link} target="_blank" rel="noopener noreferrer">
              Open recipe
            </a>
          )}
        </div>
        {isOwner && (
          <div className="row">
            <button className="secondary small" onClick={() => setEditing(!editing)}>
              {editing ? 'Close editor' : 'Edit list'}
            </button>
            <button
              className="danger small"
              onClick={() => window.confirm('Delete this list and all its items?') && remove.mutate()}
            >
              Delete list
            </button>
          </div>
        )}
      </div>

      {editing && <EditForm list={list} onDone={() => setEditing(false)} />}

      <ItemsSection list={list} canEdit={canEdit} />
      <MembersSection list={list} isOwner={isOwner} onLeave={closeList} />
    </section>
  )
}

function EditForm({ list, onDone }) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState(list.title)
  const [link, setLink] = useState(list.recipe_link ?? '')

  const save = useMutation({
    // An empty link is sent as null, which removes the link
    mutationFn: () => api.updateList(list.id, { title: title.trim(), recipe_link: link.trim() || null }),
    onSuccess: () => {
      refreshList(queryClient, list.id)
      onDone()
    },
  })

  return (
    <form
      className="edit"
      onSubmit={(e) => {
        e.preventDefault()
        if (title.trim()) save.mutate()
      }}
    >
      <label>
        Title
        <input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        Recipe link
        <input
          type="url"
          value={link}
          placeholder="https://"
          onChange={(e) => setLink(e.target.value)}
        />
      </label>
      <div className="row">
        <button type="submit" disabled={save.isPending}>Save changes</button>
        <button type="button" className="secondary" onClick={onDone}>Cancel</button>
      </div>
    </form>
  )
}

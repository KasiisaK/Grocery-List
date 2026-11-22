import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, refreshList } from '../api'

export default function ItemsSection({ list, canEdit }) {
  const queryClient = useQueryClient()
  const key = ['list', list.id]
  const refresh = () => refreshList(queryClient, list.id)

  const [name, setName] = useState('')
  const [quantity, setQuantity] = useState('')
  const [unit, setUnit] = useState('')

  // user id -> username, to show who added each item
  const who = Object.fromEntries(list.members.map((m) => [m.user_id, m.username]))

  const add = useMutation({
    mutationFn: (body) => api.addItem(list.id, body),
    onSuccess: () => {
      refresh()
      setName('')
      setQuantity('')
      setUnit('')
    },
  })

  // Ticking an item updates the screen first, then tells the server.
  // If the server refuses, the screen is rolled back.
  const toggle = useMutation({
    mutationFn: (item) => api.updateItem(list.id, item.id, { is_checked: !item.is_checked }),
    onMutate: async (item) => {
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData(key)
      queryClient.setQueryData(key, (old) => ({
        ...old,
        items: old.items.map((i) => (i.id === item.id ? { ...i, is_checked: !i.is_checked } : i)),
      }))
      return { previous }
    },
    onError: (_error, _item, context) => queryClient.setQueryData(key, context.previous),
    onSettled: refresh,
  })

  const remove = useMutation({
    mutationFn: (item) => api.deleteItem(list.id, item.id),
    onSuccess: refresh,
  })

  function submit(event) {
    event.preventDefault()
    if (!name.trim()) return
    const body = { name: name.trim() }
    if (quantity !== '' && Number.isFinite(Number(quantity))) body.quantity = Number(quantity)
    if (unit.trim()) body.unit = unit.trim()
    add.mutate(body)
  }

  const ticked = list.items.filter((item) => item.is_checked).length

  return (
    <div className="section">
      <h3>
        Items <span className="muted small">{ticked} of {list.items.length} ticked</span>
      </h3>

      {canEdit && (
        <form className="row" onSubmit={submit}>
          <input
            className="grow"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Add an item, e.g. Sugar"
            maxLength={100}
            aria-label="Item name"
          />
          <input
            className="qty"
            type="number"
            min="0"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            placeholder="Amount"
            aria-label="Amount"
          />
          <input
            className="unit"
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
            placeholder="Unit"
            maxLength={10}
            aria-label="Unit"
          />
          <button type="submit" disabled={add.isPending}>Add item</button>
        </form>
      )}

      {list.items.length === 0 && (
        <p className="muted">
          {canEdit ? 'Nothing on this list yet. Add the first item above.' : 'Nothing on this list yet.'}
        </p>
      )}

      <ul className="items">
        {list.items.map((item) => (
          <li key={item.id} className={item.is_checked ? 'item done' : 'item'}>
            <label className="item-label">
              <input
                type="checkbox"
                checked={item.is_checked}
                disabled={!canEdit}
                onChange={() => toggle.mutate(item)}
              />
              <span className="item-name">{item.name}</span>
            </label>
            <span className="leader" aria-hidden="true" />
            {item.quantity != null && (
              <span className="amount">
                {item.quantity} {item.unit ?? ''}
              </span>
            )}
            {who[item.added_by] && <span className="by muted small">{who[item.added_by]}</span>}
            {canEdit && (
              <button
                className="icon"
                aria-label={`Delete ${item.name}`}
                onClick={() => remove.mutate(item)}
              >
                ✕
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, refreshList } from '../api'
import { useDebounce } from '../hooks/useDebounce'

export default function MembersSection({ list, isOwner, onLeave }) {
  const queryClient = useQueryClient()
  const refresh = () => refreshList(queryClient, list.id)
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })

  const [term, setTerm] = useState('')
  const [role, setRole] = useState('editor')
  const debounced = useDebounce(term.trim(), 300)

  // Search as the owner types (the API needs at least 2 characters)
  const results = useQuery({
    queryKey: ['userSearch', debounced],
    queryFn: () => api.searchUsers(debounced),
    enabled: isOwner && debounced.length >= 2,
  })
  const memberIds = new Set(list.members.map((m) => m.user_id))
  const candidates = (results.data ?? []).filter((user) => !memberIds.has(user.id))

  const add = useMutation({
    mutationFn: (user) => api.addMember(list.id, { user_id: user.id, role }),
    onSuccess: () => {
      refresh()
      setTerm('')
    },
  })

  const changeRole = useMutation({
    mutationFn: ({ userId, newRole }) => api.updateMember(list.id, userId, { role: newRole }),
    onSuccess: refresh,
  })

  // The owner removes other people; anyone else can only remove themselves (leave)
  const removeMember = useMutation({
    mutationFn: (userId) => api.removeMember(list.id, userId),
    onSuccess: (_data, userId) => (userId === me.data?.id ? onLeave() : refresh()),
  })

  return (
    <div className="section">
      <h3>People</h3>
      <ul className="people">
        {list.members.map((member) => {
          const isMe = member.user_id === me.data?.id
          return (
            <li key={member.user_id}>
              <span>
                {member.username}
                {isMe && ' (you)'}
              </span>
              {member.role === 'owner' ? (
                <span className="badge">owner</span>
              ) : isOwner ? (
                <span className="row">
                  <select
                    value={member.role}
                    aria-label={`Role of ${member.username}`}
                    onChange={(e) => changeRole.mutate({ userId: member.user_id, newRole: e.target.value })}
                  >
                    <option value="editor">editor</option>
                    <option value="viewer">viewer</option>
                  </select>
                  <button className="danger small" onClick={() => removeMember.mutate(member.user_id)}>
                    Remove
                  </button>
                </span>
              ) : (
                <span className="row">
                  <span className="badge">{member.role}</span>
                  {isMe && (
                    <button
                      className="secondary small"
                      onClick={() => window.confirm('Leave this list?') && removeMember.mutate(member.user_id)}
                    >
                      Leave list
                    </button>
                  )}
                </span>
              )}
            </li>
          )
        })}
      </ul>

      {isOwner && (
        <div className="invite">
          <div className="row">
            <input
              className="grow"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="Find people by username"
              aria-label="Find people by username"
            />
            <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role for new person">
              <option value="editor">Can edit</option>
              <option value="viewer">Can only view</option>
            </select>
          </div>
          {debounced.length >= 2 && results.data && candidates.length === 0 && (
            <p className="muted small">
              No one to add with that name. People appear here after they have opened the app once.
            </p>
          )}
          <ul className="people">
            {candidates.map((user) => (
              <li key={user.id}>
                <span>{user.username}</span>
                <button className="small" onClick={() => add.mutate(user)}>
                  Add to list
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

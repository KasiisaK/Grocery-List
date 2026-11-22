import { keycloak } from './auth'

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

async function authHeader() {
  try {
    await keycloak.updateToken(30) // refresh the token if it expires within 30 seconds
  } catch {
    keycloak.login() // the session is gone: send the user to the login page
    throw new Error('Your session expired. Redirecting to the login page.')
  }
  return { Authorization: `Bearer ${keycloak.token}` }
}

async function request(path, { method = 'GET', body } = {}) {
  const headers = await authHeader()
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const error = new Error(data.error ?? `Server returned ${response.status}`)
    error.status = response.status
    throw error
  }
  return response.status === 204 ? null : response.json()
}

// One function per backend path. Components never call fetch directly.
export const api = {
  me: () => request('/me'),
  searchUsers: (text) => request(`/users?search=${encodeURIComponent(text)}`),

  getLists: () => request('/lists'),
  createList: (body) => request('/lists', { method: 'POST', body }),
  getList: (id) => request(`/lists/${id}`),
  updateList: (id, body) => request(`/lists/${id}`, { method: 'PATCH', body }),
  deleteList: (id) => request(`/lists/${id}`, { method: 'DELETE' }),

  addMember: (id, body) => request(`/lists/${id}/members`, { method: 'POST', body }),
  updateMember: (id, userId, body) =>
    request(`/lists/${id}/members/${userId}`, { method: 'PATCH', body }),
  removeMember: (id, userId) =>
    request(`/lists/${id}/members/${userId}`, { method: 'DELETE' }),

  addItem: (id, body) => request(`/lists/${id}/items`, { method: 'POST', body }),
  updateItem: (id, itemId, body) =>
    request(`/lists/${id}/items/${itemId}`, { method: 'PATCH', body }),
  deleteItem: (id, itemId) => request(`/lists/${id}/items/${itemId}`, { method: 'DELETE' }),
}

// After a change, mark the cached data as stale so it is fetched again.
export function refreshList(queryClient, listId) {
  queryClient.invalidateQueries({ queryKey: ['list', listId] })
  queryClient.invalidateQueries({ queryKey: ['lists'] })
}

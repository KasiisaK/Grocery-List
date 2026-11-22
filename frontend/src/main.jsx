import React from 'react'
import { createRoot } from 'react-dom/client'
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { initAuth } from './auth'
import App from './App.jsx'
import './styles.css'

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    // Every failed write ends up in the banner shown by App.jsx
    onError: (error) =>
      window.dispatchEvent(new CustomEvent('app-error', { detail: error.message })),
  }),
  defaultOptions: { queries: { retry: 1 } },
})

const root = createRoot(document.getElementById('root'))

// Log in first, then render. (Doing this outside React avoids Keycloak being
// initialised twice by React's development double-render.)
initAuth()
  .then(() =>
    root.render(
      <React.StrictMode>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </React.StrictMode>
    )
  )
  .catch(() => {
    root.render(<p className="fatal">Could not reach the login server. Is Keycloak running?</p>)
  })

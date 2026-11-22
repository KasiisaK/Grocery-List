import { useEffect, useState } from 'react'

// Returns `value`, but only after it has stopped changing for `delay` ms.
// Typing "Bob" then makes one search request instead of three.
export function useDebounce(value, delay = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

import { Navigate, useSearch } from '@tanstack/react-router'

// Task #32A: the real sign-in lives on the portal gate ('/'). Old /login
// links redirect there (route beforeLoad), keeping the invite token (#31).
// This component is a belt-and-suspenders fallback for the same redirect.
export function LoginPage() {
  const { invite } = useSearch({ from: '/login' })
  return <Navigate to="/" search={{ invite }} />
}
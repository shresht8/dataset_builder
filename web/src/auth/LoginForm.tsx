// Dev-login UI (GL-1-9): email in, session cookie out. Kept isolated from
// the rest of the app shell so GL-3-11 can swap this for an Entra OIDC
// redirect button without touching AuthContext's consumers.
import { useState, type FormEvent } from 'react'
import { useAuth } from './AuthContext'

export function LoginForm() {
  const { login, error } = useAuth()
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await login(email)
    } catch {
      // error is surfaced via useAuth().error
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1>Groundline</h1>
        <p className="login-hint">Dev login — enter your email to sign in.</p>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          required
          autoFocus
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
        />
        {error && <p className="login-error">{error}</p>}
        <button type="submit" disabled={submitting || !email}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

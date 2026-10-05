// Login page: "Sign in with Microsoft" when the API has Entra SSO enabled
// (GL-3-11), plus the dev-login form (GL-1-9) while dev login is on. Both can
// show at once in dev. Isolated from the app shell: AuthContext's consumers
// are unaffected by which sign-in method was used.
import { useEffect, useState, type FormEvent } from 'react'
import { useAuth } from './AuthContext'
import { fetchAuthConfig, startSsoSignIn, type AuthConfig } from './authConfig'
import './auth.css'

export function LoginForm() {
  const { login, error } = useAuth()
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void fetchAuthConfig().then(setConfig)
  }, [])

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

  if (config === null) return <p className="loading">Loading…</p>

  return (
    <div className="login-page">
      <div className="login-form">
        <h1>Groundline</h1>
        {config.sso && (
          <button type="button" className="sso-button" autoFocus onClick={startSsoSignIn}>
            Sign in with Microsoft
          </button>
        )}
        {config.sso && config.dev_login && <p className="login-divider">or</p>}
        {config.dev_login && (
          <form onSubmit={handleSubmit}>
            <p className="login-hint">Dev login — enter your email to sign in.</p>
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              required
              autoFocus={!config.sso}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
            />
            {error && <p className="login-error">{error}</p>}
            <button type="submit" disabled={submitting || !email}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}
        {!config.sso && !config.dev_login && <p className="login-error">No sign-in method is enabled.</p>}
      </div>
    </div>
  )
}

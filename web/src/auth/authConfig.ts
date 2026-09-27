// Which sign-in options the API offers (GL-3-11): dev login, Entra SSO, or
// both. Kept inside auth/ so swapping sign-in never touches the rest of the app.
export interface AuthConfig {
  dev_login: boolean
  sso: boolean
}

// If the config can't be read (older API), fall back to the dev-login form,
// which is what the app offered before SSO existed.
const FALLBACK: AuthConfig = { dev_login: true, sso: false }

export async function fetchAuthConfig(): Promise<AuthConfig> {
  try {
    const response = await fetch('/v1/auth/config', { credentials: 'include' })
    if (!response.ok) return FALLBACK
    return (await response.json()) as AuthConfig
  } catch {
    return FALLBACK
  }
}

// Full-page navigation: the API redirects to the IdP and, after sign-in,
// back to `/` with the session cookie set.
export function startSsoSignIn(): void {
  window.location.assign('/v1/auth/sso/login')
}

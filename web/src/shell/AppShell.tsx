// Post-login chrome: current user/role and sign-out, wrapping the routed
// pages (dataset list, dataset placeholder).
import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title">Groundline</span>
        {user && (
          <span className="app-user">
            {user.email} &middot; {user.role}
            <button type="button" onClick={() => void logout()}>
              Sign out
            </button>
          </span>
        )}
      </header>
      <main>{children}</main>
    </div>
  )
}

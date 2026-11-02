import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { LoginForm } from './auth/LoginForm'
import { DatasetListPage } from './datasets/DatasetListPage'
import { DatasetPage } from './datasets/DatasetPage'
import { TokensPage } from './tokens/TokensPage'
import { AppShell } from './shell/AppShell'

function AuthGate() {
  const { user, loading } = useAuth()

  if (loading) return <p className="loading">Loading…</p>
  if (!user) return <LoginForm />

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DatasetListPage />} />
        <Route path="/datasets/:datasetId" element={<DatasetPage />} />
        <Route path="/tokens" element={<TokensPage />} />
      </Routes>
    </AppShell>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App

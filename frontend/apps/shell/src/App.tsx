import { useEffect, useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import DashboardRemote from 'dashboard/App';
import CustomersRemote from 'customers/App';
import SubscriptionsRemote from 'subscriptions/App';

type Customer = {
  id: string;
  tenant_id: string;
  user_id: string;
  name: string;
  email: string;
  product: string;
  subscription_status: string;
  status?: string;
  trial_start_date: string;
  created_at: string;
};

function Login({
  onLogin,
}: {
  onLogin: (email: string, password: string) => Promise<string | undefined>;
}) {
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  return (
    <div className="login-page">
      <form
        className="login-card"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const form = new FormData(e.currentTarget);
            setError((await onLogin(String(form.get('email')), String(form.get('password')))) || '');
          } catch {
            setError('Unable to reach the server');
          }
        }}
      >
        <div className="brand login-brand">
          <span className="brand-mark">OP</span>
          <div>
            <strong>Onboarding</strong>
            <small>PLATFORM</small>
          </div>
        </div>
        <h1>Welcome back</h1>
        <p className="muted">Sign in to manage onboarded customers.</p>
        <label>
          Email
          <input name="email" type="email" required placeholder="admin@reconq.com" />
        </label>
        <label>
          Password
          <span className="password-field">
            <input
              name="password"
              type={showPassword ? 'text' : 'password'}
              required
              placeholder="••••••••"
            />
            <button
              type="button"
              className="password-toggle"
              onClick={() => setShowPassword((visible) => !visible)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.1A10.8 10.8 0 0 1 12 4.9c5.2 0 9.2 4.6 10 7.1a12.8 12.8 0 0 1-3.1 4.8M6.2 6.2C3.9 7.8 2.4 10.2 2 12c.8 2.5 4.8 7.1 10 7.1 1 0 2-.2 2.9-.5" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
                  <circle cx="12" cy="12" r="2.5" />
                </svg>
              )}
            </button>
          </span>
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary full" type="submit">
          Log in
        </button>
      </form>
    </div>
  );
}

function Layout({ children, onLogout }: { children: React.ReactNode; onLogout: () => void }) {
  const icon = (path: string) => (
    <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={path} />
    </svg>
  );
  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">OP</span>
          <div>
            <strong>Onboarding</strong>
            <small>PLATFORM</small>
          </div>
        </div>
        <nav>
          <NavLink to="/dashboard">
            {icon('M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z')} <span>Dashboard</span>
          </NavLink>
          <NavLink to="/customers">
            {icon('M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75')} <span>Customers</span>
          </NavLink>
          <NavLink to="/subscriptions">
            {icon('M4 7h16M4 12h16M4 17h16')} <span>Subscriptions</span>
          </NavLink>
        </nav>
      </aside>
      <section className="workspace">
        <header>
          <div className="header-actions">
            <span className="role">Administrator</span>
            <button className="logout-button" onClick={onLogout} type="button">
              <span className="avatar">AD</span>
              <span>Logout</span>
            </button>
          </div>
        </header>
        <div className="content">{children}</div>
      </section>
    </main>
  );
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [loggedIn, setLoggedIn] = useState(() =>
    Boolean(sessionStorage.getItem('onboarding_admin')),
  );
  const [rows, setRows] = useState<Customer[]>([]);
  useEffect(() => {
    if (!loggedIn) return;
    const load = async () => {
      let token = sessionStorage.getItem('onboarding_admin');
      let response = await fetch('/api/onboarding/customers', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.status === 401) {
        const refreshToken = sessionStorage.getItem('onboarding_refresh');
        const refreshed = await fetch('/api/admin/refresh', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!refreshed.ok) { setLoggedIn(false); return; }
        token = (await refreshed.json()).access_token;
        sessionStorage.setItem('onboarding_admin', token!);
        response = await fetch('/api/onboarding/customers', {
          headers: { Authorization: `Bearer ${token}` },
        });
      }
      if (response.ok) setRows(await response.json());
    };
    load();
  }, [loggedIn]);
  if (!loggedIn)
    return (
      <Login
        onLogin={async (email, password) => {
          const response = await fetch('/api/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
          });
          if (!response.ok) return 'Invalid email or password';
          const { access_token, refresh_token } = await response.json();
          sessionStorage.setItem('onboarding_admin', access_token);
          sessionStorage.setItem('onboarding_refresh', refresh_token);
          setLoggedIn(true);
          window.location.assign('/dashboard');
        }}
      />
    );
  const detail = location.pathname.match(/^\/customers\/(.+)$/);
  const remoteRows = rows as unknown as Array<Record<string, string>>;
  const page =
    location.pathname === '/subscriptions' ? (
      <SubscriptionsRemote />
    ) : location.pathname === '/customers' ? (
      <CustomersRemote page="customers" rows={remoteRows} onNavigate={navigate} onDeleted={(id) => setRows((current) => current.filter((customer) => customer.id !== id))} />
    ) : detail ? (
      <CustomersRemote page="details" rows={remoteRows} onNavigate={navigate} onDeleted={(id) => setRows((current) => current.filter((customer) => customer.id !== id))} />
    ) : (
      <DashboardRemote rows={remoteRows} />
    );
  return (
    <Layout
      onLogout={() => {
        sessionStorage.removeItem('onboarding_admin');
        sessionStorage.removeItem('onboarding_refresh');
        setLoggedIn(false);
      }}
    >
      {page}
    </Layout>
  );
}

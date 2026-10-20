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
type Project = { id: string; key: string; name: string };
type SubscriptionPlan = { id: string; name: string; product: string; guardrails?: Record<string, number | null | string> };

function Login({ onLogin }: { onLogin: (email: string, password: string) => Promise<string | undefined> }) {
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  return (
    <div className="login-page">
      <form className="login-card" onSubmit={async (e) => {
        e.preventDefault();
        const form = new FormData(e.currentTarget);
        try { setError((await onLogin(String(form.get('email')), String(form.get('password')))) || ''); }
        catch { setError('Unable to reach the server'); }
      }}>
        <div className="brand login-brand">
          <span className="brand-mark">OP</span>
          <div>
            <strong>Onboarding</strong>
            <small>PLATFORM</small>
          </div>
        </div>
        <h1>Welcome back</h1>
        <p className="muted">Sign in to manage onboarded customers.</p>
        <label>Email<input name="email" type="email" required placeholder="admin@reconq.com" /></label>
        <label>Password<span className="password-field"><input name="password" type={showPassword ? 'text' : 'password'} required placeholder="••••••••" /><button type="button" className="password-toggle" onClick={() => setShowPassword((visible) => !visible)}>{showPassword ? 'Hide' : 'Show'}</button></span></label>
        {error && <p className="error">{error}</p>}
        <button className="primary full" type="submit">Log in</button>
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
          <NavLink to="/guardrails">
            {icon('M12 3l8 4v5c0 4.8-3.4 7.8-8 9-4.6-1.2-8-4.2-8-9V7l8-4Zm-3 9 2 2 4-4')} <span>Guardrails</span>
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
  const [loggedIn, setLoggedIn] = useState(() => Boolean(sessionStorage.getItem('onboarding_admin')));
  const [rows, setRows] = useState<Customer[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  useEffect(() => {
    if (!loggedIn) return;
    const load = async () => {
      let token = sessionStorage.getItem('onboarding_admin');
      const projectResponse = await fetch('/api/projects', { headers: { Authorization: `Bearer ${token}` } });
      if (projectResponse.ok) setProjects(await projectResponse.json());
      const plansResponse = await fetch('/api/subscription-plans', { headers: { Authorization: `Bearer ${token}` } });
      if (plansResponse.ok) setPlans(await plansResponse.json());
      const customerId = location.pathname.match(/^\/customers\/(.+)$/)?.[1];
      const customerUrl = customerId
        ? `/api/onboarding/customers/${encodeURIComponent(customerId)}`
        : '/api/onboarding/customers';
      let response = await fetch(customerUrl, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.status === 401) {
        const refreshToken = sessionStorage.getItem('onboarding_refresh');
        const refreshed = await fetch('/api/admin/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: refreshToken }) });
        if (!refreshed.ok) { setLoggedIn(false); return; }
        token = (await refreshed.json()).access_token;
        sessionStorage.setItem('onboarding_admin', token!);
        response = await fetch(customerUrl, {
          headers: { Authorization: `Bearer ${token}` },
        });
      }
      if (response.ok) {
        const data = await response.json();
        setRows(customerId ? [data] : data);
      }
    };
    load();
    const refresh = window.setInterval(load, 10000);
    window.addEventListener('focus', load);
    return () => { window.clearInterval(refresh); window.removeEventListener('focus', load); };
  }, [loggedIn, location.pathname]);
  if (!loggedIn)
    return (
      <Login
        onLogin={async (email, password) => {
          const response = await fetch('/api/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
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
  const guardrailsCustomerId = new URLSearchParams(location.search).get('customer_id');
  const remoteRows = rows as unknown as Array<Record<string, string>>;
  const page =
    location.pathname === '/subscriptions' ? (
      <SubscriptionsRemote />
    ) : location.pathname === '/guardrails' ? (
      <Projects projects={projects} onNavigate={navigate} />
    ) : location.pathname === '/guardrails/reconq' ? (
      <Guardrails projectKey="reconq" projects={projects} plans={plans} />
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

function Projects({ projects, onNavigate }: { projects: Project[]; onNavigate: (path: string) => void }) {
  const reconq = projects.find((project) => project.key.toLowerCase() === 'reconq') || { id: 'reconq', key: 'reconq', name: 'ReconQ' };
  return <div><div className="page-title"><div><p className="eyebrow">ONBOARDING PLATFORM / PROJECTS</p><h1>Projects</h1><p className="muted">Choose a project to configure its onboarding controls.</p></div></div><div className="project-grid"><button className="project-card" type="button" onClick={() => onNavigate('/guardrails/reconq')}><span className="project-card-icon">◈</span><strong>{reconq.name}</strong><small>AI-powered payment reconciliation</small><small>{reconq.key}</small></button></div></div>;
}

function Guardrails({ projectKey, projects, plans }: { projectKey: string; projects: Project[]; plans: SubscriptionPlan[] }) {
  const projectPlans = plans.filter((plan) => !plan.product || plan.product.toLowerCase() === 'reconq');
  const [planId, setPlanId] = useState('');
  const [value, setValue] = useState<Record<string, number | null | string | boolean>>({ scope: 'tenant' });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const plan = projectPlans.find((item) => item.id === planId) || projectPlans[0];
    if (!plan) return;
    if (!planId) {
      setPlanId(plan.id);
      setValue(plan.guardrails || { scope: 'tenant', alerts_enabled: true, notifications_enabled: true });
    }
  }, [planId, plans.length]);
  const groups = [
    ['Subscription capacity', [['max_legal_entities', 'Legal entities'], ['max_users_per_entity', 'Users / members per entity'], ['max_erp_connectors', 'ERP connectors'], ['alerts_enabled', 'Alerts enabled'], ['notifications_enabled', 'Notifications enabled']]],
    ['Monthly usage', [['max_reconciliations', 'Reconciliations'], ['max_invoices', 'Invoices'], ['max_bank_statements', 'Bank statements'], ['max_purchase_orders', 'Purchase orders'], ['max_credit_notes', 'Credit notes'], ['max_grns', 'GRNs'], ['max_erp_syncs', 'ERP syncs'], ['max_erp_writebacks', 'ERP write-backs'], ['max_ai_reads', 'AI read queries'], ['max_ai_writes', 'AI write queries']]],
  ] as const;
  const save = async () => {
    setError(''); setSaved(false);
    const numericKeys = ['max_legal_entities', 'max_users_per_entity', 'max_erp_connectors', 'max_reconciliations', 'max_invoices', 'max_bank_statements', 'max_purchase_orders', 'max_credit_notes', 'max_grns', 'max_erp_syncs', 'max_erp_writebacks', 'max_ai_reads', 'max_ai_writes'];
    const savedValue = { ...value };
    numericKeys.forEach((key) => { if (savedValue[key] === '') savedValue[key] = 0; });
    const response = await fetch(`/api/subscription-plans/${planId}/guardrails`, { method: 'PATCH', headers: { Authorization: `Bearer ${sessionStorage.getItem('onboarding_admin')}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ guardrails: { ...savedValue, limit_types: { max_legal_entities: 'subscription', max_users_per_entity: 'subscription', max_erp_connectors: 'subscription', alerts_enabled: 'subscription', notifications_enabled: 'subscription', max_reconciliations: 'monthly', max_invoices: 'monthly', max_bank_statements: 'monthly', max_purchase_orders: 'monthly', max_credit_notes: 'monthly', max_grns: 'monthly', max_erp_syncs: 'monthly', max_erp_writebacks: 'monthly', max_ai_reads: 'monthly', max_ai_writes: 'monthly' } } }) });
    if (!response.ok) { setError((await response.json()).detail || 'Unable to save guardrails'); return; }
    setSaved(true);
  };
  return <div><div className="page-title"><div><p className="eyebrow">ONBOARDING PLATFORM / GOVERNANCE / RECONQ</p><h1>Guardrails</h1><p className="muted">Set tenant-wide usage limits and feature controls for each subscription plan.</p></div><span className="status-pill">● Plan-based</span></div><div className="guardrail-card"><div className="guardrail-toolbar"><div className="project-context"><span>Project</span><strong>ReconQ</strong><small>{projectKey}</small></div><label><span>Subscription plan</span><select value={planId} onChange={(e) => { setPlanId(e.target.value); const plan = projectPlans.find((item) => item.id === e.target.value); setValue(plan?.guardrails || { scope: 'tenant', alerts_enabled: true, notifications_enabled: true }); }}>{projectPlans.map((plan) => <option key={plan.id} value={plan.id}>{plan.name}</option>)}</select></label><div className="scope-summary"><span>Applies to</span><strong>Entire tenant</strong><small>All legal entities share this plan policy</small></div><div className="guardrail-help"><strong>How controls work</strong><small>Capacity fields persist with the plan. Usage fields reset monthly.</small></div></div>{groups.map(([title, fields]) => <section className="guardrail-section" key={title}><div className="section-heading"><h2>{title}</h2><span>{title === 'Subscription capacity' ? 'Plan capacity' : 'Monthly usage'}</span></div><div className="guardrail-grid">{fields.map(([key, label]) => <label className="guardrail-field" key={key}><span>{label}</span><div className="limit-input">{key.endsWith('_enabled') ? <button type="button" className={`toggle ${value[key] !== false ? 'on' : ''}`} aria-pressed={value[key] !== false} onClick={() => setValue({ ...value, [key]: value[key] === false })}><span className="toggle-track"><span className="toggle-knob" /></span><small>{value[key] !== false ? 'On' : 'Off'}</small></button> : <><input type="number" min="0" disabled={value[key] == null} placeholder="Unlimited" value={value[key] as number | string | undefined ?? ''} onChange={(e) => setValue({ ...value, [key]: e.target.value === '' ? '' : Number(e.target.value) })} /><button type="button" className={`toggle ${value[key] == null ? 'on' : ''}`} aria-pressed={value[key] == null} onClick={() => setValue({ ...value, [key]: value[key] == null ? 0 : null })}><span className="toggle-track"><span className="toggle-knob" /></span><small>Unlimited</small></button><small>per month</small></>}</div></label>)}</div></section>)}<div className="guardrail-footer">{error && <p className="error">{error}</p>}{saved && <p className="success">Guardrails saved successfully.</p>}<button className="primary save-guardrails" type="button" onClick={save} disabled={!planId}>Save guardrails</button></div></div></div>;
}

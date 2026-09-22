type Customer = {
  id: string;
  name: string;
  status?: string;
  trial_end_date: string;
  created_at: string;
};
const left = (v: string) => Math.max(0, Math.ceil((new Date(v).getTime() - Date.now()) / 86400000));
const date = (v: string) =>
  new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).format(
    new Date(v),
  );
export default function Dashboard({ rows }: { rows: Array<Record<string, string>> }) {
  const customers = rows as unknown as Customer[];
  const active = customers.filter((c) => c.status !== 'EXPIRED');
  const expired = customers.filter((c) => c.status === 'EXPIRED');
  const soon = active.filter((c) => left(c.trial_end_date) <= 7);
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">ONBOARDING PLATFORM / OVERVIEW</p>
          <h1>Dashboard</h1>
          <p className="muted">A high-level view of onboarding activity.</p>
        </div>
      </div>
      <div className="kpis">
        {[
          ['Total Customers', customers.length],
          ['Active Trials', active.length],
          ['Trials Expiring Soon', soon.length],
          ['Expired Trials', expired.length],
        ].map(([label, value]) => (
          <div className="kpi" key={String(label)}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>Current platform status</small>
          </div>
        ))}
      </div>
      <div className="card">
        <h2>Recent Activity</h2>
        {customers.slice(0, 5).map((c) => (
          <div className="activity" key={c.id}>
            <span className="activity-dot" />
            <div>
              <strong>{c.name} registered</strong>
              <small>
                {date(c.created_at)} ·{' '}
                {c.status === 'EXPIRED' ? 'Trial expired' : 'Free trial started'}
              </small>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

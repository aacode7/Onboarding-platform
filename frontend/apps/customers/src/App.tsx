import { useState } from 'react';
type Customer = {
  id: string;
  tenant_id: string;
  name: string;
  email: string;
  product: string;
  status?: string;
  subscription_status?: string;
  trial_start_date: string;
  trial_end_date: string;
  created_at: string;
};
const date = (v: string) =>
  new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).format(
    new Date(v),
  );
const left = (v: string) => Math.max(0, Math.ceil((new Date(v).getTime() - Date.now()) / 86400000));
function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function Details({ c }: { c: Customer }) {
  return (
    <>
      <Title text={c.name} sub="Complete onboarding information." />
      <div className="grid">
        <div className="card">
          <h2>Customer Information</h2>
          <Info label="Customer Name" value={c.name} />
          <Info label="Email" value={c.email} />
          <Info label="Customer ID" value={c.id} />
          <Info label="Tenant ID" value={c.tenant_id} />
          <Info label="Product" value={c.product} />
          <Info label="Created At" value={date(c.created_at)} />
        </div>
        <div className="card">
          <h2>Trial Information</h2>
          <Info label="Trial Status" value={c.status || c.subscription_status || 'TRIAL'} />
          <Info label="Trial Start Date" value={date(c.trial_start_date)} />
          <Info label="Trial End Date" value={date(c.trial_end_date)} />
          <Info label="Days Remaining" value={String(left(c.trial_end_date))} />
        </div>
      </div>
    </>
  );
}
function Title({ text, sub }: { text: string; sub: string }) {
  return (
    <div className="page-title">
      <div>
        <p className="eyebrow">ONBOARDING PLATFORM / CUSTOMERS</p>
        <h1>{text}</h1>
        <p className="muted">{sub}</p>
      </div>
    </div>
  );
}
export default function Customers({
  page,
  rows,
  onNavigate,
}: {
  page: string;
  rows: Array<Record<string, string>>;
  onNavigate: (path: string) => void;
}) {
  const customers = rows as unknown as Customer[];
  if (page === 'details') {
    const c = customers.find((item) => item.id === location.pathname.split('/').pop());
    return c ? <Details c={c} /> : <p className="empty">Customer not found.</p>;
  }
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('ALL');
  const shown = customers.filter(
    (c) =>
      (status === 'ALL' || c.status === status) &&
      `${c.name} ${c.email} ${c.product}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <Title text="Customers" sub="View and search all onboarded customers." />
      <div className="card">
        <div className="toolbar">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search customers..."
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="ALL">All statuses</option>
            <option value="TRIAL">Trial</option>
            <option value="EXPIRING_SOON">Expiring soon</option>
            <option value="EXPIRED">Expired</option>
          </select>
        </div>
        <table>
          <thead>
            <tr>
              {['Customer', 'Email', 'Product', 'Status', 'Trial End', 'Action'].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr className="clickable" key={c.id} onClick={() => onNavigate(`/customers/${c.id}`)}>
                <td>
                  <strong>{c.name}</strong>
                  <small>{c.id}</small>
                </td>
                <td>{c.email}</td>
                <td>{c.product}</td>
                <td>
                  <span className={`badge ${c.status === 'EXPIRED' ? 'past-due' : c.status === 'EXPIRING_SOON' ? 'expiring-soon' : 'trial'}`}>
                    {c.status === 'EXPIRING_SOON' ? 'EXPIRING SOON' : c.status}
                  </span>
                </td>
                <td>{date(c.trial_end_date)}</td>
                <td>
                  <button className="link-button">View</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!shown.length && <p className="empty">No customers found.</p>}
      </div>
    </>
  );
}

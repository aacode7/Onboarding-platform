import { useEffect, useState } from 'react';

type Plan = { id: string; name: string; price: number; annual_price: number; currency: string; description: string; features: string; product: string; popular: boolean; };

export default function Subscriptions() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Plan | null>(null);
  const [error, setError] = useState('');
  const [products, setProducts] = useState<string[]>([]);
  const [selectedProduct, setSelectedProduct] = useState<string>('');
  const [featureRows, setFeatureRows] = useState<string[]>(['']);
  const token = sessionStorage.getItem('onboarding_admin');
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  const projectNames = [...new Set([...products, ...plans.map((plan) => plan.product).filter((product) => product && product !== 'Application')])];

  useEffect(() => {
    fetch('/api/subscription-products', { headers }).then((response) => response.ok ? response.json() : Promise.reject()).then((items: string[]) => setProducts(items.filter((product) => product !== 'Application'))).catch(() => setError('Unable to load products'));
    fetch('/api/subscription-plans', { headers })
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then(setPlans)
      .catch(() => setError('Unable to load subscription plans'));
  }, []);

  const deletePlan = async (plan: Plan) => {
    if (!window.confirm(`Delete the ${plan.name} plan permanently?`)) return;
    const response = await fetch(`/api/subscription-plans/${plan.id}`, { method: 'DELETE', headers });
    if (!response.ok) { setError((await response.json()).detail || 'Unable to delete subscription plan'); return; }
    setPlans((current) => current.filter((item) => item.id !== plan.id));
  };

  return (
    <>
      <div className="subscriptions-page">
      <div className="page-title">
        <div>
          <p className="eyebrow">ONBOARDING PLATFORM / BILLING</p>
          <h1>Subscription plans</h1>
          <p className="muted">Manage the plans available to your customers.</p>
        </div>
      </div>
      <div className="project-grid">
        {projectNames.map((product) => <button type="button" className={`project-card ${selectedProduct === product ? 'active' : ''}`} key={product} onClick={() => setSelectedProduct(product)}>
          <span className="project-card-icon">⌂</span><strong>{product}</strong><small>{plans.filter((plan) => plan.product === product).length} subscription plans</small>
        </button>)}
      </div>

      {!selectedProduct && <p className="muted project-hint">Select a project to view or create its subscription plans.</p>}
      {selectedProduct && !formOpen && <button className="primary new-plan-button" type="button" onClick={() => { setEditing(null); setFeatureRows(['']); setFormOpen(true); }}>Create subscription plan</button>}

      {formOpen && (
        <form className="card plan-form" onKeyDown={(event) => { if (event.key === 'Enter' && (event.target as HTMLElement).tagName !== 'TEXTAREA') event.preventDefault(); }} onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const payload = {
            name: String(data.get('name')),
            price: Number(data.get('price')),
            annual_price: Number(data.get('annual_price')),
            currency: String(data.get('currency')),
            product: String(data.get('product')),
            description: String(data.get('description')),
            features: featureRows.filter(Boolean).join('\n'),
            popular: data.get('popular') === 'on',
          };
          fetch(editing ? `/api/subscription-plans/${editing.id}` : '/api/subscription-plans', { method: editing ? 'PUT' : 'POST', headers, body: JSON.stringify(payload) }).then((response) => response.ok ? response.json() : Promise.reject())
            .then((plan) => setPlans((current) => editing ? current.map((item) => item.id === plan.id ? plan : item) : [...current, plan]))
            .catch(() => setError('Unable to save subscription plan'));
          event.currentTarget.reset();
          setEditing(null);
          setFormOpen(false);
        }}>
          <h2>{editing ? 'Edit plan' : 'New plan'}</h2>
          <div className="plan-form-grid">
            <label>Plan name<input name="name" required defaultValue={editing?.name} placeholder="Business" /></label>
            <label>Monthly price<input name="price" required type="number" min="0" step="0.01" defaultValue={editing?.price} placeholder="99" /></label>
            <label>Annual price<input name="annual_price" required type="number" min="0" step="0.01" defaultValue={editing?.annual_price} placeholder="999" /></label>
            <label>Project<input value={editing?.product || selectedProduct} readOnly /><input type="hidden" name="product" value={editing?.product || selectedProduct} /></label>
            <label>Currency<input name="currency" required defaultValue={editing?.currency || 'INR'} placeholder="USD" /></label>
            <label>Description<input name="description" required defaultValue={editing?.description} placeholder="For established teams" /></label>
            <label>Features<div className="feature-editor">{featureRows.map((feature, index) => <div className="feature-row" key={index}><input name="features" required value={feature} placeholder="Unlimited accounts" onChange={(event) => setFeatureRows((current) => current.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} />{featureRows.length > 1 && <button type="button" onClick={() => setFeatureRows((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Remove</button>}</div>)}<button type="button" className="add-feature" onClick={() => setFeatureRows((current) => [...current, ''])}>+ Add feature</button></div></label>
            <label className="checkbox-field"><input name="popular" type="checkbox" defaultChecked={editing?.popular} /> Mark as most popular</label>
          </div>
          <div className="form-actions"><button type="button" onClick={() => { setEditing(null); setFormOpen(false); }}>Cancel</button><button className="primary" type="submit">Save plan</button></div>
        </form>
      )}

      {error && <p className="error">{error}</p>}

      {selectedProduct && <div className="plan-grid">
        {plans.filter((plan) => !selectedProduct || plan.product === selectedProduct).map((plan) => <article className="card plan-card" key={plan.id}>
          <div className="plan-card-header"><h2>{plan.name}</h2><span className="badge trial">{plan.popular ? 'Most popular' : 'Active'}</span></div>
          <p>{plan.description}</p>
          <p className="plan-product">Product: <strong>{plan.product || 'Unassigned'}</strong></p>
          <strong className="plan-price">{plan.currency} {plan.price}<small>/mo</small></strong>
          <div className="plan-features">{plan.features.split(/\n+/).map((feature) => feature.replace(/^\s*[•✓*-]\s*/, '')).filter(Boolean).map((feature) => <span key={feature}>✓ {feature.trim()}</span>)}</div>
          <div className="plan-card-actions"><button type="button" onClick={() => { setEditing(plan); setFeatureRows(plan.features.split('\n').map((feature) => feature.replace(/^\s*[•✓*-]\s*/, ''))); setFormOpen(true); }}>Edit plan</button><button type="button" className="danger" onClick={() => deletePlan(plan)}>Delete</button></div>
        </article>)}
      </div>}
      </div>
    </>
  );
}

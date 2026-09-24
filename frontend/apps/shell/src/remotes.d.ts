declare module 'dashboard/App' {
  import type { ComponentType } from 'react';
  const Dashboard: ComponentType<{ rows: Array<Record<string, string>> }>;
  export default Dashboard;
}
declare module 'customers/App' {
  import type { ComponentType } from 'react';
  const Customers: ComponentType<{
    page: string;
    rows: Array<Record<string, string>>;
    onNavigate: (path: string) => void;
    onDeleted: (id: string) => void;
  }>;
  export default Customers;
}
declare module 'subscriptions/App' {
  import type { ComponentType } from 'react';
  const Subscriptions: ComponentType;
  export default Subscriptions;
}

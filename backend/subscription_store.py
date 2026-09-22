from datetime import datetime

from app.domain.models import Plan, Subscription, SubscriptionStatus


class PostgresSubscriptionStore:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def initialize(self):
        with self.connection_factory() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS subscription_plans (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, price_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL, entitlements TEXT[] NOT NULL DEFAULT '{}',
                    description TEXT NOT NULL DEFAULT ''
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
                    trial_end_at TIMESTAMPTZ, period_end_at TIMESTAMPTZ,
                    canceled_at TIMESTAMPTZ
                )
            ''')
            conn.commit()

    def save_plan(self, plan: Plan):
        with self.connection_factory() as conn:
            conn.execute('''INSERT INTO subscription_plans
                (id, name, price_minor, currency, entitlements, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,
                price_minor=EXCLUDED.price_minor, currency=EXCLUDED.currency,
                entitlements=EXCLUDED.entitlements, description=EXCLUDED.description''',
                (plan.id, plan.name, plan.price_minor, plan.currency,
                 list(plan.entitlements), plan.description))
            conn.commit()

    def get_plan(self, plan_id):
        with self.connection_factory() as conn:
            row = conn.execute('SELECT * FROM subscription_plans WHERE id=%s', (plan_id,)).fetchone()
        return self._plan(row) if row else None

    def list_plans(self):
        with self.connection_factory() as conn:
            return [self._plan(row) for row in conn.execute('SELECT * FROM subscription_plans ORDER BY id')]

    def save_subscription(self, subscription: Subscription):
        with self.connection_factory() as conn:
            conn.execute('''INSERT INTO subscriptions
                (id, customer_id, plan_id, status, created_at, trial_end_at, period_end_at, canceled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''', self._subscription_values(subscription))
            conn.commit()

    def get_subscription(self, subscription_id):
        with self.connection_factory() as conn:
            row = conn.execute('SELECT * FROM subscriptions WHERE id=%s', (subscription_id,)).fetchone()
        return self._subscription(row) if row else None

    def list_customer_subscriptions(self, customer_id):
        with self.connection_factory() as conn:
            return [self._subscription(row) for row in conn.execute(
                'SELECT * FROM subscriptions WHERE customer_id=%s ORDER BY created_at DESC', (customer_id,))]

    def update_subscription(self, subscription: Subscription):
        with self.connection_factory() as conn:
            conn.execute('''UPDATE subscriptions SET plan_id=%s, status=%s,
                trial_end_at=%s, period_end_at=%s, canceled_at=%s WHERE id=%s''',
                (subscription.plan_id, subscription.status.value, subscription.trial_end_at,
                 subscription.period_end_at, subscription.canceled_at, subscription.id))
            conn.commit()

    def list_due_subscriptions(self, at: datetime):
        with self.connection_factory() as conn:
            return [self._subscription(row) for row in conn.execute('''SELECT * FROM subscriptions
                WHERE status IN ('trialing', 'active') AND
                (trial_end_at <= %s OR period_end_at <= %s)''', (at, at))]

    def has_open_subscription(self, customer_id):
        with self.connection_factory() as conn:
            return conn.execute('''SELECT 1 FROM subscriptions
                WHERE customer_id=%s AND status IN ('trialing', 'active', 'paused') LIMIT 1''',
                (customer_id,)).fetchone() is not None

    def has_entitlement(self, customer_id, key, at):
        with self.connection_factory() as conn:
            return conn.execute('''SELECT 1 FROM subscriptions s
                JOIN subscription_plans p ON p.id=s.plan_id
                WHERE s.customer_id=%s AND s.status IN ('trialing', 'active')
                AND %s = ANY(p.entitlements)
                AND (s.trial_end_at IS NULL OR s.trial_end_at > %s)
                AND (s.period_end_at IS NULL OR s.period_end_at > %s) LIMIT 1''',
                (customer_id, key, at, at)).fetchone() is not None

    @staticmethod
    def _plan(row):
        return Plan(row['id'], row['name'], row['price_minor'], row['currency'],
                    frozenset(row['entitlements']), row['description'])

    @staticmethod
    def _subscription(row):
        return Subscription(row['id'], row['customer_id'], row['plan_id'],
                            SubscriptionStatus(row['status']), row['created_at'],
                            row['trial_end_at'], row['period_end_at'], row['canceled_at'])

    @staticmethod
    def _subscription_values(subscription):
        return (subscription.id, subscription.customer_id, subscription.plan_id,
                subscription.status.value, subscription.created_at, subscription.trial_end_at,
                subscription.period_end_at, subscription.canceled_at)

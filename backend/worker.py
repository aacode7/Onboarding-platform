import asyncio
import logging
import os
import hashlib
import hmac
import json
import httpx
from datetime import datetime, timezone

from app.scheduler import SubscriptionScheduler
from app.services.subscription_service import SubscriptionService

from .main import connection, initialize_database_with_retry
from .subscription_store import PostgresSubscriptionStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
INTERVAL_SECONDS = int(os.getenv('ONBOARDING_SUBSCRIPTION_INTERVAL_SECONDS', '60'))
RECONQ_WEBHOOK_URL = os.getenv('RECONQ_WEBHOOK_URL', '')
RECONQ_WEBHOOK_SECRET = os.getenv('RECONQ_WEBHOOK_SECRET', '')


def notify_subscription(event: str, customer: dict, subscription: dict, plan: dict) -> None:
    payload = json.dumps({'event': event, 'customer': customer, 'subscription': subscription, 'plan': plan}, separators=(',', ':'), default=str)
    targets = [('ReconQ', RECONQ_WEBHOOK_URL, RECONQ_WEBHOOK_SECRET)]
    for label, url, secret in targets:
        if not url or not secret:
            logger.warning('Subscription update skipped because %s webhook is not configured', label)
            continue
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        response = httpx.post(url, content=payload, headers={
            'Content-Type': 'application/json', 'X-ReconQ-Signature': signature,
        }, timeout=10)
        response.raise_for_status()


def process_cycle(scheduler, store, notified: set[str], synced: dict[str, str]) -> None:
    now = datetime.now(timezone.utc)
    due = store.list_due_subscriptions(now)
    scheduler.service.process_due_subscriptions()
    with connection() as conn:
        rows = conn.execute(
            '''SELECT c.id, c.tenant_id, c.user_id, c.email,
                      s.id AS subscription_id, s.status, s.trial_end_at, s.period_end_at,
                      p.id AS plan_id, p.name AS plan_name, p.product, p.price_minor, p.currency
               FROM onboarding_customers c
               JOIN subscriptions s ON s.customer_id = c.id
               JOIN subscription_plans p ON p.id = s.plan_id'''
        ).fetchall()
    for row in rows:
        row = dict(row)
        state = f"{row['status']}:{row['plan_id']}:{row['period_end_at']}"
        if synced.get(str(row['subscription_id'])) == state:
            continue
        customer_data = {key: row[key] for key in ('id', 'tenant_id', 'user_id', 'email')}
        subscription_data = {key: row[key] for key in ('subscription_id', 'status', 'trial_end_at', 'period_end_at')}
        plan_data = {key: row[key] for key in ('plan_id', 'plan_name', 'product', 'price_minor', 'currency')}
        notify_subscription('subscription.updated', customer_data, subscription_data, plan_data)
        synced[str(row['subscription_id'])] = state
    for subscription in due:
        current = store.get_subscription(subscription.id)
        if not current or current.status.value != 'expired' or current.id in notified:
            continue
        with connection() as conn:
            customer = conn.execute(
                '''SELECT c.id, c.tenant_id, c.user_id, c.email,
                          s.id AS subscription_id, s.status, s.trial_end_at, s.period_end_at,
                          p.id AS plan_id, p.name AS plan_name, p.product, p.price_minor, p.currency
                   FROM onboarding_customers c
                   JOIN subscriptions s ON s.customer_id = c.id
                   JOIN subscription_plans p ON p.id = s.plan_id
                   WHERE c.id=%s''',
                (current.customer_id,),
            ).fetchone()
        if customer:
            row = dict(customer)
            customer_data = {key: row[key] for key in ('id', 'tenant_id', 'user_id', 'email')}
            subscription_data = {key: row[key] for key in ('subscription_id', 'status', 'trial_end_at', 'period_end_at')}
            plan_data = {key: row[key] for key in ('plan_id', 'plan_name', 'product', 'price_minor', 'currency')}
            notify_subscription('subscription.expired', customer_data, subscription_data, plan_data)
            notified.add(current.id)


async def run() -> None:
    initialize_database_with_retry()
    scheduler = SubscriptionScheduler(
        SubscriptionService(PostgresSubscriptionStore(connection)),
        interval_seconds=INTERVAL_SECONDS,
    )
    notified, synced = set(), {}
    logger.info('Subscription scheduler started (interval=%ss)', INTERVAL_SECONDS)
    try:
        while True:
            try:
                await asyncio.to_thread(process_cycle, scheduler, scheduler.service.store, notified, synced)
            except Exception:
                logger.exception('Subscription scheduler cycle failed')
            await asyncio.sleep(INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info('Subscription scheduler stopped')


if __name__ == '__main__':
    asyncio.run(run())

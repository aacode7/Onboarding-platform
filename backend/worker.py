import asyncio
import logging
import os

from app.scheduler import SubscriptionScheduler
from app.services.subscription_service import SubscriptionService

from .main import connection, initialize_database_with_retry
from .subscription_store import PostgresSubscriptionStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
INTERVAL_SECONDS = int(os.getenv('ONBOARDING_SUBSCRIPTION_INTERVAL_SECONDS', '60'))


async def run() -> None:
    initialize_database_with_retry()
    scheduler = SubscriptionScheduler(
        SubscriptionService(PostgresSubscriptionStore(connection)),
        interval_seconds=INTERVAL_SECONDS,
    )
    scheduler.start()
    logger.info('Subscription scheduler started (interval=%ss)', INTERVAL_SECONDS)
    try:
        await asyncio.Event().wait()
    finally:
        await scheduler.stop()
        logger.info('Subscription scheduler stopped')


if __name__ == '__main__':
    asyncio.run(run())

import os
import logging
import time
import hashlib
import hmac
import re
import secrets
from contextlib import closing, asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import jwt
import httpx

from app.services.subscription_service import SubscriptionService

from .subscription_store import PostgresSubscriptionStore

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
DATABASE_URL = os.getenv('ONBOARDING_DATABASE_URL', 'postgresql://onboarding_app:change-this-password@localhost:5432/onboarding_db')
API_KEY = os.getenv('ONBOARDING_API_KEY', '')
RECONQ_WEBHOOK_URL = os.getenv('RECONQ_WEBHOOK_URL', '')
RECONQ_WEBHOOK_SECRET = os.getenv('RECONQ_WEBHOOK_SECRET', '')
INTELLQ_WEBHOOK_URL = os.getenv('INTELLQ_WEBHOOK_URL', '')
INTELLQ_WEBHOOK_SECRET = os.getenv('INTELLQ_WEBHOOK_SECRET', '')
ADMIN_EMAIL = os.getenv('ONBOARDING_ADMIN_EMAIL', 'admin@example.com')
ADMIN_PASSWORD = os.getenv('ONBOARDING_ADMIN_PASSWORD')
ACCESS_TOKEN_SECRET = os.getenv('ONBOARDING_ACCESS_TOKEN_SECRET') or secrets.token_urlsafe(32)
REFRESH_TOKEN_SECRET = os.getenv('ONBOARDING_REFRESH_TOKEN_SECRET') or secrets.token_urlsafe(32)
ACCESS_TOKEN_MINUTES = int(os.getenv('ONBOARDING_ACCESS_TOKEN_MINUTES', '15'))
REFRESH_TOKEN_DAYS = int(os.getenv('ONBOARDING_REFRESH_TOKEN_DAYS', '7'))
FREE_TRIAL_PLAN_ID = '2f1c0d5e-5b6e-4c38-9f76-8a7e5d2b1c40'
TRIAL_DURATION_DAYS = int(os.getenv('ONBOARDING_TRIAL_DURATION_DAYS', '15'))

def connection(): return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def notify_reconq(event: str, plan: dict):
    import json
    payload = json.dumps({'event': event, ('customer' if event == 'customer.deleted' else 'plan'): plan}, separators=(',', ':'), default=str)
    targets = [('ReconQ', RECONQ_WEBHOOK_URL, RECONQ_WEBHOOK_SECRET)] if event == 'customer.deleted' else [('ReconQ', RECONQ_WEBHOOK_URL, RECONQ_WEBHOOK_SECRET), ('IntellQ', INTELLQ_WEBHOOK_URL, INTELLQ_WEBHOOK_SECRET)]
    for label, url, secret in targets:
        if not url or not secret:
            continue
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        try:
            httpx.post(url, content=payload, headers={'Content-Type': 'application/json', 'X-ReconQ-Signature': signature}, timeout=5)
        except httpx.HTTPError:
            logging.getLogger(__name__).exception('Unable to notify %s about %s', label, event)

@asynccontextmanager
async def lifespan(_app):
    initialize_database_with_retry()
    yield

app = FastAPI(title='Onboarding Platform', lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv('ONBOARDING_CORS_ORIGINS', 'http://localhost:3000,http://localhost:4000').split(',') if origin.strip()],
    allow_methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['*'],
)
logger = logging.getLogger(__name__)

class Customer(BaseModel):
    id: str | None = None
    tenant_id: str
    user_id: str
    name: str
    email: EmailStr
    product: str = 'Application'
    subscription_status: str = 'TRIAL'
    trial_start_date: datetime | None = None
    created_at: datetime | None = None

class Login(BaseModel):
    email: EmailStr
    password: str

class SubscriptionTokenRequest(BaseModel):
    email: EmailStr
    plan: str = "Free Trial"
class SubscriptionTokenConsumeRequest(BaseModel):
    email: EmailStr
    token: str

class PlanInput(BaseModel):
    name: str
    price: float = 0
    annual_price: float = 0
    currency: str = 'INR'
    description: str = ''
    features: str = ''
    product: str = ''
    popular: bool = False

def initialize_database():
    with closing(connection()) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS onboarding_customers (
            id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            name TEXT NOT NULL, email TEXT NOT NULL, product TEXT NOT NULL,
            trial_start_date TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL)''')
        conn.execute('ALTER TABLE onboarding_customers DROP COLUMN IF EXISTS trial_end_date')
        conn.execute('ALTER TABLE onboarding_customers DROP COLUMN IF EXISTS subscription_status')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS onboarding_customers_user_id_uq
            ON onboarding_customers(user_id)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS subscription_plans (
            id UUID PRIMARY KEY, name TEXT NOT NULL, price_minor INTEGER NOT NULL, annual_price_minor INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL, entitlements TEXT[] NOT NULL DEFAULT '{}',
            description TEXT NOT NULL DEFAULT ''
            , product TEXT NOT NULL DEFAULT ''
        )''')
        conn.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS annual_price_minor INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS product TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS popular BOOLEAN NOT NULL DEFAULT FALSE")
        conn.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
            id UUID PRIMARY KEY, customer_id UUID NOT NULL REFERENCES onboarding_customers(id) ON DELETE CASCADE,
            plan_id UUID NOT NULL REFERENCES subscription_plans(id), status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, trial_end_at TIMESTAMPTZ,
            period_end_at TIMESTAMPTZ, canceled_at TIMESTAMPTZ
        )''')
        conn.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS product TEXT NOT NULL DEFAULT ''")
        conn.execute('ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_plan_fk')
        conn.execute('''DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'subscription_plans'
                         AND column_name = 'id' AND data_type = 'text') THEN
                ALTER TABLE subscription_plans ALTER COLUMN id TYPE UUID USING CASE
                    WHEN id = 'onboarding-free-trial' THEN '%s'::uuid
                    ELSE id::uuid END;
            END IF;
        END $$''' % FREE_TRIAL_PLAN_ID)
        conn.execute('''ALTER TABLE subscriptions
            ALTER COLUMN customer_id TYPE UUID USING customer_id::uuid''')
        conn.execute('''DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'subscriptions'
                         AND column_name = 'plan_id' AND data_type = 'text') THEN
                ALTER TABLE subscriptions ALTER COLUMN plan_id TYPE UUID USING CASE
                    WHEN plan_id = 'onboarding-free-trial' THEN '%s'::uuid
                    ELSE plan_id::uuid END;
            END IF;
        END $$''' % FREE_TRIAL_PLAN_ID)
        conn.execute('''DO $$ BEGIN
            ALTER TABLE subscriptions
            ADD CONSTRAINT subscriptions_customer_fk
            FOREIGN KEY (customer_id) REFERENCES onboarding_customers(id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$''')
        conn.execute('''DO $$ BEGIN
            ALTER TABLE subscriptions
            ADD CONSTRAINT subscriptions_plan_fk
            FOREIGN KEY (plan_id) REFERENCES subscription_plans(id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$''')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_customer_plan_uq
            ON subscriptions(customer_id, plan_id)''')
        conn.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'trial_signup_tokens') AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'subscription_signup_tokens') THEN ALTER TABLE trial_signup_tokens RENAME TO subscription_signup_tokens; END IF; END $$;")
        conn.execute("CREATE TABLE IF NOT EXISTS subscription_signup_tokens (id UUID PRIMARY KEY, email TEXT NOT NULL, plan TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ)")
        conn.execute('''CREATE TABLE IF NOT EXISTS admin_users (
            email TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL)''')
        if ADMIN_PASSWORD:
            salt = secrets.token_hex(16)
            password_hash = hash_password(ADMIN_PASSWORD, salt)
            conn.execute('''INSERT INTO admin_users (email, password_hash, password_salt, created_at)
                VALUES (%s, %s, %s, NOW()) ON CONFLICT (email) DO NOTHING''',
                (ADMIN_EMAIL, password_hash, salt))
        conn.commit()

def initialize_database_with_retry(attempts=30):
    for attempt in range(attempts):
        try:
            initialize_database()
            return
        except Exception:
            if attempt == attempts - 1:
                raise
            delay = min(2 ** attempt, 10)
            logger.warning("Database is not ready; retrying in %ss", delay)
            time.sleep(delay)


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 210_000).hex()

def require_admin(authorization: str | None):
    if not authorization:
        raise HTTPException(401, 'Authentication required')
    try:
        claims = jwt.decode(authorization.removeprefix('Bearer '), ACCESS_TOKEN_SECRET, algorithms=['HS256'])
        if claims.get('type') != 'access': raise ValueError
        return claims
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401, 'Authentication required')

def require_service(authorization: str | None):
    if not API_KEY or authorization != f'Bearer {API_KEY}':
        raise HTTPException(401, 'Invalid service credentials')

def require_admin_or_service(authorization: str | None):
    if API_KEY and authorization == f'Bearer {API_KEY}':
        return
    require_admin(authorization)

@app.get('/health')
def health(): return {'status': 'ok', 'database': 'postgresql'}

@app.post('/api/admin/login')
def login(credentials: Login):
    with closing(connection()) as conn:
        admin = conn.execute('SELECT password_hash, password_salt FROM admin_users WHERE email=%s', (credentials.email,)).fetchone()
    if not admin or not hmac.compare_digest(hash_password(credentials.password, admin['password_salt']), admin['password_hash']):
        raise HTTPException(401, 'Invalid email or password')
    now = datetime.now(timezone.utc)
    access_token = jwt.encode(
        {'sub': credentials.email, 'type': 'access', 'exp': now + timedelta(minutes=ACCESS_TOKEN_MINUTES)},
        ACCESS_TOKEN_SECRET, algorithm='HS256')
    refresh_token = jwt.encode(
        {'sub': credentials.email, 'type': 'refresh', 'exp': now + timedelta(days=REFRESH_TOKEN_DAYS)},
        REFRESH_TOKEN_SECRET, algorithm='HS256')
    return {'access_token': access_token, 'refresh_token': refresh_token,
            'token_type': 'bearer', 'expires_in': ACCESS_TOKEN_MINUTES * 60}

class Refresh(BaseModel):
    refresh_token: str

@app.post('/api/admin/refresh')
def refresh(credentials: Refresh):
    try:
        claims = jwt.decode(credentials.refresh_token, REFRESH_TOKEN_SECRET, algorithms=['HS256'])
        if claims.get('type') != 'refresh': raise ValueError
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401, 'Invalid refresh token')
    now = datetime.now(timezone.utc)
    access_token = jwt.encode(
        {'sub': claims['sub'], 'type': 'access', 'exp': now + timedelta(minutes=ACCESS_TOKEN_MINUTES)},
        ACCESS_TOKEN_SECRET, algorithm='HS256')
    return {'access_token': access_token, 'token_type': 'bearer',
            'expires_in': ACCESS_TOKEN_MINUTES * 60}

@app.post('/api/onboarding/customers', status_code=201)
def create_customer(customer: Customer, authorization: str | None = Header(default=None)):
    require_service(authorization)
    now = datetime.now(timezone.utc)
    customer.created_at = customer.created_at or now
    customer.trial_start_date = customer.trial_start_date or now
    subscription_end = now + timedelta(days=TRIAL_DURATION_DAYS)
    with closing(connection()) as conn:
        existing = conn.execute(
            'SELECT id FROM onboarding_customers WHERE user_id=%s',
            (customer.user_id,),
        ).fetchone()
        customer_id = str(existing['id']) if existing else (customer.id or str(uuid4()))
        conn.execute('''INSERT INTO onboarding_customers
            (id, tenant_id, user_id, name, email, product,
             trial_start_date, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
            user_id=EXCLUDED.user_id, name=EXCLUDED.name, email=EXCLUDED.email,
            product=EXCLUDED.product, trial_start_date=EXCLUDED.trial_start_date,
            created_at=EXCLUDED.created_at''',
            (customer_id, customer.tenant_id, customer.user_id, customer.name,
             customer.email, customer.product, customer.trial_start_date, customer.created_at))
        plan = conn.execute('''SELECT id FROM subscription_plans
            WHERE price_minor = 0 AND product = %s
            ORDER BY name LIMIT 1''', (customer.product,)).fetchone()
        if not plan:
            raise HTTPException(409, f'No free subscription plan configured for product {customer.product}')
        conn.execute('''INSERT INTO subscriptions
            (id, customer_id, plan_id, status, created_at, trial_end_at, period_end_at)
            VALUES (%s, %s, %s, 'trialing', %s, %s, %s)
            ON CONFLICT DO NOTHING''',
            (str(uuid4()), customer_id, plan['id'], customer.created_at,
             subscription_end, subscription_end))
        conn.commit()
    customer.id = customer_id
    return {'customer': customer}

def customer_query(where=''):
    return f'''SELECT c.*, s.period_end_at AS subscription_end_at, CASE
                   WHEN s.status = 'expired' OR s.period_end_at < NOW() THEN 'EXPIRED'
                   WHEN s.period_end_at <= NOW() + INTERVAL '7 days' THEN 'EXPIRING_SOON'
                   ELSE 'TRIAL'
               END AS status
               FROM onboarding_customers c
               LEFT JOIN LATERAL (
                   SELECT period_end_at, status FROM subscriptions
                   WHERE customer_id = c.id ORDER BY created_at DESC LIMIT 1
               ) s ON TRUE
               {where.replace('WHERE ', 'WHERE c.')} ORDER BY c.created_at DESC'''

@app.get('/api/onboarding/customers')
def list_customers(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn: return conn.execute(customer_query()).fetchall()

@app.get('/api/onboarding/customers/{customer_id}')
def get_customer(customer_id: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        customer = conn.execute(customer_query('WHERE id=%s'), (customer_id,)).fetchone()
    if not customer: raise HTTPException(404, 'Customer not found')
    return customer

@app.delete('/api/onboarding/customers/{customer_id}')
def delete_customer(customer_id: str, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        customer = conn.execute('SELECT id, tenant_id, user_id, email FROM onboarding_customers WHERE id=%s', (customer_id,)).fetchone()
        if not customer: raise HTTPException(404, 'Customer not found')
        conn.execute('DELETE FROM subscriptions WHERE customer_id=%s', (customer_id,))
        conn.execute('DELETE FROM onboarding_customers WHERE id=%s', (customer_id,))
        conn.commit()
    background_tasks.add_task(notify_reconq, 'customer.deleted', dict(customer))
    return {'deleted': True, 'id': customer_id}

@app.get('/api/subscription-entitlements/{customer_id}/{key}')
def check_subscription_entitlement(customer_id: str, key: str, authorization: str | None = Header(default=None)):
    require_service(authorization)
    result = SubscriptionService(PostgresSubscriptionStore(connection)).has_entitlement(customer_id, key)
    return {'customer_id': result.customer_id, 'key': result.key, 'allowed': result.allowed}


@app.post("/api/public/subscription-token")
def create_subscription_token(req: SubscriptionTokenRequest):
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with closing(connection()) as conn:
        conn.execute("DELETE FROM subscription_signup_tokens WHERE expires_at <= %s", (now,))
        conn.execute("INSERT INTO subscription_signup_tokens (id, email, plan, token_hash, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s)", (str(uuid4()), str(req.email), req.plan, hashlib.sha256(raw_token.encode()).hexdigest(), now, now + timedelta(minutes=30)))
        conn.commit()
    return {"token": raw_token, "expires_at": now + timedelta(minutes=30)}


@app.post("/api/public/subscription-token/consume")
def consume_subscription_token(req: SubscriptionTokenConsumeRequest):
    token_hash = hashlib.sha256(req.token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    with closing(connection()) as conn:
        conn.execute("DELETE FROM subscription_signup_tokens WHERE expires_at <= %s", (now,))
        row = conn.execute("UPDATE subscription_signup_tokens SET consumed_at=%s WHERE token_hash=%s AND email=%s AND consumed_at IS NULL AND expires_at>%s RETURNING email, plan", (now, token_hash, str(req.email), now)).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=400, detail="This signup link is invalid or expired.")
    return {"valid": True, "email": row["email"], "plan": row["plan"]}


@app.get("/api/public/subscription-token/status")
def subscription_token_status(token: str, email: EmailStr):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    with closing(connection()) as conn:
        row = conn.execute("SELECT email, expires_at, consumed_at FROM subscription_signup_tokens WHERE token_hash=%s AND email=%s", (token_hash, str(email))).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Signup link not found")
        if row["expires_at"] <= now:
            conn.execute("DELETE FROM subscription_signup_tokens WHERE token_hash=%s", (token_hash,))
            conn.commit()
            raise HTTPException(status_code=410, detail="This signup link has expired")
        if row["consumed_at"]:
            raise HTTPException(status_code=410, detail="This signup link has already been used")
    return {"valid": True, "expires_at": row["expires_at"]}


@app.get('/api/public/subscription-plans')
def public_subscription_plans():
    with closing(connection()) as conn:
        rows = conn.execute('''SELECT id, name, price_minor, annual_price_minor, currency, product, popular, description,
            array_to_string(entitlements, E'\n') AS features
            FROM subscription_plans WHERE product IS NOT NULL AND product <> '' ORDER BY price_minor, name''').fetchall()
    return [{**row, 'price': row.pop('price_minor') / 100, 'annual_price': row.pop('annual_price_minor') / 100} for row in rows]


@app.get('/api/subscription-plans')
def list_subscription_plans(authorization: str | None = Header(default=None)):
    require_admin_or_service(authorization)
    with closing(connection()) as conn:
        rows = conn.execute('''SELECT id, name, price_minor, annual_price_minor, currency, product, popular, description,
            array_to_string(entitlements, E'\n') AS features, product
            FROM subscription_plans ORDER BY price_minor, name''').fetchall()
    return [{**row, 'price': row.pop('price_minor') / 100, 'annual_price': row.pop('annual_price_minor') / 100} for row in rows]

@app.get('/api/subscription-products')
def list_subscription_products(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        rows = conn.execute("SELECT DISTINCT product FROM onboarding_customers WHERE product IS NOT NULL AND product <> '' AND product <> 'Application' ORDER BY product").fetchall()
    return [row['product'] for row in rows]


@app.post('/api/subscription-plans', status_code=201)
def create_subscription_plan(plan: PlanInput, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    plan_id = str(uuid4())
    with closing(connection()) as conn:
        if plan.popular:
            conn.execute('UPDATE subscription_plans SET popular=FALSE WHERE product=%s', (plan.product.strip(),))
        row = conn.execute('''INSERT INTO subscription_plans
            (id, name, price_minor, annual_price_minor, currency, entitlements, description, product, popular)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, name, price_minor, annual_price_minor, currency, description, array_to_string(entitlements, E'\n') AS features, product, popular''',
            (plan_id, plan.name.strip(), round(plan.price * 100), round(plan.annual_price * 100), plan.currency.upper(),
             [item.strip() for item in plan.features.splitlines() if item.strip()], plan.description.strip(), plan.product.strip(), plan.popular)).fetchone()
        conn.commit()
    result = {**row, 'price': row.pop('price_minor') / 100, 'annual_price': row.pop('annual_price_minor') / 100}
    background_tasks.add_task(notify_reconq, 'subscription.plan.created', result)
    return result

@app.put('/api/subscription-plans/{plan_id}')
def update_subscription_plan(plan_id: str, plan: PlanInput, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        if plan.popular:
            conn.execute('UPDATE subscription_plans SET popular=FALSE WHERE product=%s AND id<>%s', (plan.product.strip(), plan_id))
        row = conn.execute('''UPDATE subscription_plans SET name=%s, price_minor=%s,
            annual_price_minor=%s, currency=%s, entitlements=%s, description=%s, product=%s, popular=%s WHERE id=%s
            RETURNING id, name, price_minor, annual_price_minor, currency, description,
            array_to_string(entitlements, E'\n') AS features, product, popular''',
            (plan.name.strip(), round(plan.price * 100), round(plan.annual_price * 100), plan.currency.upper(),
             [item.strip() for item in plan.features.splitlines() if item.strip()],
             plan.description.strip(), plan.product.strip(), plan.popular, plan_id)).fetchone()
        if not row: raise HTTPException(404, 'Subscription plan not found')
        conn.commit()
    result = {**row, 'price': row.pop('price_minor') / 100, 'annual_price': row.pop('annual_price_minor') / 100}
    background_tasks.add_task(notify_reconq, 'subscription.plan.updated', result)
    return result

@app.delete('/api/subscription-plans/{plan_id}')
def delete_subscription_plan(plan_id: str, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        try:
            row = conn.execute('DELETE FROM subscription_plans WHERE id=%s RETURNING id', (plan_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Subscription plan not found')
            conn.commit()
        except psycopg.errors.ForeignKeyViolation as exc:
            conn.rollback()
            raise HTTPException(409, 'This plan is already used by a subscription and cannot be deleted') from exc
    background_tasks.add_task(notify_reconq, 'subscription.plan.deleted', {'id': plan_id})
    return {'deleted': True, 'id': plan_id}

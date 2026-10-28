import os
import logging
import time
import hashlib
import hmac
import re
import secrets
import json
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
KEYCLOAK_URL = os.getenv('KEYCLOAK_URL', 'http://localhost:8080')
KEYCLOAK_REALM = os.getenv('KEYCLOAK_REALM', 'onboarding')
KEYCLOAK_CLIENT_ID = os.getenv('KEYCLOAK_CLIENT_ID', 'onboarding-frontend')
KEYCLOAK_ADMIN_USERNAME = os.getenv('KEYCLOAK_ADMIN_USERNAME', '')
KEYCLOAK_ADMIN_PASSWORD = os.getenv('KEYCLOAK_ADMIN_PASSWORD', '')
KEYCLOAK_RECONQ_REALM = os.getenv('KEYCLOAK_RECONQ_REALM', 'reconq')
RECONQ_AUTH_URL = os.getenv('RECONQ_AUTH_URL', 'http://localhost:3000/login')
KEYCLOAK_ISSUER = f'{KEYCLOAK_URL.rstrip("/")}/realms/{KEYCLOAK_REALM}'
KEYCLOAK_JWKS = jwt.PyJWKClient(f'{KEYCLOAK_ISSUER}/protocol/openid-connect/certs')
PLAN_DURATION_DAYS = int(os.environ['PLAN_DURATION_DAYS'])
TRIAL_START_DATE = os.getenv('ONBOARDING_TRIAL_START_DATE', '').strip()

def connection(): return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def notify_reconq(event: str, plan: dict):
    import json
    payload = json.dumps({'event': event, ('customer' if event == 'customer.deleted' else 'plan'): plan}, separators=(',', ':'), default=str)
    targets = [('ReconQ', RECONQ_WEBHOOK_URL, RECONQ_WEBHOOK_SECRET)] if event == 'customer.deleted' else [('ReconQ', RECONQ_WEBHOOK_URL, RECONQ_WEBHOOK_SECRET), ('IntellQ', INTELLQ_WEBHOOK_URL, INTELLQ_WEBHOOK_SECRET)]
    for label, url, secret in targets:
        if not url or not secret:
            if event == 'customer.deleted' and label == 'ReconQ':
                raise RuntimeError('RECONQ_WEBHOOK_URL and RECONQ_WEBHOOK_SECRET are required for customer deletion')
            continue
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        try:
            response = httpx.post(url, content=payload, headers={'Content-Type': 'application/json', 'X-ReconQ-Signature': signature}, timeout=5)
            response.raise_for_status()
        except httpx.HTTPError:
            logging.getLogger(__name__).exception('Unable to notify %s about %s', label, event)
            if event == 'customer.deleted' and label == 'ReconQ':
                raise

def delete_keycloak_user(user_id: str, email: str, realm: str = KEYCLOAK_REALM):
    if not KEYCLOAK_ADMIN_USERNAME or not KEYCLOAK_ADMIN_PASSWORD:
        raise RuntimeError('Keycloak admin credentials are not configured')
    token = httpx.post(f'{KEYCLOAK_URL.rstrip("/")}/realms/master/protocol/openid-connect/token', data={
        'grant_type': 'password', 'client_id': 'admin-cli',
        'username': KEYCLOAK_ADMIN_USERNAME, 'password': KEYCLOAK_ADMIN_PASSWORD,
    }, timeout=10)
    token.raise_for_status()
    headers = {'Authorization': f'Bearer {token.json()["access_token"]}'}
    response = httpx.delete(
        f'{KEYCLOAK_URL.rstrip("/")}/admin/realms/{realm}/users/{user_id}',
        headers=headers, timeout=10,
    )
    if response.status_code == 404:
        users = httpx.get(
            f'{KEYCLOAK_URL.rstrip("/")}/admin/realms/{realm}/users',
            params={'email': email, 'exact': 'true', 'max': 20}, headers=headers, timeout=10,
        )
        users.raise_for_status()
        for user in users.json():
            if user.get('email', '').casefold() == email.casefold():
                httpx.delete(
                    f'{KEYCLOAK_URL.rstrip("/")}/admin/realms/{realm}/users/{user["id"]}',
                    headers=headers, timeout=10,
                ).raise_for_status()
                break
    elif response.status_code != 204:
        response.raise_for_status()

def delete_keycloak_group(tenant_id: str, realm: str):
    if not KEYCLOAK_ADMIN_USERNAME or not KEYCLOAK_ADMIN_PASSWORD:
        raise RuntimeError('Keycloak admin credentials are not configured')
    token = httpx.post(f'{KEYCLOAK_URL.rstrip("/")}/realms/master/protocol/openid-connect/token', data={
        'grant_type': 'password', 'client_id': 'admin-cli',
        'username': KEYCLOAK_ADMIN_USERNAME, 'password': KEYCLOAK_ADMIN_PASSWORD,
    }, timeout=10)
    token.raise_for_status()
    headers = {'Authorization': f'Bearer {token.json()["access_token"]}'}
    base = f'{KEYCLOAK_URL.rstrip("/")}/admin/realms/{realm}'
    groups = httpx.get(f'{base}/groups', params={'search': f'tenant-{tenant_id}'}, headers=headers, timeout=10)
    groups.raise_for_status()
    for group in groups.json():
        if group.get('name') == f'tenant-{tenant_id}':
            response = httpx.delete(f'{base}/groups/{group["id"]}', headers=headers, timeout=10)
            if response.status_code not in (204, 404):
                response.raise_for_status()

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
MANAGED_ROLES = {
    'CFO', 'Finance Manager', 'Accountant', 'Reviewer', 'Administrator', 'Auditor',
    'superadmin', 'tenant_admin', 'analyst', 'auditor', 'viewer',
}
class Customer(BaseModel):
    id: str | None = None
    tenant_id: str
    user_id: str
    name: str
    email: EmailStr
    product: str = 'Application'
    business_type: str = 'Other'
    project_id: str | None = None
    subscription_status: str = 'TRIAL'
    plan_name: str
    trial_start_date: datetime | None = None
    plan_duration_days: int | None = None
    created_at: datetime | None = None
    role: str = 'Administrator'

class TrialUpdate(BaseModel):
    trial_end_at: datetime

class Login(BaseModel):
    email: EmailStr
    password: str

class WorkspaceAccessTokenRequest(BaseModel):
    email: EmailStr
    user_id: str
    tenant_id: str

class WorkspaceAccessTokenConsumeRequest(BaseModel):
    token: str

class ProjectRegistration(BaseModel):
    key: str
    name: str

class PlanInput(BaseModel):
    name: str
    price: float = 0
    annual_price: float = 0
    currency: str = 'INR'
    description: str = ''
    features: str = ''
    product: str = ''
    popular: bool = False
    plan_duration_days: int = 15

def initialize_database():
    with closing(connection()) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS onboarding_customers (
            id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            name TEXT NOT NULL, email TEXT NOT NULL, product TEXT NOT NULL,
            business_type TEXT NOT NULL DEFAULT 'Other',
            project_id UUID,
            role TEXT NOT NULL DEFAULT 'Administrator',
            trial_start_date TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL)''')
        conn.execute("ALTER TABLE onboarding_customers ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'Administrator'")
        conn.execute("DROP TABLE IF EXISTS role_permissions")
        conn.execute("ALTER TABLE onboarding_customers ADD COLUMN IF NOT EXISTS business_type TEXT NOT NULL DEFAULT 'Other'")
        conn.execute("ALTER TABLE onboarding_customers ADD COLUMN IF NOT EXISTS project_id UUID")
        conn.execute("ALTER TABLE onboarding_customers ADD COLUMN IF NOT EXISTS guardrails JSONB NOT NULL DEFAULT '{}'::jsonb")
        conn.execute("ALTER TABLE onboarding_projects ADD COLUMN IF NOT EXISTS guardrails JSONB NOT NULL DEFAULT '{}'::jsonb")
        conn.execute('ALTER TABLE onboarding_customers DROP COLUMN IF EXISTS trial_end_date')
        conn.execute('ALTER TABLE onboarding_customers DROP COLUMN IF EXISTS subscription_status')
        conn.execute('''CREATE TABLE IF NOT EXISTS onboarding_projects (
            id UUID PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )''')
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
        conn.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'subscription_plans' AND column_name = 'trial_duration_days')
               AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'subscription_plans' AND column_name = 'plan_duration_days') THEN
                ALTER TABLE subscription_plans RENAME COLUMN trial_duration_days TO plan_duration_days;
            END IF;
        END $$;""")
        conn.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS plan_duration_days INTEGER NOT NULL DEFAULT 15")
        conn.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS guardrails JSONB NOT NULL DEFAULT '{}'::jsonb")
        conn.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
            id UUID PRIMARY KEY, customer_id UUID NOT NULL REFERENCES onboarding_customers(id) ON DELETE CASCADE,
            plan_id UUID NOT NULL REFERENCES subscription_plans(id), status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, trial_end_at TIMESTAMPTZ,
            period_end_at TIMESTAMPTZ, canceled_at TIMESTAMPTZ
        )''')
        conn.execute('''DELETE FROM subscriptions
            WHERE customer_id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY lower(email) ORDER BY created_at DESC, id DESC
                    ) AS duplicate_rank
                    FROM onboarding_customers
                ) duplicates WHERE duplicate_rank > 1
            )''')
        conn.execute('''DELETE FROM onboarding_customers
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY lower(email) ORDER BY created_at DESC, id DESC
                    ) AS duplicate_rank
                    FROM onboarding_customers
                ) duplicates WHERE duplicate_rank > 1
            )''')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS onboarding_customers_email_uq
            ON onboarding_customers(lower(email))''')
        conn.execute("ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS product TEXT NOT NULL DEFAULT ''")
        conn.execute('ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_plan_fk')
        conn.execute('''ALTER TABLE subscriptions
            ALTER COLUMN customer_id TYPE UUID USING customer_id::uuid''')
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
        conn.execute("CREATE TABLE IF NOT EXISTS workspace_access_tokens (id UUID PRIMARY KEY, email TEXT NOT NULL, user_id TEXT NOT NULL, tenant_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ)")
        # Migration: workspace links remain valid until expiry, regardless of prior use.
        conn.execute("UPDATE workspace_access_tokens SET consumed_at = NULL WHERE expires_at > %s", (datetime.now(timezone.utc),))
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
        token = authorization.removeprefix('Bearer ')
        signing_key = KEYCLOAK_JWKS.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, signing_key, algorithms=['RS256'], issuer=KEYCLOAK_ISSUER, options={'verify_aud': False})
        if claims.get('azp') != KEYCLOAK_CLIENT_ID: raise ValueError
        return claims
    except Exception:
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
    response = httpx.post(f'{KEYCLOAK_ISSUER}/protocol/openid-connect/token', data={
        'grant_type': 'password', 'client_id': KEYCLOAK_CLIENT_ID,
        'username': credentials.email, 'password': credentials.password,
    }, timeout=10)
    if response.status_code != 200:
        raise HTTPException(401, 'Invalid email or password')
    return response.json()

class Refresh(BaseModel):
    refresh_token: str

@app.post('/api/admin/refresh')
def refresh(credentials: Refresh):
    response = httpx.post(f'{KEYCLOAK_ISSUER}/protocol/openid-connect/token', data={
        'grant_type': 'refresh_token', 'client_id': KEYCLOAK_CLIENT_ID,
        'refresh_token': credentials.refresh_token,
    }, timeout=10)
    if response.status_code != 200:
        raise HTTPException(401, 'Invalid refresh token')
    return response.json()

@app.post('/api/onboarding/customers', status_code=201)
def create_customer(customer: Customer, authorization: str | None = Header(default=None)):
    require_service(authorization)
    now = datetime.now(timezone.utc)
    customer.created_at = customer.created_at or now
    customer.trial_start_date = customer.trial_start_date or (datetime.fromisoformat(TRIAL_START_DATE).replace(tzinfo=timezone.utc) if TRIAL_START_DATE else now)
    duration_days = customer.plan_duration_days if customer.plan_duration_days is not None else PLAN_DURATION_DAYS
    if duration_days < 1:
        raise HTTPException(422, 'plan_duration_days must be at least 1')
    with closing(connection()) as conn:
        existing = conn.execute(
            'SELECT id FROM onboarding_customers WHERE user_id=%s',
            (customer.user_id,),
        ).fetchone()
        duplicate = conn.execute(
            'SELECT id FROM onboarding_customers WHERE lower(email)=lower(%s) AND user_id<>%s',
            (customer.email, customer.user_id),
        ).fetchone()
        if duplicate:
            raise HTTPException(409, 'A customer with this email already exists')
        customer_id = str(existing['id']) if existing else (customer.id or str(uuid4()))
        conn.execute('''INSERT INTO onboarding_customers
            (id, tenant_id, user_id, name, email, product, business_type, project_id,
             role, trial_start_date, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
            user_id=EXCLUDED.user_id, name=EXCLUDED.name, email=EXCLUDED.email,
            product=EXCLUDED.product, role=EXCLUDED.role, trial_start_date=EXCLUDED.trial_start_date,
            created_at=EXCLUDED.created_at''',
            (customer_id, customer.tenant_id, customer.user_id, customer.name,
             customer.email, customer.product, customer.business_type, customer.project_id,
             customer.role, customer.trial_start_date, customer.created_at))
        plan = conn.execute('''SELECT id, name, plan_duration_days FROM subscription_plans
            WHERE product = %s AND lower(name) = lower(%s::text)
            LIMIT 1''',
            (customer.product, customer.plan_name)).fetchone()
        if not plan:
            raise HTTPException(409, f'Subscription plan {customer.plan_name!r} is not configured for product {customer.product}')
        duration_days = plan['plan_duration_days']
        subscription_end = customer.trial_start_date + timedelta(days=duration_days)
        conn.execute('''INSERT INTO subscriptions
            (id, customer_id, plan_id, status, created_at, trial_end_at, period_end_at)
            VALUES (%s, %s, %s, 'trialing', %s, %s, %s)
            ON CONFLICT DO NOTHING''',
            (str(uuid4()), customer_id, plan['id'], customer.created_at,
             subscription_end, subscription_end))
        conn.commit()
    customer.id = customer_id
    return {'customer': customer}

@app.get('/api/authorization/{user_id}')
def get_authorization(user_id: str, authorization: str | None = Header(default=None)):
    """Return the central role and subscription state for ReconQ service calls."""
    require_service(authorization)
    with closing(connection()) as conn:
        row = conn.execute('''SELECT c.user_id, c.tenant_id, c.role,
            CASE WHEN s.status = 'expired' OR s.period_end_at < NOW()
                 THEN 'EXPIRED' ELSE COALESCE(s.status, 'trialing') END AS status,
            p.name AS plan_name, COALESCE(p.guardrails, '{}'::jsonb) AS guardrails
            FROM onboarding_customers c
            LEFT JOIN LATERAL (
                SELECT status, period_end_at, plan_id FROM subscriptions
                WHERE customer_id = c.id ORDER BY created_at DESC LIMIT 1
            ) s ON TRUE
            LEFT JOIN subscription_plans p ON p.id = s.plan_id
            WHERE c.user_id = %s''', (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Authorization record not found')
    result = dict(row)
    return result

@app.patch('/api/authorization/{user_id}/role')
def update_authorization_role(user_id: str, payload: dict, authorization: str | None = Header(default=None)):
    """Update a role from a trusted ReconQ service call."""
    require_service(authorization)
    role = payload.get('role')
    if role not in MANAGED_ROLES:
        raise HTTPException(422, 'Unsupported role')
    with closing(connection()) as conn:
        row = conn.execute(
            'UPDATE onboarding_customers SET role=%s WHERE user_id=%s RETURNING user_id, tenant_id, role',
            (role, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, 'Authorization record not found')
        conn.commit()
    return dict(row)

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
def list_customers(user_id: str | None = None, authorization: str | None = Header(default=None)):
    require_admin_or_service(authorization)
    with closing(connection()) as conn:
        rows = conn.execute(customer_query('WHERE user_id=%s') if user_id else customer_query(), (user_id,) if user_id else ()).fetchall()
    return rows

@app.get('/api/onboarding/customers/primary/{tenant_id}')
def get_primary_customer(tenant_id: str, authorization: str | None = Header(default=None)):
    require_service(authorization)
    with closing(connection()) as conn:
        customer = conn.execute(
            "SELECT user_id FROM onboarding_customers WHERE tenant_id=%s AND business_type <> 'internal' ORDER BY created_at LIMIT 1",
            (tenant_id,),
        ).fetchone()
    if not customer:
        raise HTTPException(404, 'Primary customer not found')
    return dict(customer)

@app.get('/api/onboarding/customers/{customer_id}')
def get_customer(customer_id: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        customer = conn.execute(customer_query('WHERE id=%s'), (customer_id,)).fetchone()
    if not customer: raise HTTPException(404, 'Customer not found')
    return customer

GUARDRAIL_FIELDS = {
    'max_legal_entities', 'max_reconciliations', 'max_users_per_entity',
    'max_invoices', 'max_bank_statements', 'max_purchase_orders', 'max_credit_notes',
    'max_grns', 'max_erp_syncs', 'max_erp_writebacks', 'max_erp_connectors',
    'max_ai_reads', 'max_ai_writes',
}

@app.patch('/api/onboarding/customers/{customer_id}/guardrails')
def update_customer_guardrails(customer_id: str, payload: dict, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    values = payload.get('guardrails', payload)
    if not isinstance(values, dict) or values.get('scope', 'tenant') != 'tenant':
        raise HTTPException(422, 'Guardrails are tenant-wide')
    cleaned = {'scope': 'tenant'}
    for key in GUARDRAIL_FIELDS:
        value = values.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise HTTPException(422, f'{key} must be a non-negative integer or null')
        cleaned[key] = value
    for key in ('alerts_enabled', 'notifications_enabled'):
        value = values.get(key, True)
        if not isinstance(value, bool):
            raise HTTPException(422, f'{key} must be boolean')
        cleaned[key] = value
    limit_types = values.get('limit_types')
    if not isinstance(limit_types, dict) or any(kind not in ('subscription', 'monthly') for kind in limit_types.values()):
        raise HTTPException(422, 'limit_types must classify each guardrail as subscription or monthly')
    cleaned['limit_types'] = limit_types
    limit_types = values.get('limit_types')
    if not isinstance(limit_types, dict) or any(kind not in ('subscription', 'monthly') for kind in limit_types.values()):
        raise HTTPException(422, 'limit_types must classify each guardrail as subscription or monthly')
    cleaned['limit_types'] = limit_types
    with closing(connection()) as conn:
        row = conn.execute('UPDATE onboarding_customers SET guardrails=%s WHERE id=%s RETURNING id, guardrails', (json.dumps(cleaned), customer_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Customer not found')
        conn.commit()
    return dict(row)

@app.patch('/api/onboarding/customers/{customer_id}/trial')
def update_customer_trial(customer_id: str, update: TrialUpdate, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    end_at = update.trial_end_at
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    if end_at <= datetime.now(timezone.utc):
        raise HTTPException(422, 'Trial end date must be in the future')
    with closing(connection()) as conn:
        row = conn.execute('''UPDATE subscriptions SET trial_end_at=%s, period_end_at=%s
            WHERE customer_id=%s AND status IN ('trialing', 'active')
            RETURNING period_end_at''', (end_at, end_at, customer_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Active trial not found')
        conn.commit()
    return {'customer_id': customer_id, 'trial_end_at': row['period_end_at']}

@app.delete('/api/onboarding/customers/{customer_id}')
def delete_customer(customer_id: str, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        customer = conn.execute('SELECT id, tenant_id, user_id, email FROM onboarding_customers WHERE id=%s', (customer_id,)).fetchone()
        if not customer: raise HTTPException(404, 'Customer not found')
        notify_reconq('customer.deleted', dict(customer))
        delete_keycloak_group(customer['tenant_id'], KEYCLOAK_REALM)
        delete_keycloak_group(customer['tenant_id'], KEYCLOAK_RECONQ_REALM)
        delete_keycloak_user(customer['user_id'], customer['email'])
        delete_keycloak_user(customer['user_id'], customer['email'], KEYCLOAK_RECONQ_REALM)
        # Customer deletion is permanent; invalidate every reusable workspace
        # link for this tenant immediately instead of waiting for expiry.
        conn.execute('DELETE FROM workspace_access_tokens WHERE tenant_id=%s', (customer['tenant_id'],))
        conn.execute('DELETE FROM subscriptions WHERE customer_id=%s', (customer_id,))
        conn.execute('DELETE FROM onboarding_customers WHERE id=%s', (customer_id,))
        conn.commit()
    return {'deleted': True, 'id': customer_id}

@app.get('/api/subscription-entitlements/{customer_id}/{key}')
def check_subscription_entitlement(customer_id: str, key: str, authorization: str | None = Header(default=None)):
    require_service(authorization)
    result = SubscriptionService(PostgresSubscriptionStore(connection)).has_entitlement(customer_id, key)
    return {'customer_id': result.customer_id, 'key': result.key, 'allowed': result.allowed}


@app.post("/api/public/workspace-access-token")
def create_workspace_access_token(req: WorkspaceAccessTokenRequest):
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with closing(connection()) as conn:
        conn.execute("DELETE FROM workspace_access_tokens WHERE expires_at <= %s", (now,))
        conn.execute("INSERT INTO workspace_access_tokens (id, email, user_id, tenant_id, token_hash, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)", (str(uuid4()), str(req.email), req.user_id, req.tenant_id, hashlib.sha256(raw_token.encode()).hexdigest(), now, now + timedelta(hours=72)))
        conn.commit()
    return {"token": raw_token, "expires_at": now + timedelta(hours=72),
            "login_url": f"{RECONQ_AUTH_URL}?workspace_token={raw_token}"}


@app.post("/api/public/workspace-access-token/consume")
def consume_workspace_access_token(req: WorkspaceAccessTokenConsumeRequest | None = None, x_workspace_token: str | None = Header(default=None)):
    token = (req.token if req else None) or x_workspace_token
    if not token:
        raise HTTPException(status_code=422, detail="Workspace token is required.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    with closing(connection()) as conn:
        conn.execute("DELETE FROM workspace_access_tokens WHERE expires_at <= %s", (now,))
        row = conn.execute("SELECT email, user_id, tenant_id FROM workspace_access_tokens WHERE token_hash=%s AND expires_at>%s", (token_hash, now)).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="This workspace link is invalid or expired.")
    return {"valid": True, "email": row["email"], "user_id": row["user_id"], "tenant_id": row["tenant_id"]}


@app.get('/api/public/subscription-plans')
def public_subscription_plans():
    with closing(connection()) as conn:
        rows = conn.execute('''SELECT id, name, price_minor, annual_price_minor, currency, product, popular, plan_duration_days, description,
            array_to_string(entitlements, E'\n') AS features
            FROM subscription_plans WHERE product IS NOT NULL AND product <> '' ORDER BY price_minor, name''').fetchall()
    return [{**row, 'price': row.pop('price_minor') / 100, 'annual_price': row.pop('annual_price_minor') / 100} for row in rows]


@app.get('/api/subscription-plans')
def list_subscription_plans(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        rows = conn.execute('''SELECT id, name, price_minor, annual_price_minor, currency, product, popular, plan_duration_days, description,
            array_to_string(entitlements, E'\n') AS features, product, guardrails
            FROM subscription_plans ORDER BY price_minor, name''').fetchall()
    return [{**row, 'price': row.pop('price_minor') / 100, 'annual_price': row.pop('annual_price_minor') / 100} for row in rows]

@app.patch('/api/subscription-plans/{plan_id}/guardrails')
def update_plan_guardrails(plan_id: str, payload: dict, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    values = payload.get('guardrails', payload)
    if not isinstance(values, dict):
        raise HTTPException(422, 'Guardrails must be an object')
    cleaned = {'scope': 'tenant'}
    for key in GUARDRAIL_FIELDS:
        value = values.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise HTTPException(422, f'{key} must be a non-negative integer or null')
        cleaned[key] = value
    for key in ('alerts_enabled', 'notifications_enabled'):
        value = values.get(key, True)
        if not isinstance(value, bool):
            raise HTTPException(422, f'{key} must be boolean')
        cleaned[key] = value
    with closing(connection()) as conn:
        row = conn.execute('UPDATE subscription_plans SET guardrails=%s WHERE id=%s RETURNING id, name, product, guardrails', (json.dumps(cleaned), plan_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Subscription plan not found')
        conn.commit()
    result = dict(row)
    notify_reconq('subscription.plan.guardrails.updated', result)
    return result

@app.post('/api/projects/register')
def register_project(project: ProjectRegistration, authorization: str | None = Header(default=None)):
    require_service(authorization)
    key = project.key.strip()
    name = project.name.strip()
    if not key or not name:
        raise HTTPException(422, 'Project key and name are required')
    with closing(connection()) as conn:
        row = conn.execute('''INSERT INTO onboarding_projects (id, key, name)
            VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET name=EXCLUDED.name
            RETURNING id, key, name, created_at''',
            (str(uuid4()), key, name)).fetchone()
        conn.commit()
    return dict(row)

@app.get('/api/projects')
def list_projects(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        return conn.execute('SELECT id, key, name, created_at, guardrails FROM onboarding_projects ORDER BY name').fetchall()

@app.patch('/api/projects/{project_key}/guardrails')
def update_project_guardrails(project_key: str, payload: dict, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    values = payload.get('guardrails', payload)
    if not isinstance(values, dict) or values.get('scope', 'tenant') not in ('tenant', 'legal_entity'):
        raise HTTPException(422, 'scope must be tenant or legal_entity')
    cleaned = {'scope': values.get('scope', 'tenant')}
    for key in GUARDRAIL_FIELDS:
        value = values.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise HTTPException(422, f'{key} must be a non-negative integer or null')
        cleaned[key] = value
    with closing(connection()) as conn:
        row = conn.execute('UPDATE onboarding_projects SET guardrails=%s WHERE key=%s RETURNING key, guardrails', (json.dumps(cleaned), project_key)).fetchone()
        if not row:
            raise HTTPException(404, 'Project not found')
        conn.commit()
    return dict(row)


@app.get('/api/subscription-products')
def list_subscription_products(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with closing(connection()) as conn:
        rows = conn.execute("SELECT DISTINCT product FROM onboarding_customers WHERE product IS NOT NULL AND product <> '' AND product <> 'Application' ORDER BY product").fetchall()
    return [row['product'] for row in rows]


@app.post('/api/subscription-plans', status_code=201)
def create_subscription_plan(plan: PlanInput, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if plan.plan_duration_days < 1:
        raise HTTPException(422, "plan_duration_days must be at least 1")
    plan_id = str(uuid4())
    with closing(connection()) as conn:
        if plan.popular:
            conn.execute('UPDATE subscription_plans SET popular=FALSE WHERE product=%s', (plan.product.strip(),))
        row = conn.execute('''INSERT INTO subscription_plans
            (id, name, price_minor, annual_price_minor, currency, entitlements, description, product, popular, plan_duration_days)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, name, price_minor, annual_price_minor, currency, description, array_to_string(entitlements, E'\n') AS features, product, popular, plan_duration_days''',
            (plan_id, plan.name.strip(), round(plan.price * 100), round(plan.annual_price * 100), plan.currency.upper(),
             [item.strip() for item in plan.features.splitlines() if item.strip()], plan.description.strip(), plan.product.strip(), plan.popular, plan.plan_duration_days)).fetchone()
        conn.commit()
    result = {**row, 'price': row.pop('price_minor') / 100, 'annual_price': row.pop('annual_price_minor') / 100}
    background_tasks.add_task(notify_reconq, 'subscription.plan.created', result)
    return result

@app.put('/api/subscription-plans/{plan_id}')
def update_subscription_plan(plan_id: str, plan: PlanInput, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    if plan.plan_duration_days < 1:
        raise HTTPException(422, "plan_duration_days must be at least 1")
    require_admin(authorization)
    with closing(connection()) as conn:
        if plan.popular:
            conn.execute('UPDATE subscription_plans SET popular=FALSE WHERE product=%s AND id<>%s', (plan.product.strip(), plan_id))
        row = conn.execute('''UPDATE subscription_plans SET name=%s, price_minor=%s,
            annual_price_minor=%s, currency=%s, entitlements=%s, description=%s, product=%s, popular=%s, plan_duration_days=%s WHERE id=%s
            RETURNING id, name, price_minor, annual_price_minor, currency, description,
            array_to_string(entitlements, E'\n') AS features, product, popular, plan_duration_days''',
            (plan.name.strip(), round(plan.price * 100), round(plan.annual_price * 100), plan.currency.upper(),
             [item.strip() for item in plan.features.splitlines() if item.strip()],
             plan.description.strip(), plan.product.strip(), plan.popular, plan.plan_duration_days, plan_id)).fetchone()
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

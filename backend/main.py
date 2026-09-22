import os
import hashlib
import hmac
import secrets
from contextlib import closing, asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr
import jwt

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
DATABASE_URL = os.getenv('ONBOARDING_DATABASE_URL', 'postgresql://onboarding_app:change-this-password@localhost:5432/onboarding_db')
API_KEY = os.getenv('ONBOARDING_API_KEY', '')
ADMIN_EMAIL = os.getenv('ONBOARDING_ADMIN_EMAIL', 'admin@example.com')
ADMIN_PASSWORD = os.getenv('ONBOARDING_ADMIN_PASSWORD')
ACCESS_TOKEN_SECRET = os.getenv('ONBOARDING_ACCESS_TOKEN_SECRET') or secrets.token_urlsafe(32)
REFRESH_TOKEN_SECRET = os.getenv('ONBOARDING_REFRESH_TOKEN_SECRET') or secrets.token_urlsafe(32)
ACCESS_TOKEN_MINUTES = int(os.getenv('ONBOARDING_ACCESS_TOKEN_MINUTES', '15'))
REFRESH_TOKEN_DAYS = int(os.getenv('ONBOARDING_REFRESH_TOKEN_DAYS', '7'))
FREE_TRIAL_PLAN_ID = '2f1c0d5e-5b6e-4c38-9f76-8a7e5d2b1c40'

def connection(): return psycopg.connect(DATABASE_URL, row_factory=dict_row)

@asynccontextmanager
async def lifespan(_app):
    initialize_database()
    yield

app = FastAPI(title='Onboarding Platform', lifespan=lifespan)

class Customer(BaseModel):
    id: str | None = None
    tenant_id: str
    user_id: str
    name: str
    email: EmailStr
    product: str = 'Application'
    subscription_status: str = 'TRIAL'
    trial_start_date: datetime
    trial_end_date: datetime
    created_at: datetime

class Login(BaseModel):
    email: EmailStr
    password: str

def initialize_database():
    with closing(connection()) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS onboarding_customers (
            id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            name TEXT NOT NULL, email TEXT NOT NULL, product TEXT NOT NULL,
            trial_start_date TIMESTAMPTZ NOT NULL, trial_end_date TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL)''')
        conn.execute('ALTER TABLE onboarding_customers DROP COLUMN IF EXISTS subscription_status')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS onboarding_customers_user_id_uq
            ON onboarding_customers(user_id)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS subscription_plans (
            id UUID PRIMARY KEY, name TEXT NOT NULL, price_minor INTEGER NOT NULL,
            currency TEXT NOT NULL, entitlements TEXT[] NOT NULL DEFAULT '{}',
            description TEXT NOT NULL DEFAULT ''
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
            id UUID PRIMARY KEY, customer_id UUID NOT NULL REFERENCES onboarding_customers(id) ON DELETE CASCADE,
            plan_id UUID NOT NULL REFERENCES subscription_plans(id), status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, trial_end_at TIMESTAMPTZ,
            period_end_at TIMESTAMPTZ, canceled_at TIMESTAMPTZ
        )''')
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
        conn.execute('''INSERT INTO subscription_plans
            (id, name, price_minor, currency, entitlements, description)
            VALUES (%s, 'Onboarding Free Trial', 0, 'INR', '{}', '')
            ON CONFLICT (id) DO NOTHING''', (FREE_TRIAL_PLAN_ID,))
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
    with closing(connection()) as conn:
        existing = conn.execute(
            'SELECT id FROM onboarding_customers WHERE user_id=%s',
            (customer.user_id,),
        ).fetchone()
        customer_id = str(existing['id']) if existing else (customer.id or str(uuid4()))
        conn.execute('''INSERT INTO onboarding_customers
            (id, tenant_id, user_id, name, email, product,
             trial_start_date, trial_end_date, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
            user_id=EXCLUDED.user_id, name=EXCLUDED.name, email=EXCLUDED.email,
            product=EXCLUDED.product, trial_start_date=EXCLUDED.trial_start_date,
            trial_end_date=EXCLUDED.trial_end_date''',
            (customer_id, customer.tenant_id, customer.user_id, customer.name,
             customer.email, customer.product, customer.trial_start_date,
             customer.trial_end_date, customer.created_at))
        conn.execute('''INSERT INTO subscriptions
            (id, customer_id, plan_id, status, created_at, trial_end_at, period_end_at)
            VALUES (%s, %s, %s, 'trialing', %s, %s, %s)
            ON CONFLICT DO NOTHING''',
            (str(uuid4()), customer_id, FREE_TRIAL_PLAN_ID, customer.created_at,
             customer.trial_end_date, customer.trial_end_date))
        conn.commit()
    customer.id = customer_id
    return {'customer': customer}

def customer_query(where=''):
    return f'''SELECT *, CASE
                   WHEN trial_end_date < NOW() THEN 'EXPIRED'
                   WHEN trial_end_date <= NOW() + INTERVAL '7 days' THEN 'EXPIRING_SOON'
                   ELSE 'TRIAL'
               END AS status
               FROM onboarding_customers {where} ORDER BY created_at DESC'''

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

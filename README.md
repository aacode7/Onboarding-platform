# Onboarding Platform

The platform receives customers from any connected application and makes them visible to administrators. The host app is `frontend/apps/shell`.

## Run

```bash
cd frontend
npm install
npm --workspace apps/dashboard run dev -- --port 5001
npm --workspace apps/customers run dev -- --port 5002
# in another terminal
npm --workspace apps/shell run dev
```

The feature applications are independently structured and composed through Module Federation.

Run the API with `ONBOARDING_API_KEY=change-me uvicorn backend.main:app --reload --port 8001`.

Admin login returns a 15-minute access JWT and a 7-day refresh JWT. Configure separate stable `ONBOARDING_ACCESS_TOKEN_SECRET` and `ONBOARDING_REFRESH_TOKEN_SECRET` values.

Connected applications send `POST /api/onboarding/customers` with `Authorization: Bearer <ONBOARDING_API_KEY>`. The JSON body contains `tenant_id`, `user_id`, `name`, `email`, `trial_start_date`, `trial_end_date`, and `created_at`; the platform generates the customer `id`, while `product` and `package_id` can be supplied per application.

Docker runs the frontend and backend containers while connecting to PostgreSQL installed on the host laptop. Configure `POSTGRES_PASSWORD` in the shell environment when it differs from the local onboarding database password.

The shell is the host application. Dashboard is exposed at port 5001 and Customers (including Customer Details) at port 5002 through Module Federation.

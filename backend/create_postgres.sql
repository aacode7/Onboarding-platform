-- Run this file as a PostgreSQL administrator.
-- Replace the password before running it.
CREATE USER onboarding_app WITH PASSWORD 'replace-with-a-strong-password';
CREATE DATABASE onboarding_db OWNER onboarding_app;
GRANT ALL PRIVILEGES ON DATABASE onboarding_db TO onboarding_app;

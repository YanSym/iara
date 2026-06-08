-- IAra — initial Postgres database bootstrap
-- Run automatically by docker-compose on first container start.
-- Alembic migrations create the application schema; this script only
-- creates extensions and any database-level settings needed beforehand.

-- Required for UUID primary keys
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Required for pgcrypto (HMAC in application-level hashing, if needed)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Required for pg_stat_statements (slow-query observability)
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Ensure the iara user has sufficient privileges
GRANT ALL PRIVILEGES ON DATABASE iara_dev TO iara;

-- Default search_path for the iara user (public schema)
ALTER USER iara SET search_path TO public;

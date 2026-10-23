-- Private backend schema: do not add courtcast to Supabase's exposed schemas.
CREATE SCHEMA IF NOT EXISTS courtcast;
REVOKE ALL ON SCHEMA courtcast FROM PUBLIC, anon, authenticated, service_role;
CREATE TABLE IF NOT EXISTS courtcast.predictions (
    sequence bigserial UNIQUE NOT NULL,
    entry_key text PRIMARY KEY,
    payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS prediction_game ON courtcast.predictions
    ((payload->>'league'), (payload->>'player'), (payload->>'date'));
CREATE TABLE IF NOT EXISTS courtcast.documents (key text PRIMARY KEY, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS courtcast.budgets (key text PRIMARY KEY, used bigint NOT NULL, expires double precision NOT NULL);
CREATE TABLE IF NOT EXISTS courtcast.leases (token text PRIMARY KEY, resource text NOT NULL, expires double precision NOT NULL);
CREATE INDEX IF NOT EXISTS lease_resource ON courtcast.leases(resource);
CREATE TABLE IF NOT EXISTS courtcast.snapshots (key text PRIMARY KEY, payload text NOT NULL);
REVOKE ALL ON ALL TABLES IN SCHEMA courtcast FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA courtcast FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA courtcast REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA courtcast REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated, service_role;
ALTER TABLE courtcast.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE courtcast.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE courtcast.budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE courtcast.leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE courtcast.snapshots ENABLE ROW LEVEL SECURITY;

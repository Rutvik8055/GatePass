-- PostgreSQL-ready logical schema for production migrations.
-- The runnable local edition initializes an equivalent normalized SQLite schema.
CREATE TABLE users (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','operator','viewer')), active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE gates (id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'Active', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE vehicles (id BIGSERIAL PRIMARY KEY, number TEXT UNIQUE NOT NULL, vehicle_type TEXT NOT NULL, model TEXT, first_visit_at TIMESTAMPTZ NOT NULL, last_visit_at TIMESTAMPTZ NOT NULL, total_visits INTEGER NOT NULL DEFAULT 0);
CREATE INDEX idx_vehicles_number ON vehicles(number);
CREATE TABLE visits (id BIGSERIAL PRIMARY KEY, public_id TEXT UNIQUE NOT NULL, group_name TEXT NOT NULL, mobile TEXT, tourist_count INTEGER NOT NULL CHECK (tourist_count > 0), vehicle_id BIGINT NOT NULL REFERENCES vehicles(id), entry_gate TEXT NOT NULL, exit_gate TEXT, entry_operator TEXT NOT NULL, exit_operator TEXT, entered_at TIMESTAMPTZ NOT NULL, exited_at TIMESTAMPTZ, status TEXT NOT NULL CHECK (status IN ('INSIDE','EXITED')), qr_token TEXT UNIQUE NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
CREATE INDEX idx_visits_status ON visits(status); CREATE INDEX idx_visits_entered_at ON visits(entered_at); CREATE INDEX idx_visits_mobile ON visits(mobile);

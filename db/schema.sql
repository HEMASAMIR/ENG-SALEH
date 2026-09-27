CREATE DATABASE medical_database WITH
ENCODING = "UTF8"
TEMPLATE = template0;

\c medical_database

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Users & Access Management

CREATE TABLE roles (
    role_id SERIAL PRIMARY KEY, 
    role_name VARCHAR(50) UNIQUE NOT NULL, 
    description TEXT,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    user_name VARCHAR(50) UNIQUE NOT NULL, 
    password_hash TEXT NOT NULL, 
    full_name VARCHAR(255) NOT NULL,
    role_id INT REFERENCES roles(role_id) NOT NULL, 
    last_login TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    password_last_change TIMESTAMPTZ NOT NULL DEFAULT NOW(), 
    failed_attempts INT DEFAULT 0, 
    is_disabled BOOLEAN DEFAULT FALSE, 
    created_by INT REFERENCES users(id) ON DELETE SET NULL, 
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE permissions (
    permission_id SERIAL PRIMARY KEY,
    permission_name VARCHAR(255) UNIQUE NOT NULL, 
    description TEXT,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE role_permission (
    role_id INT NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id INT NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE user_permissions (
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission_id INT NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, permission_id)
);

-- Audit Trail

CREATE TYPE audit_action_type AS ENUM ('APPEND', 'EDIT', 'DELETE', 'LOG_IN', 'LOG_OUT', 'NAVIGATE', 'EXPORT', 'BACKUP');

CREATE TABLE audit_trail (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), 
    audit_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id INT NOT NULL REFERENCES users(id),
    action_type audit_action_type NOT NULL,
    location_screen VARCHAR(255),
    old_value JSONB DEFAULT NULL,
    new_value JSONB DEFAULT NULL, 
    reason TEXT DEFAULT NULL, 
    signature_format TEXT NOT NULL,
    signature_value TEXT,
    signature_meaning TEXT
);

REVOKE UPDATE, DELETE ON audit_trail FROM PUBLIC;

-- Recipes 

CREATE TYPE recipe_status AS ENUM ('ACTIVE', 'INACTIVE');

CREATE TABLE recipes (
    recipe_id SERIAL PRIMARY KEY, 
    recipe_number INT NOT NULL, 
    recipe_version INT NOT NULL, 
    UNIQUE (recipe_number, recipe_version),
    created_by INT REFERENCES users(id), 
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL, 
    status recipe_status NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX uniq_active_recipe
ON recipes (recipe_number)
WHERE status = 'ACTIVE';

CREATE TABLE recipe_parameters (
    parameter_id SERIAL PRIMARY KEY, 
    recipe_id INT NOT NULL REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    parameter_name TEXT NOT NULL, 
    UNIQUE (recipe_id, parameter_name),
    current_value JSONB NOT NULL, 
    min_value JSONB, 
    max_value JSONB, 
    unit TEXT,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    last_activity TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE batches (
    batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_number VARCHAR(100) UNIQUE NOT NULL,
    recipe_id INT NOT NULL REFERENCES recipes(recipe_id),
    started_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL DEFAULT 'RUNNING',
    operator_id INT NOT NULL REFERENCES users(id)
);
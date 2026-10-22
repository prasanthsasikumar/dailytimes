-- Daily Times schema. Plain types only, so the same file runs on SQLite and Postgres.
-- Dates/timestamps are ISO-8601 text (UTC for timestamps). Booleans are integers.
-- Tables are prefixed dt_ so they can share a Supabase project's public schema.

create table if not exists dt_users (
    id text primary key,
    email text unique,                 -- null for accounts created by the admin CLI
    created_at text not null,
    timezone text not null default 'UTC',
    interests text not null default '{}',   -- JSON, see autobrief.Interests
    edition_size text not null default 'page',  -- page | slip
    auto_enabled integer not null default 1,
    auto_after text not null default '05:30',   -- local time after which an auto edition may be built
    auto_built_on text,                -- local date of the last auto-edition attempt
    send_token_hash text unique,       -- write-only token for AI agents
    session_epoch integer not null default 0    -- bump to sign out everywhere
);

create table if not exists dt_devices (
    id text primary key,
    user_id text not null,
    name text not null,
    token_hash text not null unique,
    created_at text not null,
    last_seen_at text
);
create index if not exists dt_devices_user on dt_devices (user_id);

create table if not exists dt_pairings (
    code_hash text primary key,
    user_id text not null,
    device_name text not null,
    expires_at text not null
);

create table if not exists dt_logins (
    token_hash text primary key,
    email text not null,
    created_at text not null,
    expires_at text not null
);

create table if not exists dt_briefs (
    id text primary key,
    user_id text not null,
    brief_date text not null,
    title text,
    blocks text not null,              -- JSON list of blocks
    content_hash text not null,
    source text not null,              -- mcp | webhook | email | auto
    status text not null,              -- pending | superseded
    created_at text not null
);
create index if not exists dt_briefs_queue on dt_briefs (user_id, brief_date, status);

create table if not exists dt_receipts (
    brief_id text not null,
    device_id text not null,
    brief_date text not null,
    status text not null,              -- printed | failed
    attempts integer not null default 0,
    detail text,
    updated_at text not null,
    primary key (brief_id, device_id)
);
create index if not exists dt_receipts_device on dt_receipts (device_id, brief_date);

create table if not exists dt_history (
    user_id text not null,
    fingerprint text not null,
    printed_on text not null,
    primary key (user_id, fingerprint)
);

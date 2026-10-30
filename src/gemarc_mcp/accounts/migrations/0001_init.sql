-- 0001_init: accounts schema (design 5.2). Only TEXT/INTEGER/BIGINT columns, epoch-second times (BIGINT: int4 overflows in 2038 on Postgres), app-generated TEXT ids.
-- Splitting rule: a statement ends at the line whose last non-space character is the semicolon. No other line may
-- contain that character, comments included.
-- Note: there is deliberately no password column and no display-name column.
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  sub_bidx      TEXT NOT NULL UNIQUE,
  sub_enc       TEXT NOT NULL,
  email_bidx    TEXT NOT NULL,
  email_enc     TEXT NOT NULL,
  domain_bidx   TEXT NOT NULL,
  has_hd        INTEGER NOT NULL DEFAULT 0 CHECK (has_hd IN (0,1)),
  can_write     INTEGER NOT NULL DEFAULT 0 CHECK (can_write IN (0,1)),
  bidx_kid      TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at    BIGINT NOT NULL,
  last_login_at BIGINT
);
CREATE INDEX IF NOT EXISTS users_email_bidx_idx  ON users(email_bidx);
CREATE INDEX IF NOT EXISTS users_domain_bidx_idx ON users(domain_bidx);

CREATE TABLE IF NOT EXISTS sessions (
  id_hash         TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at      BIGINT NOT NULL,
  last_seen_at    BIGINT NOT NULL,
  idle_expires_at BIGINT NOT NULL,
  expires_at      BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS api_tokens (
  id           TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL DEFAULT 'pat',
  name         TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  scope        TEXT NOT NULL CHECK (scope IN ('read','write')),
  secret_hash  TEXT NOT NULL,
  created_at   BIGINT NOT NULL,
  expires_at   BIGINT NOT NULL,
  last_used_at BIGINT,
  revoked_at   BIGINT
);
CREATE INDEX IF NOT EXISTS api_tokens_user_idx ON api_tokens(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
  id       TEXT PRIMARY KEY,
  ts       BIGINT NOT NULL,
  event    TEXT NOT NULL,
  user_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
  ip_hash  TEXT,
  ref      TEXT,
  code     TEXT
);
CREATE INDEX IF NOT EXISTS audit_ts_idx   ON audit_log(ts);
CREATE INDEX IF NOT EXISTS audit_user_idx ON audit_log(user_id, ts);

CREATE TABLE IF NOT EXISTS rate_limits (
  key_hash     TEXT NOT NULL,
  window_start BIGINT NOT NULL,
  count        INTEGER NOT NULL,
  PRIMARY KEY (key_hash, window_start)
);

CREATE TABLE IF NOT EXISTS oauth_states (
  state_hash   TEXT PRIMARY KEY,
  nonce_hash   TEXT NOT NULL,
  binding_hash TEXT NOT NULL,
  created_at   BIGINT NOT NULL,
  expires_at   BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_states_expires_idx ON oauth_states(expires_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version    TEXT PRIMARY KEY,
  checksum   TEXT NOT NULL,
  applied_at BIGINT NOT NULL
);

-- 0002_admin: the operator area (administrator role and runtime settings).
-- Two ADDITIVE tables: `users` is not touched, so an existing deployment upgrades without rewriting a single row
-- and without a lock on the table the sign-in path writes to.
CREATE TABLE IF NOT EXISTS admin_users (
  user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  granted_at BIGINT NOT NULL,
  granted_by TEXT
);
CREATE TABLE IF NOT EXISTS operator_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at BIGINT NOT NULL
);

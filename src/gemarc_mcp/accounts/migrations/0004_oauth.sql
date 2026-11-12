-- 0004_oauth: the OAuth 2.1 tables (design 3.16, ADR-020). Four additive tables: the registered
-- clients, the authorization codes, the access tokens and the refresh tokens. `users` and
-- `api_tokens` are not touched, so an existing deployment upgrades without rewriting a single row
-- and without a lock on the table the sign-in path writes to.
--
-- Only hashes are stored: `token_hash` is the SHA-256 of the secret with a domain separator. The
-- plaintext exists once, in the response that issued it. `resource` binds an access token to one
-- audience (RFC 8707). The empty string means "this deployment only" (the token was issued before
-- the audience was known, or the issuer did not ask for one).
CREATE TABLE IF NOT EXISTS oauth_clients (
  id             TEXT PRIMARY KEY,
  user_id        TEXT REFERENCES users(id) ON DELETE CASCADE,
  name           TEXT NOT NULL,
  redirect_uris  TEXT NOT NULL,
  grant_types    TEXT NOT NULL,
  response_types TEXT NOT NULL,
  created_at     BIGINT NOT NULL,
  expires_at     BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_clients_user_idx ON oauth_clients(user_id);

CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash      TEXT PRIMARY KEY,
  client_id      TEXT NOT NULL,
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  redirect_uri   TEXT NOT NULL,
  scope          TEXT NOT NULL CHECK (scope IN ('read','write')),
  resource       TEXT NOT NULL DEFAULT '',
  code_challenge TEXT NOT NULL,
  created_at     BIGINT NOT NULL,
  expires_at     BIGINT NOT NULL,
  used_at        BIGINT
);
CREATE INDEX IF NOT EXISTS oauth_codes_expires_idx ON oauth_codes(expires_at);

CREATE TABLE IF NOT EXISTS oauth_access_tokens (
  id           TEXT PRIMARY KEY,
  token_hash   TEXT NOT NULL UNIQUE,
  client_id    TEXT NOT NULL,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope        TEXT NOT NULL CHECK (scope IN ('read','write')),
  resource     TEXT NOT NULL DEFAULT '',
  created_at   BIGINT NOT NULL,
  expires_at   BIGINT NOT NULL,
  last_used_at BIGINT,
  revoked_at   BIGINT
);
CREATE INDEX IF NOT EXISTS oauth_access_user_idx ON oauth_access_tokens(user_id);
CREATE INDEX IF NOT EXISTS oauth_access_expires_idx ON oauth_access_tokens(expires_at);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
  id         TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  client_id  TEXT NOT NULL,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope      TEXT NOT NULL CHECK (scope IN ('read','write')),
  resource   TEXT NOT NULL DEFAULT '',
  chain_id   TEXT NOT NULL,
  created_at BIGINT NOT NULL,
  expires_at BIGINT NOT NULL,
  revoked_at BIGINT,
  rotated_to TEXT
);
CREATE INDEX IF NOT EXISTS oauth_refresh_chain_idx ON oauth_refresh_tokens(chain_id);
CREATE INDEX IF NOT EXISTS oauth_refresh_expires_idx ON oauth_refresh_tokens(expires_at);

-- The login round trip has to come back to the request that started it: the OAuth authorisation
-- page sends the reader to the Google sign-in and back again. The pending path is kept with the
-- one-time sign-in state, so it can never be forged and it dies with the state (ten minutes).
ALTER TABLE oauth_states ADD COLUMN return_to TEXT NOT NULL DEFAULT '';

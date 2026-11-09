-- 0003_user_settings: the per-user settings (DD-010 section 2). One row per account, created when the
-- account saves something for the first time. `tools` is a comma separated allow list and the empty
-- string means "no restriction". An empty `lang` means "the account never chose a language", so the
-- reader's own language decides, and an empty `render_profile` means the same for the render form.
-- `users` and `api_tokens` are not touched, so an existing deployment upgrades without rewriting a
-- single row and without a lock on the table the sign-in path writes to.
CREATE TABLE IF NOT EXISTS user_settings (
  user_id        TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  lang           TEXT NOT NULL DEFAULT '' CHECK (lang IN ('','zh-CN','ja-JP','en-US')),
  tools          TEXT NOT NULL DEFAULT '',
  render_profile TEXT NOT NULL DEFAULT '' CHECK (render_profile IN ('','mvp','static','vite')),
  updated_at     BIGINT NOT NULL
);

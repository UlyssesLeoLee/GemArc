"""Completeness rules of gemarc_mcp.accounts.config (design 3.0, 3.9, 6)."""
from __future__ import annotations

import unittest

from gemarc_mcp.accounts import config as cfg
from gemarc_mcp.accounts.crypto import b64u

KEY = "k1:" + b64u(bytes(range(32)))
PG_OK = "postgresql://u:p@ep-x-pooler.eu-1.aws.neon.tech/db?sslmode=verify-full&sslrootcert=system"


def good(**over):
    env = {
        "GEMARC_ACCOUNTS": "1",
        "GEMARC_ACCOUNTS_BASE_URL": "https://gem-arc.vercel.app",
        "GEMARC_GOOGLE_CLIENT_ID": "1234-abc.apps.googleusercontent.com",
        "GEMARC_GOOGLE_CLIENT_SECRET": "s3cr3t-value-zz",
        "GEMARC_ACCOUNTS_KEYS": KEY,
        "GEMARC_ACCOUNTS_DATABASE_URL": PG_OK,
        "GEMARC_ACCOUNTS_ALLOWED_EMAILS": "a@gmail.com",
    }
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def names(env, **kw):
    return {(p.var, p.reason) for p in cfg.load(env, **kw).problems}


class Complete(unittest.TestCase):
    def test_good_config_is_ok_with_defaults(self):
        r = cfg.load(good())
        self.assertTrue(r.ok, r.describe())
        c = r.config
        self.assertEqual((c.max_users, c.session_idle_min, c.session_max_hours), (1000, 30, 8))
        self.assertEqual((c.pat_default_days, c.pat_max_days, c.pat_max_per_user), (30, 90, 10))
        self.assertEqual((c.pat_cache_seconds, c.pat_negcache_seconds, c.audit_retention_days), (30, 15, 30))
        self.assertEqual(c.allowed_domains, ("gmail.com",))
        self.assertTrue(c.secure_cookies)
        self.assertEqual(c.origin, "https://gem-arc.vercel.app")
        self.assertEqual(c.host, "gem-arc.vercel.app")
        self.assertEqual(c.mcp_mode, "none")
        self.assertEqual(c.db.kind, "postgres")

    def test_each_required_variable_missing_reports_only_its_name(self):
        for var in ("GEMARC_ACCOUNTS_BASE_URL", "GEMARC_GOOGLE_CLIENT_ID", "GEMARC_GOOGLE_CLIENT_SECRET",
                    "GEMARC_ACCOUNTS_KEYS", "GEMARC_ACCOUNTS_DATABASE_URL"):
            r = cfg.load(good(**{var: None}))
            self.assertFalse(r.ok)
            self.assertIsNone(r.config)
            self.assertEqual(r.names(), [var], var)
            self.assertEqual(r.problems[0].reason, "missing")
            self.assertEqual(r.problems[0].code, "GEMARC_ACCT_001")

    def test_accounts_flag_must_be_exactly_one(self):
        self.assertEqual(names(good(GEMARC_ACCOUNTS=None)), {("GEMARC_ACCOUNTS", "not_enabled")})
        self.assertEqual(names(good(GEMARC_ACCOUNTS="true")), {("GEMARC_ACCOUNTS", "bad_flag")})

    def test_problem_text_never_contains_values(self):
        env = good(GEMARC_GOOGLE_CLIENT_SECRET="<changeme>", GEMARC_ACCOUNTS_KEYS="k1:" + b64u(b"\x09" * 32))
        text = " ".join(cfg.load(env).describe())
        self.assertNotIn("changeme", text)
        self.assertNotIn(b64u(b"\x09" * 32), text)

    def test_repr_hides_secrets(self):
        c = cfg.load(good()).config
        shown = repr(c) + str(cfg.load(good()))
        for secret in ("s3cr3t-value-zz", "1234-abc", KEY.split(":")[1], "u:p@", "a@gmail.com"):
            self.assertNotIn(secret, shown)

    def test_placeholder_client_values_rejected(self):
        for value in ("<client-id>", "your-client-id", "changeme", "xxx", "TODO"):
            self.assertIn(("GEMARC_GOOGLE_CLIENT_ID", "placeholder"), names(good(GEMARC_GOOGLE_CLIENT_ID=value)), value)

    def test_all_problems_are_reported_at_once(self):
        r = cfg.load({"GEMARC_ACCOUNTS": "1"})
        self.assertEqual(set(r.names()), {"GEMARC_ACCOUNTS_BASE_URL", "GEMARC_GOOGLE_CLIENT_ID",
                                          "GEMARC_GOOGLE_CLIENT_SECRET", "GEMARC_ACCOUNTS_KEYS",
                                          "GEMARC_ACCOUNTS_DATABASE_URL"})


class Keys(unittest.TestCase):
    def test_keyring_problems(self):
        cases = {
            "k1:" + b64u(bytes(range(31))): "length",
            "k1:" + b64u(bytes(range(33))): "length",
            "k1:" + b64u(b"\x01" * 32): "weak",
            "k1:" + b64u(bytes(range(32))) + ",k1:" + b64u(bytes(range(1, 33))): "duplicate_kid",
            "nokid": "format",
            "K1:" + b64u(bytes(range(32))): "kid",
        }
        for text, reason in cases.items():
            self.assertEqual(names(good(GEMARC_ACCOUNTS_KEYS=text)), {("GEMARC_ACCOUNTS_KEYS", reason)}, reason)

    def test_two_keys_accepted_first_active(self):
        env = good(GEMARC_ACCOUNTS_KEYS="k2:" + b64u(bytes(range(1, 33))) + "," + KEY)
        c = cfg.load(env).config
        self.assertEqual(c.keyring.kids, ("k2", "k1"))


class BaseUrl(unittest.TestCase):
    def test_rules(self):
        ok = ["https://gem-arc.vercel.app", "https://example.org:8443", "http://localhost:8000", "http://127.0.0.1:8000",
              "http://[::1]:8000"]
        bad = ["http://gem-arc.vercel.app", "https://gem-arc.vercel.app/", "https://gem-arc.vercel.app/x",
               "https://gem-arc.vercel.app?x=1", "https://gem-arc.vercel.app#f", "ftp://x.org", "gem-arc.vercel.app",
               "https://u:p@gem-arc.vercel.app", "https://", "https://x.org:notaport"]
        for url in ok:
            self.assertEqual(cfg.load(good(GEMARC_ACCOUNTS_BASE_URL=url)).problems, (), url)
        for url in bad:
            self.assertEqual(names(good(GEMARC_ACCOUNTS_BASE_URL=url)), {("GEMARC_ACCOUNTS_BASE_URL", "invalid")}, url)

    def test_loopback_http_is_not_secure(self):
        c = cfg.load(good(GEMARC_ACCOUNTS_BASE_URL="http://localhost:8000")).config
        self.assertFalse(c.secure_cookies)
        self.assertEqual(c.host, "localhost:8000")


class DatabaseUrl(unittest.TestCase):
    def test_tls_matrix(self):
        h = "postgresql://u:p@ep-x-pooler.neon.tech/db"
        full = h + "?sslmode=verify-full&sslrootcert=system"
        self.assertEqual(cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL=full)).problems, ())
        self.assertEqual(cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL=h.replace("postgresql", "postgres") + "?sslmode=verify-full&sslrootcert=/etc/ca.pem")).problems, ())
        insecure = {h + "?sslmode=disable", h + "?sslmode=allow", h + "?sslmode=prefer", h, h + "?sslmode=verify-full",
                    h + "?sslmode=verify-full&sslrootcert=", h + "?sslmode=bogus"}
        for url in insecure:
            for relaxed in (None, "1"):
                self.assertEqual(names(good(GEMARC_ACCOUNTS_DATABASE_URL=url, GEMARC_ACCOUNTS_DB_TLS_RELAXED=relaxed)),
                                 {("GEMARC_ACCOUNTS_DATABASE_URL", "tls_insecure")}, (url, relaxed))
        for mode in ("require", "verify-ca"):
            url = h + "?sslmode=" + mode
            self.assertEqual(names(good(GEMARC_ACCOUNTS_DATABASE_URL=url)), {("GEMARC_ACCOUNTS_DATABASE_URL", "tls_insecure")})
            r = cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL=url, GEMARC_ACCOUNTS_DB_TLS_RELAXED="1"))
            self.assertTrue(r.ok)
            self.assertIn("db_tls_relaxed", r.warnings)

    def test_loopback_host_needs_no_tls(self):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            self.assertEqual(cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL=f"postgresql://u@{host}:5432/db")).problems, (), host)

    def test_scheme_and_garbage(self):
        for url in ("mysql://x/db", "http://x/db", "postgresql:///db", "postgresql://h:abc/db", "nonsense"):
            self.assertEqual(len(cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL=url)).problems), 1, url)

    def test_duplicate_sslmode_rejected(self):
        url = "postgresql://u@h.example/db?sslmode=verify-full&sslmode=disable&sslrootcert=system"
        self.assertEqual(names(good(GEMARC_ACCOUNTS_DATABASE_URL=url)), {("GEMARC_ACCOUNTS_DATABASE_URL", "invalid")})

    def test_selection_order_and_no_fallthrough_on_invalid_first(self):
        env = good(GEMARC_ACCOUNTS_DATABASE_URL=None)
        env.update(DATABASE_URL="postgresql://u@localhost/a", POSTGRES_URL="postgresql://u@localhost/b")
        self.assertEqual(cfg.load(env).config.db.var, "DATABASE_URL")
        del env["DATABASE_URL"]
        self.assertEqual(cfg.load(env).config.db.var, "POSTGRES_URL")
        env2 = good(GEMARC_ACCOUNTS_DATABASE_URL="postgresql://u@h.example/db?sslmode=disable", DATABASE_URL="postgresql://u@localhost/a")
        self.assertEqual(names(env2), {("GEMARC_ACCOUNTS_DATABASE_URL", "tls_insecure")})
        env3 = good(GEMARC_ACCOUNTS_DATABASE_URL=None)
        env3.update(DATABASE_URL="postgresql://u@localhost/a", GEMARC_ACCOUNTS_DATABASE_URL="postgresql://u@localhost/z")
        self.assertEqual(cfg.load(env3).config.db.var, "GEMARC_ACCOUNTS_DATABASE_URL")

    def test_migration_order(self):
        self.assertEqual(cfg.migration_db_var({"DATABASE_URL": "x", "DATABASE_URL_UNPOOLED": "y"}), "DATABASE_URL_UNPOOLED")
        self.assertEqual(cfg.migration_db_var({"DATABASE_URL_UNPOOLED": "y", "GEMARC_MIGRATE_DATABASE_URL": "z"}),
                         "GEMARC_MIGRATE_DATABASE_URL")
        self.assertIsNone(cfg.migration_db_var({"DATABASE_URL": "x"}))

    def test_sqlite_gate(self):
        url = "sqlite:///C:/tmp/x.db"
        self.assertEqual(names(good(GEMARC_ACCOUNTS_DATABASE_URL=url)), {("GEMARC_ACCOUNTS_DATABASE_URL", "sqlite_not_allowed")})
        r = cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL=url, GEMARC_ACCOUNTS_ALLOW_SQLITE="1"))
        self.assertTrue(r.ok)
        self.assertEqual(r.config.db.sqlite_path, "C:/tmp/x.db")
        for bad in ("sqlite:///:memory:", "sqlite:///", "sqlite://x.db"):
            self.assertEqual(len(cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL=bad, GEMARC_ACCOUNTS_ALLOW_SQLITE="1")).problems), 1, bad)

    def test_unpooled_host_is_only_a_warning(self):
        r = cfg.load(good(GEMARC_ACCOUNTS_DATABASE_URL="postgresql://u@ep-x.neon.tech/db?sslmode=verify-full&sslrootcert=system"))
        self.assertTrue(r.ok)
        self.assertIn("db_host_not_pooled", r.warnings)


class Numbers(unittest.TestCase):
    def test_ranges(self):
        cases = [("GEMARC_ACCOUNTS_MAX_USERS", "-1"), ("GEMARC_ACCOUNTS_SESSION_IDLE_MIN", "4"),
                 ("GEMARC_ACCOUNTS_SESSION_IDLE_MIN", "241"), ("GEMARC_ACCOUNTS_SESSION_MAX_HOURS", "0"),
                 ("GEMARC_ACCOUNTS_SESSION_MAX_HOURS", "73"), ("GEMARC_ACCOUNTS_PAT_MAX_DAYS", "366"),
                 ("GEMARC_ACCOUNTS_PAT_DEFAULT_DAYS", "0"), ("GEMARC_ACCOUNTS_PAT_MAX_PER_USER", "51"),
                 ("GEMARC_ACCOUNTS_PAT_CACHE_SECONDS", "121"), ("GEMARC_ACCOUNTS_PAT_NEGCACHE_SECONDS", "61"),
                 ("GEMARC_ACCOUNTS_AUDIT_RETENTION_DAYS", "6"), ("GEMARC_ACCOUNTS_AUDIT_RETENTION_DAYS", "91"),
                 ("GEMARC_ACCOUNTS_TRUSTED_PROXY_HOPS", "0"), ("GEMARC_ACCOUNTS_TRUSTED_PROXY_HOPS", "6"),
                 ("GEMARC_ACCOUNTS_SESSION_MAX_HOURS", "abc"), ("GEMARC_ACCOUNTS_PAT_MAX_DAYS", "1.5")]
        for var, value in cases:
            self.assertEqual(len(names(good(**{var: value}))), 1, (var, value))
            self.assertEqual(cfg.load(good(**{var: value})).names(), [var])

    def test_boundaries_accepted_and_max_users_zero(self):
        env = good(GEMARC_ACCOUNTS_SESSION_IDLE_MIN="5", GEMARC_ACCOUNTS_SESSION_MAX_HOURS="72",
                   GEMARC_ACCOUNTS_PAT_MAX_DAYS="365", GEMARC_ACCOUNTS_PAT_CACHE_SECONDS="0",
                   GEMARC_ACCOUNTS_MAX_USERS="0", GEMARC_ACCOUNTS_TRUSTED_PROXY_HOPS="5",
                   GEMARC_ACCOUNTS_AUDIT_RETENTION_DAYS="7")
        r = cfg.load(env)
        self.assertTrue(r.ok, r.describe())
        self.assertEqual(r.config.max_users, 0)
        self.assertEqual(r.config.trusted_proxy_hops, 5)

    def test_default_days_cannot_exceed_cap(self):
        self.assertEqual(names(good(GEMARC_ACCOUNTS_PAT_MAX_DAYS="7")), {("GEMARC_ACCOUNTS_PAT_DEFAULT_DAYS", "exceeds_max")})
        self.assertTrue(cfg.load(good(GEMARC_ACCOUNTS_PAT_MAX_DAYS="7", GEMARC_ACCOUNTS_PAT_DEFAULT_DAYS="7")).ok)


class Flags(unittest.TestCase):
    CASES = [(None, "off"), ("", "off"), (" ", "off"), ("0", "off"), ("1", "on"), ("1 ", "on"), (" 1", "on"), ("1\n", "on"),
             ("true", "bad"), ("yes", "bad"), ("TRUE", "bad"), ("on", "bad"), ("01", "bad"), ("2", "bad")]

    def test_parse_flag_matrix(self):
        for raw, want in self.CASES:
            self.assertEqual(cfg.parse_flag(raw), want, repr(raw))

    def test_mcp_auth_matrix(self):
        want = {None: None, "": None, "none": "none", "env-token": "env-token", "pat": "pat", "pat ": "pat",
                "pat-or-env": "pat-or-env", "PAT": "bad", "Pat": "bad", "token": "bad", "pat,env": "bad"}
        for raw, expected in want.items():
            self.assertEqual(cfg.parse_mcp_auth(raw), expected, repr(raw))

    def test_effective_mode_table(self):
        tok = {"GEMARC_MCP_TOKEN": "t"}
        rows = [
            ({}, ("none", False)), (tok, ("env-token", False)),
            ({"GEMARC_REQUIRE_LOGIN": "1"}, ("pat", False)), ({"GEMARC_REQUIRE_LOGIN": "1", **tok}, ("pat-or-env", False)),
            ({"GEMARC_MCP_AUTH": "pat"}, ("pat", False)), ({"GEMARC_MCP_AUTH": "pat-or-env"}, ("pat-or-env", False)),
            ({"GEMARC_MCP_AUTH": "none", **tok}, (None, True)), ({"GEMARC_MCP_AUTH": "env-token"}, (None, True)),
            ({"GEMARC_REQUIRE_LOGIN": "1", "GEMARC_MCP_AUTH": "none"}, (None, True)),
            ({"GEMARC_REQUIRE_LOGIN": "1", "GEMARC_MCP_AUTH": "env-token", **tok}, (None, True)),
            ({"GEMARC_MCP_AUTH": "none"}, ("none", False)), ({"GEMARC_MCP_AUTH": "env-token", **tok}, ("env-token", False)),
            ({"GEMARC_REQUIRE_LOGIN": "true"}, (None, True)), ({"GEMARC_MCP_AUTH": "PAT"}, (None, True)),
        ]
        for env, expected in rows:
            self.assertEqual(cfg.evaluate_mcp_mode(env), expected, env)

    def test_bad_booleans_make_config_incomplete(self):
        for var in ("GEMARC_ACCOUNTS_ALLOW_SQLITE", "GEMARC_ACCOUNTS_AUTO_MIGRATE", "GEMARC_ACCOUNTS_DB_TLS_RELAXED",
                    "GEMARC_ACCOUNTS_DB_ROLE_RELAXED", "GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE",
                    "GEMARC_ACCOUNTS_VERIFY_SIG", "GEMARC_ACCOUNTS_MCP_HOST_ONLY"):
            self.assertEqual(names(good(**{var: "yes"})), {(var, "bad_flag")}, var)
            self.assertEqual(names(good(**{var: " 1\n"})), set(), var)

    def test_login_vars_bad_or_conflict_make_config_incomplete(self):
        self.assertEqual(names(good(GEMARC_REQUIRE_LOGIN="true")), {("GEMARC_REQUIRE_LOGIN", "bad_flag")})
        self.assertEqual(names(good(GEMARC_MCP_AUTH="PAT")), {("GEMARC_MCP_AUTH", "bad_value")})
        self.assertEqual(names(good(GEMARC_MCP_AUTH="none", GEMARC_MCP_TOKEN="t")), {("GEMARC_MCP_AUTH", "conflict")})
        self.assertEqual(names(good(GEMARC_MCP_AUTH="env-token")), {("GEMARC_MCP_AUTH", "conflict")})

    def test_trailing_newline_values_are_cleaned(self):
        self.assertTrue(cfg.load(good(GEMARC_ACCOUNTS="1\n", GEMARC_GOOGLE_CLIENT_ID="1234-abc\n")).ok)


class Policy(unittest.TestCase):
    def test_domains(self):
        c = cfg.load(good(GEMARC_ACCOUNTS_ALLOWED_DOMAINS=" Gmail.com , example.org ")).config
        self.assertEqual(c.allowed_domains, ("gmail.com", "example.org"))
        for bad in ("gmail", "a b.com", ",", "gmail.com,@x.org", "-x.com"):
            self.assertIn(("GEMARC_ACCOUNTS_ALLOWED_DOMAINS", "invalid"), names(good(GEMARC_ACCOUNTS_ALLOWED_DOMAINS=bad)), bad)

    def test_emails(self):
        c = cfg.load(good(GEMARC_ACCOUNTS_ALLOWED_EMAILS=" A@Gmail.com,b@gmail.com ")).config
        self.assertEqual(c.allowed_emails, ("a@gmail.com", "b@gmail.com"))
        for bad in ("nobody", "a@b@c.com", "@x.com", "x@"):
            self.assertIn(("GEMARC_ACCOUNTS_ALLOWED_EMAILS", "invalid"), names(good(GEMARC_ACCOUNTS_ALLOWED_EMAILS=bad)), bad)

    def test_shared_workspace_rule(self):
        open_env = good(GEMARC_ACCOUNTS_ALLOWED_EMAILS=None)
        self.assertTrue(cfg.load(open_env).ok)  # stateless toolset default
        self.assertTrue(cfg.load(open_env).config.open_registration)
        for ts in ("full", "FULL"):
            self.assertEqual(names(open_env, toolset=ts), {("GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE", "required")})
        self.assertEqual(names(dict(open_env, GEMARC_TOOLSET="full")), {("GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE", "required")})
        self.assertTrue(cfg.load(dict(open_env, GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE="1"), toolset="full").ok)
        self.assertTrue(cfg.load(good(), toolset="full").ok)  # an invite list is not open registration
        self.assertTrue(cfg.load(dict(open_env, GEMARC_ACCOUNTS_ALLOWED_DOMAINS="example.org"), toolset="full").ok)
        self.assertEqual(names(dict(open_env, GEMARC_ACCOUNTS_ALLOWED_DOMAINS="example.org,googlemail.com"), toolset="full"),
                         {("GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE", "required")})

    def test_privacy_url_must_be_https(self):
        self.assertTrue(cfg.load(good(GEMARC_ACCOUNTS_PRIVACY_URL="https://example.org/privacy")).ok)
        for bad in ("http://example.org/p", "example.org", "https://", "javascript:alert(1)"):
            self.assertEqual(names(good(GEMARC_ACCOUNTS_PRIVACY_URL=bad)), {("GEMARC_ACCOUNTS_PRIVACY_URL", "invalid")}, bad)


class Isolation(unittest.TestCase):
    def test_config_import_pulls_no_driver_or_cryptography(self):
        import subprocess
        import sys

        code = ("import sys; import gemarc_mcp.accounts.config; "
                "bad=[m for m in ('psycopg','cryptography') if m in sys.modules]; print(bad)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                             env={**__import__('os').environ}).stdout.strip()
        self.assertEqual(out, "[]")


if __name__ == "__main__":
    unittest.main()

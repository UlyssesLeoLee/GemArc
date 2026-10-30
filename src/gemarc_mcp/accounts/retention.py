"""Bounded retention clean-up (design 1.4 and 5.6): what the running app and ``python -m gemarc_mcp.accounts gc`` run.

Each pass deletes at most ``batch`` rows per table, so one login or token creation never pays for a backlog:

* ``audit_log``     rows older than the audit retention (default 30 days),
* ``rate_limits``   counters whose window ended more than 2 days ago,
* ``sessions``      rows that expired more than a day ago,
* ``api_tokens``    tokens revoked or expired more than 30 days ago,
* ``oauth_states``  rows past their expiry.

The app runs one pass at most every ``MIN_INTERVAL_S`` per instance (after a successful login or token creation); a
serverless instance with no traffic runs nothing, which is why ``gc`` exists for the operator (or a Vercel Cron).
Failures are swallowed by the caller: clean-up is best effort and must never fail a login.
"""
from __future__ import annotations

from typing import Callable, Dict

from . import audit as audit_mod
from . import oidc
from .db import Database
from .ratelimit import RateLimiter
from .sessions import SessionStore
from .tokens import TokenStore

MIN_INTERVAL_S = 300
BATCH = 200
MAX_ROUNDS = 200  # ``gc`` safety bound: 200 rounds of 200 rows per table


def run_once(db: Database, *, sessions: SessionStore, tokens: TokenStore, limiter: RateLimiter, retention_days: int,
             now: int, batch: int = BATCH) -> "Dict[str, int]":
    """One bounded pass; returns the number of rows deleted per table."""
    now = int(now)
    out: "Dict[str, int]" = {}
    steps: "list[tuple[str, Callable[[], int]]]" = [
        ("audit_log", lambda: _in_tx(db, lambda tx: audit_mod.purge(tx, now, retention_days, batch))),
        ("rate_limits", lambda: limiter.cleanup(now, batch)),
        ("sessions", lambda: sessions.cleanup(now, batch)),
        ("api_tokens", lambda: tokens.cleanup(now, batch)),
        ("oauth_states", lambda: _in_tx(db, lambda tx: tx.execute(oidc.CLEAN_STATE_SQL, (now, batch)))),
    ]
    for name, step in steps:
        try:
            out[name] = max(int(step()), 0)
        except Exception:  # one failing table must not stop the others; the next pass retries
            out[name] = 0
    return out


def run_until_empty(db: Database, *, sessions: SessionStore, tokens: TokenStore, limiter: RateLimiter,
                    retention_days: int, now: int, batch: int = BATCH) -> "Dict[str, int]":
    """Repeat ``run_once`` until a pass deletes nothing (or ``MAX_ROUNDS``); returns the rows removed per table."""
    removed_sum: "Dict[str, int]" = {}
    for _ in range(MAX_ROUNDS):
        done = run_once(db, sessions=sessions, tokens=tokens, limiter=limiter, retention_days=retention_days,
                        now=now, batch=batch)
        for key, value in done.items():
            removed_sum[key] = removed_sum.get(key, 0) + value
        if not any(done.values()):
            break
    return removed_sum


def _in_tx(db: Database, fn: Callable) -> int:
    with db.transaction() as tx:
        return fn(tx)

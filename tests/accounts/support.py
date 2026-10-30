"""Shared helpers for the accounts tests: temp SQLite database, fixed keyring, fake clock."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import Optional

from gemarc_mcp.accounts import schema
from gemarc_mcp.accounts.crypto import Crypto, KeyRing
from gemarc_mcp.accounts.db import Database

KEY1 = bytes(range(1, 33))
KEY2 = bytes(range(101, 133))
T0 = 1_800_000_000  # a fixed "now"


class Clock:
    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> int:
        self.now += seconds
        return self.now


def make_crypto(*pairs) -> Crypto:
    return Crypto(KeyRing(pairs or (("k1", KEY1),)))


class DbCase(unittest.TestCase):
    """A fresh migrated SQLite file per test."""

    migrate_on_setup = True

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gemarc-acct-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "t.db")
        self.db = Database.sqlite(self.path)
        self.clock = Clock()
        self.crypto = make_crypto()
        if self.migrate_on_setup:
            schema.migrate(self.db, self.clock())

    def dump(self) -> str:
        """Every table's every row as text (all tables, all columns), for leak scans."""
        parts = []
        with self.db.transaction() as tx:
            tables = [r["name"] for r in tx.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")]
            for name in sorted(tables):
                if name.startswith("sqlite_"):
                    continue
                for row in tx.fetchall("SELECT * FROM " + name):  # test-only helper, name comes from sqlite_master
                    parts.append(name + "|" + "|".join(str(v) for v in row.values()))
        return "\n".join(parts)

    def raw_bytes(self) -> bytes:
        with open(self.path, "rb") as fh:
            return fh.read()

    def count(self, table: str, where: str = "1 = 1", params=()) -> int:
        with self.db.transaction() as tx:
            return tx.fetchone("SELECT count(*) AS n FROM " + table + " WHERE " + where, params)["n"]  # test-only

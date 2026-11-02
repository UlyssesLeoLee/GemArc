"""The cold start reads the tool set the operator area selected (``api/index.py::_toolset_override``).

What is pinned here:

* without accounts the deployment keeps the environment's tool set;
* a SQLite deployment is NOT probed: building an application must never create a database as a side
  effect. The regression this pins: the probe opened the configured SQLite file, and a relative path
  made it create "x.db" in the working directory, which then broke the repository's own check that no
  untracked file sits in the root;
* a Postgres deployment reads the stored tool set, and a stored value that is not a known tool set is
  ignored (the environment's tool set stays in force);
* a database that cannot be reached keeps the environment's tool set.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from api.index import _toolset_override  # noqa: E402  (the repository root has to be importable first)
from gemarc_mcp.accounts import config as config_mod  # noqa: E402
from gemarc_mcp.accounts import schema  # noqa: E402
from gemarc_mcp.accounts.crypto import b64u  # noqa: E402
from gemarc_mcp.accounts.db import Database  # noqa: E402

KEY1 = bytes(range(1, 33))
TOOLSET = "toolset"


def env(db_url: str, **extra) -> "dict[str, str]":
    e = {
        "GEMARC_ACCOUNTS": "1",
        "GEMARC_ACCOUNTS_BASE_URL": "https://gem-arc.example",
        "GEMARC_GOOGLE_CLIENT_ID": "client.id.fake",
        "GEMARC_GOOGLE_CLIENT_SECRET": "client-secret-fake",
        "GEMARC_ACCOUNTS_KEYS": "k1:" + b64u(KEY1),
        "GEMARC_ACCOUNTS_DATABASE_URL": db_url,
    }
    e.update(extra)
    return e


def sqlite_config():
    result = config_mod.load(env("sqlite:///probe.db", GEMARC_ACCOUNTS_ALLOW_SQLITE="1"))
    assert result.config is not None, result.describe()
    return result.config


def postgres_config():
    # A loopback host: no TLS requirement, and nothing is contacted in these tests.
    result = config_mod.load(env("postgresql://user:pw@127.0.0.1:1/none"))
    assert result.config is not None, result.describe()
    return result.config


class ToolsetOverride(unittest.TestCase):
    """The lookup that happens once per cold start."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gemarc-toolset-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def migrated(self) -> Database:
        db = Database.sqlite(os.path.join(self.tmp, "t.db"))
        schema.migrate(db, 1_800_000_000)
        return db

    def store(self, db: Database, value: str) -> None:
        with db.transaction() as tx:
            tx.execute("INSERT INTO operator_settings (key, value, updated_at) VALUES (?, ?, ?)",
                       (TOOLSET, value, 1_800_000_000))

    def test_without_accounts_the_environment_tool_set_stays_in_force(self):
        self.assertEqual(_toolset_override(None), "")

    def test_a_sqlite_deployment_is_not_probed_and_creates_no_file(self):
        # The dev/test database. Probing it would open the file, and a relative path would land in
        # the working directory -- building an application must never create a database.
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(_toolset_override(sqlite_config()), "")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_a_postgres_deployment_reads_the_stored_tool_set(self):
        db = self.migrated()
        self.store(db, "stateless")
        with mock.patch.object(Database, "from_target", return_value=db):
            self.assertEqual(_toolset_override(postgres_config()), "stateless")

    def test_a_stored_value_that_is_not_a_tool_set_is_ignored(self):
        db = self.migrated()
        self.store(db, "not-a-toolset")
        with mock.patch.object(Database, "from_target", return_value=db):
            self.assertEqual(_toolset_override(postgres_config()), "")

    def test_a_database_that_cannot_be_reached_keeps_the_environment_tool_set(self):
        with mock.patch.object(Database, "from_target", side_effect=RuntimeError("no database")):
            self.assertEqual(_toolset_override(postgres_config()), "")


if __name__ == "__main__":
    unittest.main()

"""The client installer: one command, and no secret on the terminal.

MiniMax Code has no command of its own, so the entry is written by this
package. The installer touches the user's configuration file, so the tests
pin the two things that matter: other servers stay untouched, and the token
never leaves the machine through the output.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp import clients  # noqa: E402

TOKEN = "gat_" + "0" * 12 + "_" + "A" * 20 + "_" + "B" * 20
ENDPOINT = "https://gem-arc.vercel.app/mcp"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="gemarc-clients-")
        self.addCleanup(self.folder.cleanup)
        self.config = Path(self.folder.name) / "mcp.json"
        self.config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "other": {
                            "type": "stdio",
                            "command": "python",
                            "args": ["-m", "something"],
                        }
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _install(self, *extra):
        argv = [
            "minimax",
            "install",
            "--url",
            ENDPOINT,
            "--token",
            TOKEN,
            "--config",
            str(self.config),
            *extra,
        ]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = clients.main(argv)
        return code, buffer.getvalue()

    def _document(self):
        return json.loads(self.config.read_text(encoding="utf-8"))

    def test_the_command_writes_the_server_entry(self):
        code, output = self._install()
        self.assertEqual(code, 0, output)
        entry = self._document()["mcpServers"]["gemarc"]
        self.assertEqual(entry["type"], "http")
        self.assertEqual(entry["url"], ENDPOINT)
        self.assertEqual(entry["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertTrue(entry["enabled"])

    def test_the_other_servers_stay_untouched(self):
        self._install()
        other = self._document()["mcpServers"]["other"]
        self.assertEqual(other["command"], "python")
        self.assertEqual(other["args"], ["-m", "something"])

    def test_the_token_never_reaches_the_output(self):
        _, output = self._install()
        self.assertNotIn(TOKEN, output, "the token was printed")
        self.assertNotIn(TOKEN[:16], output, "the head of the token was printed")
        self.assertIn("headers  : Authorization", output)

    def test_the_token_comes_from_the_environment_when_it_is_not_an_argument(self):
        previous = os.environ.get(clients.TOKEN_ENV)
        os.environ[clients.TOKEN_ENV] = TOKEN
        self.addCleanup(
            lambda: os.environ.pop(clients.TOKEN_ENV, None)
            if previous is None
            else os.environ.__setitem__(clients.TOKEN_ENV, previous)
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = clients.main(
                ["minimax", "install", "--url", ENDPOINT, "--config", str(self.config)]
            )
        self.assertEqual(code, 0, buffer.getvalue())
        entry = self._document()["mcpServers"]["gemarc"]
        self.assertEqual(entry["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertIn(clients.TOKEN_ENV, buffer.getvalue())

    def test_a_missing_token_is_refused_with_the_way_out(self):
        previous = os.environ.pop(clients.TOKEN_ENV, None)
        if previous is not None:
            self.addCleanup(lambda: os.environ.__setitem__(clients.TOKEN_ENV, previous))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = clients.main(
                ["minimax", "install", "--url", ENDPOINT, "--config", str(self.config)]
            )
        self.assertEqual(code, 2)
        self.assertIn(clients.TOKEN_ENV, stderr.getvalue())

    def test_a_plain_http_endpoint_is_refused(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = clients.main(
                [
                    "minimax",
                    "install",
                    "--url",
                    "http://gem-arc.vercel.app/mcp",
                    "--token",
                    TOKEN,
                    "--config",
                    str(self.config),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("https", stderr.getvalue())

    def test_a_missing_workspace_is_refused(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = clients.main(
                [
                    "minimax",
                    "install",
                    "--url",
                    ENDPOINT,
                    "--token",
                    TOKEN,
                    "--config",
                    str(self.config),
                    "--local-workspace",
                    str(Path(self.folder.name) / "nowhere"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("workspace", stderr.getvalue())

    def test_the_local_server_is_written_with_its_workspace(self):
        workspace = Path(self.folder.name) / "ws"
        workspace.mkdir()
        code, output = self._install("--local-workspace", str(workspace))
        self.assertEqual(code, 0, output)
        entry = self._document()["mcpServers"][clients.LOCAL_SERVER_NAME]
        self.assertEqual(entry["type"], "stdio")
        self.assertEqual(entry["args"], ["-m", "gemarc_mcp"])
        self.assertEqual(entry["env"]["GEMARC_WORKSPACE_ROOT"], str(workspace))
        self.assertEqual(entry["env"]["GEMARC_ALLOW_WRITE"], "0")

    def test_status_reports_the_header_names_and_not_their_values(self):
        self._install()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = clients.main(["minimax", "status", "--config", str(self.config)])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Authorization", output)
        self.assertNotIn(TOKEN, output, "the status printed the token")

    def test_remove_drops_only_that_entry(self):
        self._install()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = clients.main(
                ["minimax", "remove", "--config", str(self.config), "--name", "gemarc"]
            )
        self.assertEqual(code, 0, buffer.getvalue())
        servers = self._document()["mcpServers"]
        self.assertNotIn("gemarc", servers)
        self.assertIn("other", servers)

    def test_the_file_is_owner_readable_only(self):
        if os.name == "nt":
            self.skipTest("the file mode bits are a posix notion")
        self._install()
        mode = self.config.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, "the configuration file must be owner readable only")

    def test_a_broken_file_does_not_take_the_installer_down(self):
        self.config.write_text("{not json", encoding="utf-8")
        code, output = self._install()
        self.assertEqual(code, 0, output)
        self.assertIn("gemarc", self._document()["mcpServers"])


class OAuthInstallerTests(unittest.TestCase):
    """The OAuth channel: no static token, and the deployment must offer the flow."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="gemarc-clients-oauth-")
        self.addCleanup(self.folder.cleanup)
        self.config = Path(self.folder.name) / "mcp.json"
        self.saved = clients.probe_oauth
        self.addCleanup(setattr, clients, "probe_oauth", self.saved)

    def _document(self):
        return json.loads(self.config.read_text(encoding="utf-8"))

    def test_the_oauth_entry_carries_no_static_token(self):
        clients.probe_oauth = lambda url, timeout=30.0: {
            "resource": "https://gem-arc.vercel.app",
            "authorization_servers": ["https://gem-arc.vercel.app"],
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = clients.main(
                ["minimax", "install", "--url", ENDPOINT, "--oauth", "--config", str(self.config)]
            )
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        entry = self._document()["mcpServers"]["gemarc"]
        self.assertEqual(entry["type"], "http")
        self.assertEqual(entry["url"], ENDPOINT)
        self.assertNotIn("headers", entry, "the OAuth channel presents no static credential")
        self.assertIn("oauth", output)

    def test_the_oauth_channel_never_asks_for_a_token(self):
        clients.probe_oauth = lambda url, timeout=30.0: {"authorization_servers": ["https://gem-arc.vercel.app"]}
        previous = os.environ.pop(clients.TOKEN_ENV, None)
        if previous is not None:
            self.addCleanup(lambda: os.environ.__setitem__(clients.TOKEN_ENV, previous))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = clients.main(
                ["minimax", "install", "--url", ENDPOINT, "--oauth", "--config", str(self.config)]
            )
        self.assertEqual(code, 0, buffer.getvalue())

    def test_a_deployment_without_the_oauth_flow_is_refused_and_nothing_is_written(self):
        def refuse(url, timeout=30.0):
            raise clients.CliError("this deployment does not offer the OAuth flow")

        clients.probe_oauth = refuse
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = clients.main(
                ["minimax", "install", "--url", ENDPOINT, "--oauth", "--config", str(self.config)]
            )
        self.assertEqual(code, 2)
        self.assertIn("OAuth", stderr.getvalue())
        self.assertFalse(self.config.exists(), "the broken entry must not be written")

    def test_status_tells_the_two_channels_apart(self):
        clients.probe_oauth = lambda url, timeout=30.0: {"authorization_servers": ["https://gem-arc.vercel.app"]}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            clients.main(
                ["minimax", "install", "--url", ENDPOINT, "--oauth", "--config", str(self.config)]
            )
            clients.main(["minimax", "status", "--config", str(self.config)])
        self.assertIn("oauth", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()

"""Install GemArc into an MCP client's configuration file.

MiniMax Code has no command line of its own: its MCP servers live in a JSON
file under the profile directory, and the desktop client offers an "authorize"
button that only speaks OAuth. This module writes the entry that the static
token channel needs, so installing GemArc into that client is one command.

    python -m gemarc_mcp.clients minimax install --url https://<project>.vercel.app/mcp
    python -m gemarc_mcp.clients minimax status
    python -m gemarc_mcp.clients minimax remove

The token never reaches the terminal: it is read from the environment (or
from an argument the caller already typed) and written straight into the
configuration file. Only the server name, the endpoint and the header names
are reported back.

Two channels, two shapes:

* the **token** channel writes a static ``Authorization`` header. The client
  never asks anything and never signs in.
* the **OAuth** channel (``--oauth``) writes **no** header at all, so the
  client's own "authorize" button discovers the deployment, registers itself
  and sends the user through the Google sign-in. That channel only exists on
  a deployment that serves the OAuth metadata (account mode, ADR-020); the
  installer checks that before it writes anything, because an entry whose
  metadata is missing is exactly what makes the button say "no authorization
  configuration found".

Other clients keep their own installers (Claude Code, Codex and Hermes all
ship a command that writes their configuration); this module only covers the
client that has none.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

#: The name of the server entry in the client's configuration file.
DEFAULT_SERVER_NAME = "gemarc"

#: The name of the local stdio server entry (the working copy of the code).
LOCAL_SERVER_NAME = "gemarc_local"

#: The environment variable that carries the personal access token.
TOKEN_ENV = "GEMARC_MCP_PAT"

#: The header that carries the token.
AUTH_HEADER = "Authorization"

#: The document a client reads before it starts the OAuth flow (RFC 9728).
OAUTH_METADATA_PATH = "/.well-known/oauth-protected-resource"


class CliError(Exception):
    """A refusal with a reason the caller can act on."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def config_path() -> Path:
    """The user level configuration file of MiniMax Code."""
    return Path.home() / ".minimax" / "mcp.json"


def read_config(path: Path) -> dict[str, Any]:
    """Read the configuration file; a missing or broken file reads as empty."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"mcpServers": {}}
    if not isinstance(parsed, dict):
        return {"mcpServers": {}}
    servers = parsed.get("mcpServers")
    if not isinstance(servers, dict):
        parsed["mcpServers"] = {}
    return parsed


def write_config(path: Path, document: dict[str, Any]) -> None:
    """Write the configuration file atomically, owner readable only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def resolve_token(explicit: Optional[str], env_name: str) -> tuple[str, str]:
    """The token and where it came from: an argument or the environment.

    The value itself is never part of the answer.
    """
    if explicit:
        return explicit, "argument"
    value = os.environ.get(env_name, "").strip()
    if value:
        return value, f"environment variable {env_name}"
    raise CliError(
        "no token: pass --token or set the environment variable "
        f"{env_name} (the token is issued on the account page)"
    )


def _server_entry(url: str, token: str, oauth: bool = False) -> dict[str, Any]:
    """The remote server entry.

    The token channel carries a static ``Authorization`` header; the OAuth
    channel carries **no** header at all, so the client runs its own
    authorisation flow instead of presenting a credential.
    """
    entry: "dict[str, Any]" = {"type": "http", "url": url}
    if not oauth:
        entry["headers"] = {AUTH_HEADER: "Bearer " + token}
    entry["enabled"] = True
    entry["configured"] = True
    return entry


def _local_entry(workspace: Path, allow_write: bool, python: str) -> dict[str, Any]:
    """The local stdio server: the working copy of the code, no token needed."""
    environment = {
        "GEMARC_WORKSPACE_ROOT": str(workspace),
        "GEMARC_ALLOW_WRITE": "1" if allow_write else "0",
        "PYTHONUTF8": "1",
    }
    source = Path(__file__).resolve().parents[1]
    if source.is_dir():
        environment["PYTHONPATH"] = str(source)
    return {
        "type": "stdio",
        "description": "GemArc local stdio MCP (workspace: %s)" % workspace,
        "command": python,
        "args": ["-m", "gemarc_mcp"],
        "env": environment,
        "enabled": True,
        "configured": True,
    }


def install(
    path: Path,
    url: str,
    token: str,
    name: str = DEFAULT_SERVER_NAME,
    local_workspace: Optional[Path] = None,
    allow_write: bool = False,
    python: str = "",
    oauth: bool = False,
) -> dict[str, Any]:
    """Write the server entry; the other servers in the file stay untouched."""
    document = read_config(path)
    servers = document.setdefault("mcpServers", {})
    existing = servers.get(name)
    entry = dict(existing) if isinstance(existing, dict) else {}
    if oauth:
        entry.pop("headers", None)  # the OAuth channel presents no static credential
    entry.update(_server_entry(url, token, oauth))
    servers[name] = entry
    if local_workspace is not None:
        servers[LOCAL_SERVER_NAME] = _local_entry(local_workspace, allow_write, python or sys.executable)
    write_config(path, document)
    return {"config": path, "server": name, "url": url, "servers": sorted(servers), "oauth": oauth}


def base_of(url: str) -> str:
    """The deployment base of an endpoint: ``https://host``."""
    parts = urlsplit(url)
    return "%s://%s" % (parts.scheme, parts.netloc)


def fetch_json(url: str, timeout: float = 30.0) -> "tuple[int, Any]":
    """GET a JSON document: the status and the parsed body.

    A network failure answers ``0`` and the class name of the error, so the
    caller can say what went wrong without an exception escaping.
    """
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(64 * 1024)
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read(64 * 1024)
        status = error.code
    except Exception as error:  # noqa: BLE001 - the reason is the class name only
        return 0, type(error).__name__
    try:
        return status, json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return status, None


def probe_oauth(url: str, timeout: float = 30.0) -> dict[str, Any]:
    """The deployment's OAuth metadata, or a refusal with the way out.

    An entry without a static token only works when the deployment answers the
    discovery document; otherwise the client's "authorize" button reports that
    it found no authorisation configuration. The check happens **before** the
    file is written, so the installer never leaves that broken entry behind.
    """
    status, document = fetch_json(base_of(url) + OAUTH_METADATA_PATH, timeout)
    if status == 0:
        raise CliError("the deployment did not answer (%s)" % document)
    if status != 200 or not isinstance(document, dict) or not document.get("authorization_servers"):
        raise CliError(
            "this deployment does not offer the OAuth flow: %s answered HTTP %s. "
            "Use the token channel instead (install without --oauth)." % (OAUTH_METADATA_PATH, status)
        )
    return document


def status(path: Path) -> list[dict[str, Any]]:
    """What the configuration holds: names, kinds, endpoints and header names."""
    document = read_config(path)
    servers = document.get("mcpServers", {})
    report = []
    for name in sorted(servers):
        entry = servers[name]
        if not isinstance(entry, dict):
            continue
        headers = entry.get("headers")
        header_names = sorted(headers) if isinstance(headers, dict) else []
        if entry.get("type") == "http":
            kind = "token" if AUTH_HEADER in header_names else "oauth"
        else:
            kind = "local"
        report.append(
            {
                "name": name,
                "type": entry.get("type", ""),
                "url": entry.get("url", ""),
                "enabled": entry.get("enabled", True),
                "headers": header_names,
                "auth": kind,
            }
        )
    return report


def remove(path: Path, name: str = DEFAULT_SERVER_NAME) -> bool:
    """Drop one server entry; other servers stay."""
    document = read_config(path)
    servers = document.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    write_config(path, document)
    return True


def check_endpoint(url: str, token: str, timeout: float = 30.0) -> tuple[int, str]:
    """Ask the endpoint to start a session; report the status, never the token."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "gemarc-client-installer", "version": "1"},
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            AUTH_HEADER: "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(200).decode("utf-8", "replace")
            return response.status, payload.strip()[:120]
    except urllib.error.HTTPError as error:
        payload = error.read(200).decode("utf-8", "replace")
        return error.code, payload.strip()[:120]
    except Exception as error:  # noqa: BLE001 - the reason is the class name only
        return 0, type(error).__name__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gemarc_mcp.clients",
        description="Install GemArc into an MCP client's configuration file.",
    )
    client = parser.add_subparsers(dest="client", required=True)
    minimax = client.add_parser("minimax", help="MiniMax Code (profile directory JSON)")
    action = minimax.add_subparsers(dest="action", required=True)

    install_parser = action.add_parser("install", help="write the server entry")
    install_parser.add_argument("--url", required=True, help="the remote MCP endpoint")
    install_parser.add_argument("--token", default=None, help="the token (prefer the environment)")
    install_parser.add_argument("--token-env", default=TOKEN_ENV, help="the environment variable to read")
    install_parser.add_argument("--name", default=DEFAULT_SERVER_NAME, help="the server name")
    install_parser.add_argument("--config", default=None, help="the configuration file to write")
    install_parser.add_argument("--local-workspace", default=None, help="also write the local stdio server")
    install_parser.add_argument("--allow-write", action="store_true", help="open the write tools locally")
    install_parser.add_argument("--python", default=sys.executable, help="the interpreter for the local server")
    install_parser.add_argument("--check", action="store_true", help="ask the endpoint to start a session")
    install_parser.add_argument(
        "--oauth",
        action="store_true",
        help="write no static token: the client signs in with the deployment's OAuth flow",
    )
    install_parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for the deployment")

    status_parser = action.add_parser("status", help="report what the configuration holds")
    status_parser.add_argument("--config", default=None, help="the configuration file to read")

    remove_parser = action.add_parser("remove", help="drop one server entry")
    remove_parser.add_argument("--name", default=DEFAULT_SERVER_NAME, help="the server name")
    remove_parser.add_argument("--config", default=None, help="the configuration file to write")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.client == "minimax":
            path = Path(args.config) if getattr(args, "config", None) else config_path()
            if args.action == "install":
                if not args.url.startswith("https://"):
                    raise CliError("the endpoint must be https")
                workspace = Path(args.local_workspace) if args.local_workspace else None
                if workspace is not None and not workspace.is_dir():
                    raise CliError("the local workspace does not exist: %s" % workspace)
                if args.oauth:
                    # The check comes first: an entry without a static token only works on a
                    # deployment that serves the OAuth metadata.
                    metadata = probe_oauth(args.url, args.timeout)
                    result = install(path, args.url, "", args.name, workspace, args.allow_write, args.python,
                                     oauth=True)
                    print("config   :", result["config"])
                    print("server   :", result["server"])
                    print("endpoint :", result["url"])
                    print("auth     : oauth (the client signs in; no static token is written)")
                    print("issuer   :", ", ".join(metadata.get("authorization_servers") or []))
                    print("servers  :", ", ".join(result["servers"]))
                    if args.check:
                        status_code, document = fetch_json(base_of(args.url) + OAUTH_METADATA_PATH, args.timeout)
                        print("check    : HTTP %s %s" % (status_code, "metadata" if document else "-"))
                    return 0
                token, origin = resolve_token(args.token, args.token_env)
                result = install(path, args.url, token, args.name, workspace, args.allow_write, args.python)
                print("config   :", result["config"])
                print("server   :", result["server"])
                print("endpoint :", result["url"])
                print("headers  :", AUTH_HEADER)
                print("token    : from %s" % origin)
                print("servers  :", ", ".join(result["servers"]))
                if args.check:
                    status_code, detail = check_endpoint(args.url, token)
                    print("check    : HTTP %s %s" % (status_code, detail))
                return 0
            if args.action == "status":
                report = status(path)
                if not report:
                    print("no server configured in", path)
                    return 0
                for entry in report:
                    print(
                        "%-14s %-6s %-6s %-44s headers=%s enabled=%s"
                        % (
                            entry["name"],
                            entry["type"],
                            entry["auth"],
                            entry["url"],
                            ",".join(entry["headers"]) or "-",
                            entry["enabled"],
                        )
                    )
                return 0
            if args.action == "remove":
                if remove(path, args.name):
                    print("removed", args.name, "from", path)
                    return 0
                print("no entry named", args.name, "in", path)
                return 0
        raise CliError("unknown command")
    except CliError as error:
        print("error:", error, file=sys.stderr)
        return error.code


if __name__ == "__main__":
    raise SystemExit(main())

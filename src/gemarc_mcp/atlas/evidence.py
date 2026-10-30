"""Evidence verifier: does the cited anchor exist where the evidence says it does?

Local only (it reads files under the roots of ``roots.py``); stdlib only; never imports ``mcp``.

Anchor first
    The author writes ``(file, anchor, topic)``. ``lineRange`` is OUTPUT: the verifier resolves it from the anchor and
    proposes a stamp entry (``rangeSha256``, ``contextSha256``, ``occurrences``, ``fileSha256``, ``rootRev``) kept in
    ``<project>/.atlas/evidence-stamp.json``. A unique anchor is resolved, not repaired: when lines are inserted above
    it, its new line is simply the new output.

What is checked (every row, every failure reported, never first-only)
    existence, regular file, UTF-8 / BOM / CRLF (a NUL byte anywhere = binary), blank anchor (error: the vacuous pass),
    anchors with fewer than 6 non-blank characters (WARN, never PASS, never stamped), missing or blank ``topic`` and
    duplicate or malformed ``evidenceId`` (SCHEMA), ambiguous anchors (error until a stamp or a longer anchor pins one
    place), credential-looking anchors and topics (EV-006: keyword assignments, auth headers, known token formats and bare
    high-entropy tokens), explicit SKIP for an unmapped alias or a missing root, and EV-012 (``user:<name>`` quotes
    against ``<project>/atlas/user/<name>.txt``, WARN).

EV-005 (the stamped block)
    A stamp keeps the anchor line(s) and the lines around them (``context``, 3 each side). A unique anchor whose block
    changed stays PASS but carries RANGE_CONTENT_CHANGED, is not stampable and is listed by ``compact_report``; an anchor
    that occurs several times, whose stamped line is not recognisably the one standing at the stamped place (other text,
    or the number of occurrences changed), is WARN (a look-alike comment may have taken its place). ``update_stamp`` writes
    such a row only with ``accept_changes=True``.

Echo
    Source text leaves this module only through ``echo_line`` / ``echo_source_line`` (credential-like lines, sensitive
    files and every line of a private key block are withheld). Neighbour context of a missing anchor is the one stamped
    line, from code and prose files only.

Drift
    When a stamped anchor occurs several times and none of them is at the stamped place, the row FAILS with a repair
    SUGGESTION (stored context fingerprint, stored line text, nearest occurrence, file-level shift consensus) and a
    confidence. A suggestion is never applied by ``verify_evidence``; ``update_stamp`` applies one only for the ids a
    caller names.

Stated limits: the gate checks that the anchor exists where claimed, not that it supports the claim (the required
``topic`` is the claim text); line based, no symbol tracking; fuzzy matching, file-move search and a git-history adapter
are not implemented.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import model
from . import roots as _roots

STAMP_SCHEMA = "gemarc-atlas-evidence-stamp/1"
STAMP_RELATIVE_PATH = ".atlas/evidence-stamp.json"
USER_TEXT_DIR = "atlas/user"
USER_ROOT_REV = "user-text"

MIN_ANCHOR_CHARS = 6
WIDE_RANGE = 40
CONTEXT_RADIUS = 3
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_OCCURRENCES = 500
MAX_REVIEW_LINES = 10      # compact_report lists this many "PASS but review" rows, then one summary line
USER_NAME_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,79}$", re.ASCII)

STATUSES = ("PASS", "WARN", "FAIL", "SKIP")

# --------------------------------------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------------------------------------

# name -> (code, rule id of the registry, meaning). ``rule`` is what the AI-facing report prints.
_CODES: dict[str, tuple[str, str, str]] = {
    "SCHEMA": ("GEMARC_ATLAS_EV_001", "EV-003", "row is missing a field or a field has the wrong type"),
    "ANCHOR_EMPTY": ("GEMARC_ATLAS_EV_002", "EV-003", "blank anchor (the reference check would pass it vacuously)"),
    "PATH_REJECTED": ("GEMARC_ATLAS_EV_003", "EV-003", "file path is absolute, has a drive letter, '..' or an unusable name"),
    "PATH_ESCAPE": ("GEMARC_ATLAS_EV_004", "EV-003", "file path resolves outside the repository root"),
    "FILE_NOT_FOUND": ("GEMARC_ATLAS_EV_005", "EV-003", "no such file under the repository root"),
    "NOT_TEXT": ("GEMARC_ATLAS_EV_006", "EV-003", "binary, too large or not valid UTF-8"),
    "RANGE_INVALID": ("GEMARC_ATLAS_EV_007", "EV-003", "cited line range is malformed"),
    "ANCHOR_NOT_FOUND": ("GEMARC_ATLAS_EV_010", "EV-003", "anchor text does not occur in the file"),
    "ANCHOR_AMBIGUOUS": ("GEMARC_ATLAS_EV_011", "EV-003", "anchor occurs more than once and nothing pins one place"),
    "ANCHOR_DRIFT": ("GEMARC_ATLAS_EV_012", "EV-003", "ambiguous anchor is no longer at its stamped place"),
    "ANCHOR_WS_MISMATCH": ("GEMARC_ATLAS_EV_013", "EV-003", "anchor differs from the file only in whitespace or Unicode form"),
    "SECRET_IN_ANCHOR": ("GEMARC_ATLAS_EV_020", "EV-006", "anchor or topic looks like a credential"),
    "REPO_UNMAPPED": ("GEMARC_ATLAS_EV_030", "EV-003", "repository alias has no root (SKIP)"),
    "ROOT_MISSING": ("GEMARC_ATLAS_EV_031", "EV-003", "root directory does not exist (SKIP)"),
    "USER_TEXT_MISSING": ("GEMARC_ATLAS_EV_032", "EV-012", "saved requirement text not available (SKIP)"),
    "USER_NAME_INVALID": ("GEMARC_ATLAS_EV_033", "EV-012", "user:<name> is not a plain file name"),
    "USER_QUOTE_NOT_FOUND": ("GEMARC_ATLAS_EV_034", "EV-012", "quote is not in the saved requirement text (WARN)"),
    "ANCHOR_SHORT": ("GEMARC_ATLAS_EV_040", "EV-003", "anchor has fewer than 6 non-blank characters"),
    "ANCHOR_PINNED_AMBIGUOUS": ("GEMARC_ATLAS_EV_041", "EV-003", "anchor occurs several times; the stamp pins one"),
    "ANCHOR_MOVED": ("GEMARC_ATLAS_EV_042", "EV-003", "unique anchor resolved at a new line"),
    "RANGE_CONTENT_CHANGED": ("GEMARC_ATLAS_EV_043", "EV-005", "anchor present but the stamped block differs"),
    "ROOT_REV_CHANGED": ("GEMARC_ATLAS_EV_044", "EV-004", "root revision differs from the stamped one"),
    "PATH_CASE_MISMATCH": ("GEMARC_ATLAS_EV_045", "EV-003", "letter case differs from the name on disk"),
    "FILE_MIXED_EOL": ("GEMARC_ATLAS_EV_046", "EV-003", "file mixes LF and CRLF line endings"),
    "RANGE_WIDE": ("GEMARC_ATLAS_EV_047", "EV-003", "cited range is wide; a wide range hides drift"),
    "MULTI_IN_RANGE": ("GEMARC_ATLAS_EV_048", "EV-003", "anchor occurs several times inside the cited range"),
    "MATCH_NORMALIZED": ("GEMARC_ATLAS_EV_049", "EV-003", "anchor matched only after whitespace normalisation"),
    "STAMP_IGNORED": ("GEMARC_ATLAS_EV_050", "EV-005", "stamp entry ignored (the row changed since it was stamped)"),
    "CLAIM_EXCEEDS_EVIDENCE": ("GEMARC_ATLAS_EV_060", "EV-001", "claimed level is above what verified evidence supports"),
    "CLAIM_WITHOUT_EVIDENCE": ("GEMARC_ATLAS_EV_061", "EV-009", "claimed level needs evidence and has none"),
    "CLAIM_HAS_UNVERIFIED_EVIDENCE": ("GEMARC_ATLAS_EV_062", "EV-003", "some cited evidence did not verify"),
    "LEVEL_UNKNOWN": ("GEMARC_ATLAS_EV_063", "EV-001", "claimed level is not in the vocabulary"),
}
CODE_NAMES = tuple(_CODES)


class EvidenceError(ValueError):
    """The stamp file is unreadable or a caller argument is wrong."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def _diag(name: str, message: str, **extra) -> dict:
    code, rule, _ = _CODES[name]
    return {"rule": rule, "code": code, "name": name, "message": message, **extra}


# --------------------------------------------------------------------------------------------------------
# Redaction and the credential lint
# --------------------------------------------------------------------------------------------------------

_SENSITIVE_PATH_RE = re.compile(
    r"(^|/)\.env(\.[^/]*)?$|\.(pem|key|p12|pfx|jks|keystore)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)|(^|/)\.(netrc|pgpass|npmrc|pypirc)$"
    r"|secrets?\.|credentials?", re.IGNORECASE)
_DOTENV_RE = re.compile(r"(^|/)\.env(\.[^/]*)?$", re.IGNORECASE)
_KEYWORD = (r"(?:passw(?:or)?d|passwd|pwd|passphrase|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|auth[_-]?key"
            r"|credential|authorization|bearer|basic[_-]?auth|hook[_-]?(?:url|secret|token|key))")
_ASSIGN_RE = re.compile(_KEYWORD + r"\w*[\"']?\s*[:=]\s*(?P<value>[\"']?[^\s\"',;)]{1,})", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(
    r"^(?:<.*>|\$\{.*\}?|\{\{.*|\$[A-Za-z_]\w*|%[A-Za-z_]\w*%?|x{3,}|\*{3,}|\.{3,}|changeme|example|dummy|redacted|your[-_].*"
    r"|none|null|nil|true|false|undefined|required|optional|str|string|int|bool|boolean|bytes|secretstr|secret|token|password"
    r"|any|callable|dict|list|path|sequence|mapping|iterable|object|number|integer)$",
    re.IGNORECASE)
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("private-key-block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("provider-secret-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("json-web-token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("url-credentials", re.compile(r"://[^/\s:@]+:[^/\s@]{3,}@")),
    ("password-hash", re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{20,}")),
    ("payment-secret-key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{8,}")),
    ("telephony-sid", re.compile(r"\b(?:AC|SK)[0-9a-f]{32}\b")),
    ("maps-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("chat-hook-url", re.compile(r"hooks\.slack\.com/services/\S+")),
    ("package-registry-token", re.compile(r"\b(?:npm_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|pypi-[A-Za-z0-9_-]{30,})")),
)
_AUTH_SCHEME_RE = re.compile(r"\b(?:bearer|basic)\s+(?P<credential>[A-Za-z0-9._~+/=-]{12,})", re.IGNORECASE)
_BARE_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{20,}")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_SEPARATOR_RE = re.compile(r"[_/-]")
_WORDISH_RE = re.compile(r"[a-z]+|[A-Z]+|\d+|[A-Z][a-z]+")
_CASE_PIECE_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
MAX_TOKEN_CHARS = 2048


def _identifier_like(token: str) -> bool:
    """True for ``snake_case`` / ``kebab-case`` / path-like / ``camelCase`` runs made of ordinary words."""
    pieces = [p for p in _SEPARATOR_RE.split(token) if p]
    if len(pieces) > 1:
        return sum(1 for p in pieces if _WORDISH_RE.fullmatch(p)) * 2 >= len(pieces)
    words = _CASE_PIECE_RE.findall(token)
    if "".join(words) != token:
        return False
    short = sum(1 for w in words if len(w) < 3 and not w.isdigit())
    return short * 4 <= len(words)


def is_high_entropy_token(token: str) -> bool:
    """A bare run of 20+ characters from ``[A-Za-z0-9+/=_-]`` that looks random: digits and letters mixed, not made of
    ordinary words. Hex digests and keys, base64 blobs and bare API tokens qualify; identifiers and paths do not."""
    if len(token) < 20:
        return False
    if len(token) > MAX_TOKEN_CHARS:
        return True
    has_digit, has_alpha = any(c.isdigit() for c in token), any(c.isalpha() for c in token)
    if not (has_digit and has_alpha):
        return False
    if _HEX_RE.fullmatch(token):
        return True
    return not _identifier_like(token)


def _bare_token_finding(text: str) -> bool:
    return any(is_high_entropy_token(m.group()) for m in _BARE_TOKEN_RE.finditer(text))


def _auth_scheme_finding(text: str) -> bool:
    for match in _AUTH_SCHEME_RE.finditer(text):
        credential = match.group("credential")
        # an ordinary word (one case, or Capitalised) is prose; a digit, a symbol or mixed case inside is a credential
        word = credential.isalpha() and (credential.islower() or credential.isupper()
                                         or re.fullmatch(r"[A-Z][a-z]+", credential) is not None)
        if not word and not _PLACEHOLDER_RE.match(credential):
            return True
    return False


def is_sensitive_path(rel: str) -> bool:
    """Files whose content is never echoed (``.env*``, key stores, ``secrets.*``, ``credentials*``)."""
    return _SENSITIVE_PATH_RE.search(str(rel).replace("\\", "/")) is not None


def is_dotenv_path(rel: str) -> bool:
    return _DOTENV_RE.search(str(rel).replace("\\", "/")) is not None


def _literal_secret_value(value: str) -> bool:
    quoted = value[:1] in "\"'"
    bare = value.strip("\"'")
    if len(bare) < 4 or _PLACEHOLDER_RE.match(bare):
        return False
    if quoted:
        return True
    if re.search(r"[(\[]", bare):
        return False        # call or index syntax: code, not a literal
    # unquoted: letters and digits together (6+), or a plain 8+ character word / symbol run (no '_' '.' '/': not a name)
    if len(bare) >= 6 and re.search(r"[A-Za-z]", bare) is not None and re.search(r"\d", bare) is not None:
        return True
    return len(bare) >= 8 and re.fullmatch(r"[A-Za-z0-9!@#$%^&*+=~-]+", bare) is not None


def secret_findings(text) -> list[str]:
    """Names of the credential patterns found in ``text`` (empty when clean). Never returns matched text."""
    if not isinstance(text, str) or not text:
        return []
    found = [name for name, pattern in _SECRET_PATTERNS if pattern.search(text)]
    for match in _ASSIGN_RE.finditer(text):
        if _literal_secret_value(match.group("value")):
            found.append("keyword-assignment")
            break
    if _auth_scheme_finding(text):
        found.append("auth-header-credential")
    if _bare_token_finding(text):
        found.append("high-entropy-token")
    return found


def line_is_sensitive(line: str) -> bool:
    """Broader than the lint on purpose: a line that assigns anything to a credential-like key is not echoed."""
    return bool(secret_findings(line)) or _ASSIGN_RE.search(line) is not None


def echo_line(rel: str, line: str, limit: int = 100) -> str:
    """Withhold-or-trim for ONE line of text: sensitive files and credential-like lines are withheld. Source text leaves
    this module only through ``echo_line`` or ``echo_source_line`` (which also knows multi-line private key blocks)."""
    if is_sensitive_path(rel):
        return "[withheld: sensitive file]"
    if line_is_sensitive(line):
        return "[withheld: line looks sensitive]"
    text = line.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def echo_source_line(rel: str, src: "SourceText", number: int, limit: int = 100) -> str:
    """``echo_line`` for line ``number`` (1-based) of ``src``: a line inside a multi-line private key block is withheld too."""
    if is_sensitive_path(rel):
        return "[withheld: sensitive file]"
    if number in src.private_key_lines():
        return "[withheld: private key block]"
    return echo_line(rel, src.lines[number - 1] if 1 <= number <= len(src.lines) else "", limit)


#: file types whose lines may be shown as unrelated neighbour context (plain code and prose; never config or key files)
CONTEXT_ECHO_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".java", ".go", ".rs", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".cs", ".rb", ".php", ".kt", ".swift", ".sql", ".md", ".markdown", ".rst", ".txt"})


def context_echo_allowed(rel: str) -> bool:
    name = str(rel).replace("\\", "/").rsplit("/", 1)[-1].lower()
    dot = name.rfind(".")
    return dot > 0 and name[dot:] in CONTEXT_ECHO_SUFFIXES and not is_sensitive_path(rel)


def secret_lint_model(model_: Mapping) -> list[dict]:
    """EV-006 over a whole expanded model: evidence anchors and topics, and string values in ``props`` of nodes, edges,
    rooms and buildings. Pure; needs no roots, so a remote server can run it."""
    issues: list[dict] = []

    def scan(path: str, text) -> None:
        if isinstance(text, str):
            names = secret_findings(text)
            if names:
                issues.append(_diag("SECRET_IN_ANCHOR", f"{path} looks like a credential ({', '.join(names)}); "
                                                        "cite the key name, never the value", path=path))

    for index, row in enumerate(model_.get("evidences", ()) or ()):
        if isinstance(row, Mapping):
            scan(f"evidences[{index}].anchor", row.get("anchor"))
            scan(f"evidences[{index}].topic", row.get("topic"))

    def walk(path: str, value, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(f"{path}.{key}", item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(f"{path}[{index}]", item, depth + 1)
        elif isinstance(value, str):
            scan(path, value)

    space = model_.get("space", {}) or {}
    for kind in ("nodes", "edges"):
        for index, row in enumerate(model_.get(kind, ()) or ()):
            if isinstance(row, Mapping) and isinstance(row.get("props"), Mapping):
                walk(f"{kind}[{index}].props", row["props"])
    if isinstance(space, Mapping):
        for part in model.SPACE_PARTS:
            for index, row in enumerate(space.get(part, ()) or ()):
                if isinstance(row, Mapping) and isinstance(row.get("props"), Mapping):
                    walk(f"space.{part}[{index}].props", row["props"])
    return issues


# --------------------------------------------------------------------------------------------------------
# Source text
# --------------------------------------------------------------------------------------------------------


def _h16(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()[:16]


def _count_nonblank(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


class TextError(Exception):
    """``decode_text`` could not read the bytes as source text."""


_PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
_PEM_END_RE = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
MAX_PRIVATE_KEY_LINES = 200


class SourceText:
    """Decoded, LF-normalised text of one file with line tables and block fingerprints (1-based lines)."""

    def __init__(self, text: str, sha: str, *, bom: bool = False, crlf: bool = False, mixed_eol: bool = False):
        self.text, self.sha, self.bom, self.crlf, self.mixed_eol = text, sha, bom, crlf, mixed_eol
        self.lines = text.split("\n")
        self.line_count = len(self.lines) - 1 if self.lines and self.lines[-1] == "" else len(self.lines)
        starts, offset = [], 0
        for line in self.lines:
            starts.append(offset)
            offset += len(line) + 1
        self.starts = starts
        self._hashes: list[str] | None = None
        self._strip_hashes: list[str] | None = None
        self._windows: dict[int, list[str]] = {}
        self._window_counts: dict[int, dict[str, int]] = {}
        self._private: frozenset[int] | None = None
        self._freq: dict[str, int] | None = None

    @property
    def eol(self) -> str:
        return "mixed" if self.mixed_eol else ("crlf" if self.crlf else "lf")

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)

    def occurrences(self, anchor: str, cap: int = MAX_OCCURRENCES) -> list[tuple[int, int]]:
        """Non-overlapping exact occurrences as ``(first_line, last_line)``; multi-line anchors are supported."""
        needle = anchor.replace("\r\n", "\n")
        found: list[tuple[int, int]] = []
        if not needle:
            return found
        index = self.text.find(needle)
        while index >= 0 and len(found) < cap:
            found.append((self.line_of(index), self.line_of(index + max(0, len(needle) - 1))))
            index = self.text.find(needle, index + len(needle))
        return found

    def line_hashes(self) -> list[str]:
        if self._hashes is None:
            self._hashes = [hashlib.sha1(line.rstrip().encode("utf-8")).hexdigest()[:8] for line in self.lines]
        return self._hashes

    def block_hash(self, first: int, last: int) -> str:
        return _h16(",".join(self.line_hashes()[first - 1:last]))

    def strip_block_hash(self, first: int, last: int) -> str:
        """Fingerprint of lines ``first..last`` that ignores the amount and kind of whitespace (a re-indented or
        re-spaced file keeps it)."""
        if self._strip_hashes is None:
            self._strip_hashes = [hashlib.sha1(" ".join(line.split()).encode("utf-8")).hexdigest()[:8] for line in self.lines]
        return _h16(",".join(self._strip_hashes[first - 1:last]))

    def _window_hashes(self, size: int) -> list[str]:
        """Fingerprint of every window of ``size`` lines (index = first line - 1), computed once per size."""
        if size not in self._windows:
            hashes = self.line_hashes()
            self._windows[size] = [_h16(",".join(hashes[start:start + size]))
                                   for start in range(0, max(0, self.line_count - size + 1))]
        return self._windows[size]

    def find_blocks(self, size: int, target: str) -> list[int]:
        """First lines of every window of ``size`` lines whose fingerprint equals ``target``."""
        return [start + 1 for start, value in enumerate(self._window_hashes(size)) if value == target]

    def window_count(self, first: int, last: int) -> int:
        """How many windows of the same size as lines ``first..last`` hold exactly the same text (1 = unique block)."""
        size = last - first + 1
        if size not in self._window_counts:
            counts: dict[str, int] = {}
            for value in self._window_hashes(size):
                counts[value] = counts.get(value, 0) + 1
            self._window_counts[size] = counts
        return self._window_counts[size].get(self.block_hash(first, last), 1)

    def private_key_lines(self) -> frozenset[int]:
        """Line numbers inside a private key block (BEGIN line to END line); such lines are never echoed."""
        if self._private is None:
            marked: set[int] = set()
            if "-----BEGIN" in self.text:
                opened = 0
                for number, line in enumerate(self.lines, start=1):
                    if not opened:
                        begin = _PEM_BEGIN_RE.search(line)
                        if not begin:
                            continue
                        marked.add(number)
                        if not _PEM_END_RE.search(line, begin.end()):
                            opened = number
                        continue
                    marked.add(number)
                    if _PEM_END_RE.search(line) or number - opened >= MAX_PRIVATE_KEY_LINES:
                        opened = 0
            self._private = frozenset(marked)
        return self._private

    def line_frequency(self) -> dict[str, int]:
        """How often each stripped line text occurs as a whole line (a line that occurs twice has no unique substring)."""
        if self._freq is None:
            freq: dict[str, int] = {}
            for line in self.lines:
                key = line.strip()
                if key:
                    freq[key] = freq.get(key, 0) + 1
            self._freq = freq
        return self._freq


def decode_text(data: bytes, *, max_bytes: int = MAX_FILE_BYTES) -> SourceText:
    """Bytes -> ``SourceText``. Raises ``TextError`` for binary (a NUL byte anywhere), oversize or non-UTF-8 input.

    A UTF-8 BOM is removed and noted; CRLF becomes LF (noted); both must not change what an anchor matches.
    """
    if len(data) > max_bytes:
        raise TextError(f"larger than {max_bytes} bytes")
    if b"\0" in data:
        raise TextError("binary file (NUL byte)")
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = (data[3:] if bom else data).decode("utf-8")
    except UnicodeDecodeError as ex:
        raise TextError(f"not valid UTF-8 (byte {ex.start})") from None
    crlf_count = text.count("\r\n")
    bare_lf = text.count("\n") - crlf_count
    if crlf_count:
        text = text.replace("\r\n", "\n")
    return SourceText(text, _h16(data), bom=bom, crlf=crlf_count > 0, mixed_eol=crlf_count > 0 and bare_lf > 0)


# --------------------------------------------------------------------------------------------------------
# Options, stamp, claims
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyOptions:
    min_anchor_chars: int = MIN_ANCHOR_CHARS
    wide_range: int = WIDE_RANGE
    context_radius: int = CONTEXT_RADIUS
    max_bytes: int = MAX_FILE_BYTES
    normalized_match: bool = False     # opt-in: accept a whitespace/NFKC-only difference as a pass
    use_fingerprint: bool = True       # drift repair: stored context fingerprint
    use_consensus: bool = True         # drift repair: file-level shift consensus
    compute_root_rev: bool = True


def empty_stamp() -> dict:
    return {"schema": STAMP_SCHEMA, "entries": {}}


def stamp_path(project_dir: Path | str) -> Path:
    return Path(project_dir) / STAMP_RELATIVE_PATH


def load_stamp(project_dir: Path | str) -> dict:
    """The stamp of a project; an empty one when the file does not exist. Raises ``EvidenceError`` when it is unusable."""
    path = stamp_path(project_dir)
    if not path.is_file():
        return empty_stamp()
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError) as ex:
        raise EvidenceError("GEMARC_ATLAS_EV_070", f"{STAMP_RELATIVE_PATH} cannot be read: {ex}") from None
    if (not isinstance(data, dict) or data.get("schema") != STAMP_SCHEMA or not isinstance(data.get("entries"), dict)
            or any(not isinstance(v, dict) for v in data["entries"].values())):
        raise EvidenceError("GEMARC_ATLAS_EV_071", f"{STAMP_RELATIVE_PATH} is not a {STAMP_SCHEMA} document")
    return data


def stamp_text(stamp: Mapping) -> str:
    return json.dumps(stamp, ensure_ascii=False, sort_keys=True, indent=1, separators=(",", ": ")) + "\n"


def save_stamp(project_dir: Path | str, stamp: Mapping) -> Path:
    """Write the stamp (UTF-8, LF, sorted keys, no timestamp); atomic replace."""
    path = stamp_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(stamp_text(stamp).encode("utf-8"))
    os.replace(temp, path)
    return path


def merge_line_ranges(evidences: Iterable[Mapping], stamp: Mapping) -> list[dict]:
    """Copies of the evidence rows with ``lineRange`` filled from the stamp where the stamp still describes the row."""
    out = []
    for row in evidences:
        copy_ = dict(row)
        entry = (stamp.get("entries") or {}).get(row.get("evidenceId"))
        if entry and _entry_matches(entry, row) and _valid_range(entry.get("lineRange")):
            copy_["lineRange"] = list(entry["lineRange"])
        out.append(copy_)
    return out


def _norm_path(value: str) -> str:
    return value.replace("\\", "/")


def _entry_matches(entry: Mapping, row: Mapping) -> bool:
    return (entry.get("repository") == row.get("repository") and entry.get("filePath") == _norm_path(str(row.get("filePath", "")))
            and entry.get("anchorSha256") == _h16(str(row.get("anchor", ""))))


def _valid_range(value) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
            and 1 <= value[0] <= value[1])


def ceiling_of(source_kinds: Iterable[str], *, profile=None) -> str:
    """Strongest level the given source kinds can support (GemArc rank: Implemented above Observed); ``Unknown`` for none."""
    p = profile if profile is not None else model.vocab.default_profile()
    levels = []
    for kind in source_kinds:
        item = p.item("sourceKinds", kind)
        levels.append(item["verification"] if item else "Unknown")
    return max(levels, key=p.rank) if levels else "Unknown"


def check_claim(item: Mapping, evidence_by_id: Mapping[str, Mapping], results: Mapping[str, Mapping], *, profile=None) -> dict:
    """A node or edge claim against VERIFIED evidence: only PASS rows count towards the ceiling.

    ``results`` maps evidence id to a verification result (its ``status``). A claim may be at most its ceiling and a
    claim of Inferred or stronger needs evidence. Never auto-corrects; the hint says what to change.
    """
    p = profile if profile is not None else model.vocab.default_profile()
    ids = [i for i in (item.get("evidenceIds") or ())]
    passed = [evidence_by_id[i].get("sourceKind") for i in ids if (results.get(i) or {}).get("status") == "PASS" and i in evidence_by_id]
    claimed = item.get("verification")
    ceiling = ceiling_of(passed, profile=p)
    out = {"id": item.get("id"), "claimed": claimed, "ceiling": ceiling, "passedEvidence": len(passed), "totalEvidence": len(ids)}

    def verdict(status: str, name: str, **extra) -> dict:
        code, rule, _ = _CODES[name]
        return {**out, "status": status, "rule": rule, "code": code, "name": name, **extra}

    if p.rank(claimed) == 0:
        return verdict("FAIL", "LEVEL_UNKNOWN", hint=f"{claimed!r} is not a verification level")
    if not ids and p.rank(claimed) >= p.rank("Inferred"):
        return verdict("FAIL", "CLAIM_WITHOUT_EVIDENCE", hint=f"{claimed} needs evidence; add some or state Hypothetical or Unknown")
    if p.rank(claimed) > p.rank(ceiling):
        need = {"Implemented": "source-code", "Observed": "observation"}.get(claimed, "document")
        return verdict("FAIL", "CLAIM_EXCEEDS_EVIDENCE", hint=f"claims {claimed} but verified evidence supports at most {ceiling}; "
                                                             f"downgrade to {ceiling} or add {need} evidence that verifies")
    if ids and len(passed) < len(ids):
        return verdict("WARN", "CLAIM_HAS_UNVERIFIED_EVIDENCE", hint=f"{len(ids) - len(passed)} of {len(ids)} cited evidence did not verify")
    return {**out, "status": "PASS", "rule": "EV-001", "code": "GEMARC_ATLAS_EV_000", "name": "OK"}


# --------------------------------------------------------------------------------------------------------
# Root revision (EV-004)
# --------------------------------------------------------------------------------------------------------


def _git(root: Path, *args: str, timeout: float = 15.0) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=timeout, check=False,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


def root_revision(root: Path, files: Mapping[str, str]) -> str:
    """Revision label of what a root held when its files were read; never plain ``HEAD``.

    ``git:<12 hex>`` is the git tree hash of the root directory (a subtree hash inside a monorepo or mirror) and is
    used only when every CITED file is tracked and unmodified (an unrelated untracked or edited file elsewhere in the
    tree does not matter: the cited text equals the committed text). Otherwise ``manifest:<16 hex>``: sha256 over the
    sorted ``(path, file hash)`` pairs of the cited files.
    """
    cited = sorted(files)
    if cited and _git(root, "rev-parse", "--is-inside-work-tree") is not None:
        tracked: set[str] = set()
        dirty = False
        prefix = (_git(root, "rev-parse", "--show-prefix") or "").strip()
        for start in range(0, len(cited), 80):
            batch = cited[start:start + 80]
            listed = _git(root, "ls-files", "-z", "--", *batch)
            changed = _git(root, "status", "--porcelain", "--", *batch)
            if listed is None or changed is None:
                tracked, dirty = set(), True
                break
            tracked.update(part for part in listed.split("\0") if part)
            dirty = dirty or bool(changed.strip())
        if not dirty and tracked.issuperset(cited):
            tree = _git(root, "rev-parse", f"HEAD:{prefix.rstrip('/')}" if prefix else "HEAD^{tree}")
            if tree and re.fullmatch(r"[0-9a-f]{40,64}", tree.strip()):
                return "git:" + tree.strip()[:12]
    body = "\n".join(f"{path}\0{files[path]}" for path in cited)
    return "manifest:" + _h16(body)


# --------------------------------------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------------------------------------


class _Run:
    """State of one ``verify_evidence`` call."""

    def __init__(self, roots, opts: VerifyOptions, stamp: Mapping, project_dir, mode: str):
        self.roots = {str(k): Path(v) for k, v in roots.items()}
        self.opts, self.stamp, self.project_dir, self.mode = opts, stamp, project_dir, mode
        self.files: dict[str, SourceText | TextError | None] = {}
        self.dir_cache: dict = {}
        self.cited_files: dict[str, dict[str, str]] = {}   # alias -> {actual rel path: file sha}
        self.user_texts: dict[str, SourceText | None] = {}
        self.drift: list[dict] = []        # bookkeeping for the consensus pass
        self.references: list[dict] = []
        self.seen_ids: set[str] = set()
        self.files_read = 0

    def load(self, alias: str, resolved: "_roots.Resolved"):
        key = f"{alias}\0{resolved.rel}"
        if key not in self.files:
            try:
                size = os.stat(resolved.path).st_size
                if size > self.opts.max_bytes:
                    raise TextError(f"larger than {self.opts.max_bytes} bytes")
                self.files[key] = decode_text(resolved.path.read_bytes(), max_bytes=self.opts.max_bytes)
                self.files_read += 1
            except TextError as ex:
                self.files[key] = ex
            except OSError as ex:
                self.files[key] = TextError(f"cannot be read: {ex.strerror or ex}")
        return self.files[key]


def _fail(base: dict, name: str, message: str, status: str = "FAIL", **extra) -> dict:
    return {**base, "status": status, **_diag(name, message), **extra}


def _row_problem(row) -> str | None:
    if not isinstance(row, Mapping):
        return "row is not an object"
    for field in ("evidenceId", "filePath"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            return f"{field} must be a non-empty string"
    if not isinstance(row.get("anchor"), str):
        return "anchor must be a string"
    if not isinstance(row.get("topic"), str) or not row["topic"].strip():
        return "topic must be a non-empty string (what the anchor proves: the claim text)"
    if row.get("repository") is not None and not isinstance(row.get("repository"), str):
        return "repository must be a string"
    return None


def _span_hint(first: int, last: int) -> str:
    return f"L{first}" if first == last else f"L{first}-{last}"


def _decay(delta: int) -> float:
    return 1.0 / (1.0 + (abs(delta) / 200.0) ** 2)


def _entry_for(src: SourceText, row: Mapping, span: tuple[int, int], occurrences: int, opts: VerifyOptions) -> dict:
    first, last = span
    before = min(opts.context_radius, first - 1)
    after = min(opts.context_radius, src.line_count - last)
    return {
        "repository": row.get("repository") or "", "filePath": _norm_path(row["filePath"]), "lineRange": [first, last],
        "anchorSha256": _h16(row["anchor"]), "rangeSha256": src.block_hash(first, last), "context": [before, after],
        "contextSha256": src.block_hash(first - before, last + after), "occurrences": occurrences, "fileSha256": src.sha,
        "contextCount": src.window_count(first - before, last + after), "rootRev": None,
        # whitespace-insensitive identity of the anchor line(s) and of each side of the context (EV-005, ambiguous anchors)
        "lineSha256": src.strip_block_hash(first, last),
        "beforeSha256": src.strip_block_hash(first - before, first - 1) if before else None,
        "afterSha256": src.strip_block_hash(last + 1, last + after) if after else None,
    }


def _harden(src: SourceText, rel: str, anchor: str, line_no: int, limit: int = 40) -> str | None:
    """Shortest extension of a single-line anchor on its own line that occurs exactly once in the file."""
    if "\n" in anchor or not 1 <= line_no <= len(src.lines):
        return None
    line = src.lines[line_no - 1]
    if is_sensitive_path(rel) or line_is_sensitive(line) or line_no in src.private_key_lines():
        return None
    at = line.find(anchor)
    if at < 0:
        return None
    for extra in range(1, limit + 1):
        for left in range(0, extra + 1):
            start, end = max(0, at - left), min(len(line), at + len(anchor) + (extra - left))
            candidate = line[start:end]
            if candidate.strip() == anchor.strip() or "|" in candidate or candidate != candidate.strip() or secret_findings(candidate):
                continue
            if src.text.count(candidate) == 1:
                return candidate
    return None


def _ws_matches(src: SourceText, anchor: str, cap: int = 10) -> list[tuple[int, int]]:
    needle = _collapse(anchor)
    if not needle:
        return []
    width = len(anchor.split("\n"))
    found: list[tuple[int, int]] = []
    for size in sorted({width, width + 1}):
        for start in range(0, src.line_count - size + 1):
            if needle in _collapse(" ".join(src.lines[start:start + size])):
                found.append((start + 1, start + size))
                if len(found) >= cap:
                    return found
        if found:
            break
    return found


def _nearest(occ: Sequence[tuple[int, int]], position: float) -> list[tuple[int, int]]:
    return sorted(occ, key=lambda o: (abs(o[0] - position), o[0]))


def _suggest(src: SourceText, row: Mapping, occ: list[tuple[int, int]], cited: tuple[int, int], entry: Mapping | None,
             opts: VerifyOptions) -> dict:
    """Repair ladder for an ambiguous anchor that is not at its stamped place. A suggestion only."""
    rel = row["filePath"]
    stored = entry["lineRange"][0] if entry else (cited[0] + cited[1]) / 2
    why, method, confidence, exact = "", "nearest", 0.0, False
    chosen: tuple[int, int] | None = None
    edited = False
    capped = False      # confidence was limited because an identical twin makes the evidence ambiguous
    if entry and opts.use_fingerprint and entry.get("contextSha256") and isinstance(entry.get("context"), list):
        before, after = entry["context"]
        width = entry["lineRange"][1] - entry["lineRange"][0] + 1
        hits = src.find_blocks(before + width + after, entry["contextSha256"])
        inside = []
        for start in hits:
            span = [o for o in occ if o[0] == start + before and o[1] == start + before + width - 1]
            if span:
                inside.append(span[0])
        if len(inside) == 1:
            chosen, method, confidence, exact = inside[0], "fingerprint", 0.99, True
            why = "the stamped block (anchor line and its context) is unchanged at the new place: a pure move"
            if (entry.get("contextCount") or 1) > 1 or len(hits) > 1:
                # the same block exists elsewhere (it did when stamped, or it does now): a twin may be the real one
                confidence, exact, capped = 0.6, False, True
                why = "a block identical to the stamped one (anchor line and context) is at the new place, but the block is not unique in the file"
        elif len(inside) > 1:
            chosen = _nearest(inside, stored)[0]
            method, confidence = "fingerprint", 0.6
            why = f"{len(inside)} identical blocks match the stamped context; nearest to the stamped line"
        else:
            edited = True
    if chosen is None and entry and opts.use_fingerprint and entry.get("rangeSha256"):
        # the context moved or was touched, but the stamped line text itself may still single out one occurrence
        same = [o for o in occ if src.block_hash(o[0], o[1]) == entry["rangeSha256"]]
        if same:
            chosen, method = _nearest(same, stored)[0], "line"
            base = 0.9 if len(same) == 1 else max(0.4, 0.8 / math.sqrt(len(same)))
            confidence = round(base * _decay(chosen[0] - stored), 2)
            why = ("the stamped line text occurs at only one occurrence" if len(same) == 1
                   else f"nearest of {len(same)} occurrences whose line text equals the stamped line")
            edited = False
            if (entry.get("occurrences") or 1) > 3 or len(occ) > 3:
                # a repeated line says little about WHICH repeat was stamped
                confidence, capped = min(confidence, 0.79), True
                why += "; the line text is repeated, so this is a guess"
    if chosen is None:
        ordered = _nearest(occ, stored)
        chosen = ordered[0]
        base = 0.95 if len(occ) == 1 else max(0.4, 0.8 / math.sqrt(len(occ)))
        confidence = round(base * _decay(chosen[0] - stored), 2)
        why = "the only occurrence" if len(occ) == 1 else f"nearest of {len(occ)} occurrences to the stamped line"
        if edited:
            confidence = min(confidence, 0.5)
            why += "; the stamped block no longer exists verbatim (edited, not just moved)"
    if entry and entry.get("occurrences") not in (None, len(occ)):
        confidence = min(confidence, 0.4)
        why += f"; occurrence count changed {entry['occurrences']} -> {len(occ)}"
    others = [o[0] for o in _nearest([o for o in occ if o != chosen], stored)[:3]]
    suggestion = {
        "action": "SET_RANGE", "method": method, "confidence": confidence, "exact": exact,
        "suggested": {"lineRange": [chosen[0], chosen[1]]}, "anchorLines": [chosen[0]],
        "delta": int(chosen[0] - stored) if float(stored).is_integer() else round(chosen[0] - stored, 1),
        "lineText": echo_source_line(rel, src, chosen[0]),
        "alsoAt": others, "why": why, "needsReview": confidence < 0.8, "applied": False,
        "stamp": _entry_for(src, row, chosen, len(occ), opts),
    }
    if capped:
        suggestion["capped"] = True
    return suggestion


def _block_intact(src: SourceText, entry: Mapping, span: tuple[int, int]) -> bool:
    """True when the stamped anchor line(s) and their context are unchanged at ``span`` (a pure move of the stamped block)."""
    if src.block_hash(span[0], span[1]) != entry.get("rangeSha256"):
        return False
    before, after = entry.get("context") or [0, 0]
    first = span[0] - before
    return first >= 1 and span[1] + after <= src.line_count and src.block_hash(first, span[1] + after) == entry.get("contextSha256")


def _context_changed(src: SourceText, entry: Mapping, span: tuple[int, int]) -> bool:
    """True when the stamped anchor line(s), or the lines around them that were stamped (``context``), differ at ``span``.

    A stamp without context fingerprint (older documents) only compares the anchor line(s). EV-005."""
    if entry.get("rangeSha256") and src.block_hash(span[0], span[1]) != entry["rangeSha256"]:
        return True
    context = entry.get("context")
    if not entry.get("contextSha256") or not isinstance(context, list) or len(context) != 2:
        return False
    first, last = span[0] - context[0], span[1] + context[1]
    return first < 1 or last > src.line_count or src.block_hash(first, last) != entry["contextSha256"]


def _same_occurrence(src: SourceText, entry: Mapping, span: tuple[int, int], occurrences: int) -> bool:
    """Is what stands at ``span`` recognisably THE stamped occurrence of an anchor that occurs several times?

    Yes when the stamped line text is the same (ignoring whitespace), the number of occurrences is unchanged, and at least
    one side of the stamped context (the lines above, or the lines below) is still intact. A look-alike that slid into
    the place (or a comment or string with the same text) fails at least one of these. Stamps from before these fields
    existed fall back to the exact line hash and no side check."""
    if entry.get("occurrences") not in (None, occurrences):
        return False
    if entry.get("lineSha256"):
        if src.strip_block_hash(span[0], span[1]) != entry["lineSha256"]:
            return False
    elif src.block_hash(span[0], span[1]) != entry.get("rangeSha256"):
        return False
    if "beforeSha256" not in entry:
        return True
    context = entry.get("context") or [0, 0]
    sides = []
    if context[0]:
        sides.append(span[0] - context[0] >= 1 and entry.get("beforeSha256") is not None
                     and src.strip_block_hash(span[0] - context[0], span[0] - 1) == entry["beforeSha256"])
    else:
        sides.append(span[0] == 1)              # stamped at the top of the file: nothing above, and still nothing above
    if context[1]:
        sides.append(span[1] + context[1] <= src.line_count and entry.get("afterSha256") is not None
                     and src.strip_block_hash(span[1] + 1, span[1] + context[1]) == entry["afterSha256"])
    else:
        sides.append(span[1] == src.line_count)  # stamped at the end of the file, and still at the end
    return any(sides)


def _drift_hint(repair: Mapping, cited_label: str, count: int) -> str:
    low = " [LOW CONFIDENCE: re-read the file]" if repair["confidence"] < 0.5 else ""
    return (f"anchor at L{repair['anchorLines'][0]} (stamped {cited_label}, delta {repair['delta']:+g}) "
            f"[{count} matches, {repair['method']}, confidence {repair['confidence']}]{low}")


def _verify_source_row(row: Mapping, run: _Run, base: dict) -> dict:
    opts = run.opts
    alias = row.get("repository") or ""
    anchor, rel_in = row["anchor"], row["filePath"]
    warnings: list[dict] = base["warnings"]
    if not alias:
        return _fail(base, "REPO_UNMAPPED", "evidence has no repository alias (repo=<alias>)", "SKIP",
                     hint=f"set repo=<alias> (known: {', '.join(sorted(run.roots)) or 'none'})")
    root = run.roots.get(alias)
    if root is None:
        return _fail(base, "REPO_UNMAPPED", f"repository alias {alias!r} has no root in {_roots.ENV_NAME}", "SKIP",
                     hint=f"declare {alias}=<directory> in {_roots.ENV_NAME} on the server (known: {', '.join(sorted(run.roots)) or 'none'})")
    if not _roots.root_available(root):
        return _fail(base, "ROOT_MISSING", f"root of {alias!r} does not exist on this machine", "SKIP",
                     hint="check out the repository or fix the root; nothing was verified for this row")
    try:
        resolved = _roots.resolve_in_root(root, rel_in, dir_cache=run.dir_cache)
    except _roots.RootsError as ex:
        name = "PATH_ESCAPE" if ex.code == _roots.E_ESCAPE else "PATH_REJECTED"
        return _fail(base, name, ex.message, hint="cite a path relative to the repository root, inside it")
    if not resolved.exists or not _roots.is_regular_file(resolved.path):
        return _fail(base, "FILE_NOT_FOUND", f"no file {_norm_path(rel_in)} under {alias}", hint="re-cite the file (moved or deleted?)")
    src = run.load(alias, resolved)
    if isinstance(src, TextError):
        return _fail(base, "NOT_TEXT", f"{resolved.rel}: {src}", hint="cite a UTF-8 text source or document")
    run.cited_files.setdefault(alias, {})[resolved.rel] = src.sha
    observed = {"file": resolved.rel, "lineCount": src.line_count, "eol": src.eol, "bom": src.bom, "fileSha256": src.sha}
    if resolved.case_mismatch:
        warnings.append(_diag("PATH_CASE_MISMATCH", f"on disk: {resolved.case_mismatch}; works on Windows and macOS, fails on Linux CI"))
    if src.mixed_eol:
        warnings.append(_diag("FILE_MIXED_EOL", "file mixes LF and CRLF line endings"))
    if _count_nonblank(anchor) < opts.min_anchor_chars:
        warnings.append(_diag("ANCHOR_SHORT", f"anchor has fewer than {opts.min_anchor_chars} non-blank characters"))
    occ = src.occurrences(anchor)
    observed["occurrences"] = len(occ)
    base["observed"] = observed

    # what the row was last resolved to: the stamp when it still describes this row, else a cited lineRange
    entry = (run.stamp.get("entries") or {}).get(row["evidenceId"])
    if entry is not None and not _entry_matches(entry, {**row, "repository": alias}):
        warnings.append(_diag("STAMP_IGNORED", "the stamp entry belongs to another file or anchor; resolved from the anchor alone"))
        entry = None
    cited: tuple[int, int] | None = None
    if entry is not None and _valid_range(entry.get("lineRange")):
        cited = (entry["lineRange"][0], entry["lineRange"][1])
    elif row.get("lineRange") is not None:
        given = row["lineRange"]
        if not _valid_range(given):
            return _fail(base, "RANGE_INVALID", f"lineRange must be two integers with 1 <= from <= to, got {given!r}",
                         hint="lineRange is output of the verifier; delete it", observed=observed)
        cited = (given[0], given[1])
        if cited[1] - cited[0] + 1 > opts.wide_range:
            warnings.append(_diag("RANGE_WIDE", f"cited range is {cited[1] - cited[0] + 1} lines; a wide range hides drift"))
    key = f"{alias}\0{resolved.rel}"

    def passed(span: tuple[int, int], *, code_hint: str | None = None, changed: bool = False, extra: dict | None = None) -> dict:
        proposed = _entry_for(src, row, span, len(occ), opts)
        status = "PASS"
        result = {**base, "status": status, "rule": "EV-003", "code": "GEMARC_ATLAS_EV_000", "name": "OK",
                  "resolved": {"lineRange": [span[0], span[1]], "occurrences": len(occ), "unique": len(occ) == 1},
                  "stamp": proposed, "stampable": not changed, "observed": observed, **(extra or {})}
        if code_hint:
            result["hint"] = code_hint
        if cited is not None and not (cited[0] <= span[0] and span[1] <= cited[1]):
            result["resolved"]["movedFrom"] = [cited[0], cited[1]]
        if len(occ) == 1 and cited is not None:
            run.references.append({"key": key, "cited": cited, "found": span})
        return result

    if not occ:
        return _not_found(row, run, base, src, observed, cited, entry, resolved)
    if cited is None:
        if len(occ) == 1:
            return passed(occ[0])
        lines = [o[0] for o in occ[:5]]
        candidates = []
        for first, _ in occ[:3]:
            hardened = _harden(src, resolved.rel, anchor, first)
            if hardened:
                candidates.append({"line": first, "anchor": hardened})
        repair = {"action": "HARDEN_ANCHOR", "confidence": 0.8, "candidates": candidates, "applied": False,
                  "why": "a longer anchor that occurs once in the file pins one place"}
        return _fail(base, "ANCHOR_AMBIGUOUS", f"anchor occurs {len(occ)} times (lines {', '.join(map(str, lines))}"
                                               f"{', ...' if len(occ) > 5 else ''}); nothing pins one place",
                     hint="use a longer anchor that occurs once (see repair.candidates); the outline gives unique anchors",
                     observed=observed, repair=repair)
    # a cited place exists
    inside = [o for o in occ if cited[0] <= o[0] and o[1] <= cited[1]]
    if inside:
        chosen = _nearest(inside, (entry["lineRange"][0] if entry else cited[0]))[0]
        changed = False
        stale_drift = None
        if entry is not None and (len(occ) > 1 or (entry.get("occurrences") or 1) > 1):
            # the stamp pinned one of several occurrences: make sure the one found here is still THAT one
            if not _block_intact(src, entry, chosen) and opts.use_fingerprint:
                other = _suggest(src, row, occ, cited, entry, opts)
                elsewhere = other["suggested"]["lineRange"] != list(chosen)
                if len(occ) == 1 or (elsewhere and ((other["method"] == "fingerprint" and other["exact"])
                                                    or (other["method"] == "line" and other["confidence"] >= 0.8))):
                    stale_drift = other
                else:
                    changed = True
        elif entry is not None:
            changed = _context_changed(src, entry, chosen)     # EV-005 for a unique anchor, too
        if stale_drift is not None:
            run.drift.append({"key": key, "row": row, "cited": cited, "entry": entry, "occ": occ, "src": src})
            return _fail(base, "ANCHOR_DRIFT", "the stamped place now holds a different occurrence of the anchor text; "
                                               "the stamped block moved", hint=_drift_hint(stale_drift, _span_hint(*cited), len(occ)),
                         observed=observed, repair=stale_drift)
        result = passed(chosen, changed=changed)
        if changed:
            note = ("anchor is present but the stamped block differs; the cited section was edited (re-read it; the stamp "
                    "is not refreshed until update_stamp(accept_changes=True))")
            if len(occ) > 1 and not (entry is not None and _same_occurrence(src, entry, chosen, len(occ))):
                # one of several identical anchors, and what stands at the stamped place is not recognisably the stamped
                # occurrence (other line text, a changed number of occurrences, or both sides of the context replaced:
                # a comment or string with the same text appeared, the real one was deleted, a look-alike slid into the
                # place): this may be a different occurrence, so never a PASS
                result = {**result, "status": "WARN", **_diag("RANGE_CONTENT_CHANGED", note),
                          "hint": f"the anchor occurs {len(occ)} times and the stamped block around L{chosen[0]} changed: "
                                  "re-read the file and confirm this is the cited line, or use a longer anchor"}
                if entry is not None and opts.use_fingerprint:
                    other = _suggest(src, row, occ, cited, entry, opts)
                    if other["suggested"]["lineRange"] != list(chosen):
                        result["repair"] = other
                        result["hint"] += (f"; L{other['anchorLines'][0]} looks more like the stamped line "
                                           f"[{other['method']}, confidence {other['confidence']}]")
            else:
                result["warnings"].append(_diag("RANGE_CONTENT_CHANGED", note))
        if len(occ) > 1:
            result["warnings"].append(_diag("ANCHOR_PINNED_AMBIGUOUS", f"anchor occurs {len(occ)} times in the file; the stamp pins L{chosen[0]}"))
            hardened = _harden(src, resolved.rel, anchor, chosen[0])
            if hardened and "repair" not in result:
                result["repair"] = {"action": "HARDEN_ANCHOR", "confidence": 0.8, "suggested": {"anchor": hardened},
                                    "applied": False, "why": "a longer anchor that occurs once in the file"}
        if len(inside) > 1:
            result["warnings"].append(_diag("MULTI_IN_RANGE", f"anchor occurs {len(inside)} times inside the cited range"))
        return result
    if len(occ) == 1:
        if (entry is not None and (entry.get("occurrences") or 1) > 1 and opts.use_fingerprint
                and not _block_intact(src, entry, occ[0])):
            # the stamp pinned one of several occurrences, the others are gone, and the survivor is not recognisably
            # the pinned one: never silently move the stamp onto it
            repair = _suggest(src, row, occ, cited, entry, opts)
            run.drift.append({"key": key, "row": row, "cited": cited, "entry": entry, "occ": occ, "src": src})
            return _fail(base, "ANCHOR_DRIFT", f"the stamp pinned one of {entry['occurrences']} occurrences; one remains "
                                               f"at {_span_hint(*occ[0])} and it is not recognisably the pinned one",
                         hint=_drift_hint(repair, _span_hint(*cited), len(occ)), observed=observed, repair=repair)
        changed = entry is not None and _context_changed(src, entry, occ[0])
        result = passed(occ[0], changed=changed)
        result["warnings"].append(_diag("ANCHOR_MOVED", f"unique anchor resolved at {_span_hint(*occ[0])} (was {_span_hint(*cited)})"))
        if changed:
            result["warnings"].append(_diag("RANGE_CONTENT_CHANGED", "the line moved and its text or the lines around it changed; "
                                            "re-read it; the stamp is not refreshed until update_stamp(accept_changes=True)"))
        return result
    repair = _suggest(src, row, occ, cited, entry, opts)
    run.drift.append({"key": key, "row": row, "cited": cited, "entry": entry, "occ": occ, "src": src})
    return _fail(base, "ANCHOR_DRIFT", f"anchor is not at {_span_hint(*cited)} any more; it occurs {len(occ)} times in the file",
                 hint=_drift_hint(repair, _span_hint(*cited), len(occ)), observed=observed, repair=repair)


def _not_found(row: Mapping, run: _Run, base: dict, src: SourceText, observed: dict, cited, entry, resolved) -> dict:
    anchor = row["anchor"]
    warnings = base["warnings"]
    near = _ws_matches(src, anchor)
    if near:
        first = near[0]
        text = echo_source_line(resolved.rel, src, first[0])
        if run.opts.normalized_match:
            proposed = _entry_for(src, row, first, 1, run.opts)
            warnings.append(_diag("MATCH_NORMALIZED", "anchor matches only after whitespace/NFKC normalisation"))
            return {**base, "status": "PASS", "rule": "EV-003", "code": "GEMARC_ATLAS_EV_000", "name": "OK",
                    "resolved": {"lineRange": [first[0], first[1]], "occurrences": 1, "unique": True}, "stamp": proposed,
                    "stampable": False, "observed": observed}
        repair = {"action": "REPLACE_ANCHOR", "confidence": 0.9, "suggested": {"anchor": text if not text.startswith("[withheld") else ""},
                  "anchorLines": [first[0]], "applied": False}
        return _fail(base, "ANCHOR_WS_MISMATCH", "anchor differs from the file only in whitespace, width or Unicode form",
                     hint=f"same text at L{first[0]} after whitespace normalisation: {text!r}", observed=observed, repair=repair)
    # what the stamped line says now: ONE line, only where the verifier itself stamped it (a cited lineRange of a row is
    # not trusted: it would let a row read any line), only for plain code and prose files, always through the filter
    context: list[str] = []
    if entry is not None and _valid_range(entry.get("lineRange")) and context_echo_allowed(resolved.rel):
        centre = entry["lineRange"][0]
        if 1 <= centre <= src.line_count:
            context = [echo_source_line(resolved.rel, src, centre)]
    repair = {"action": "RE_EVIDENCE", "confidence": 0.0, "context": context, "applied": False}
    return _fail(base, "ANCHOR_NOT_FOUND", f"anchor text does not occur in {resolved.rel}", hint="content removed or reworded: "
                 "re-read the section and cite a new anchor", observed=observed, repair=repair)


def _verify_user_row(row: Mapping, run: _Run, base: dict) -> dict:
    """EV-012: a ``user:<name>`` quote against ``<project>/atlas/user/<name>.txt`` (whitespace-collapsed match)."""
    name = row["filePath"][len(model.USER_PREFIX):]
    anchor = row["anchor"]
    warnings = base["warnings"]
    if not USER_NAME_RE.fullmatch(name):
        return _fail(base, "USER_NAME_INVALID", f"user:{name} must be a plain file name (letters, digits, '_', '.', '-')",
                     hint="save the requirement text as atlas/user/<name>.txt and cite user:<name>")
    if run.project_dir is None:
        return _fail(base, "USER_TEXT_MISSING", "no project directory was given, so the saved requirement text cannot be read", "SKIP",
                     hint="the quote is unverified")
    user_dir = Path(run.project_dir) / USER_TEXT_DIR
    candidates = [name + ".txt"] + ([name] if name.lower().endswith((".txt", ".md", ".markdown")) else [])
    src = None
    for candidate in candidates:
        try:
            resolved = _roots.resolve_in_root(user_dir, candidate, dir_cache=run.dir_cache)
        except _roots.RootsError:
            continue
        if resolved.exists and _roots.is_regular_file(resolved.path):
            cache_key = str(resolved.path)
            if cache_key not in run.user_texts:
                try:
                    run.user_texts[cache_key] = decode_text(resolved.path.read_bytes(), max_bytes=run.opts.max_bytes)
                except (TextError, OSError):
                    run.user_texts[cache_key] = None
            src = run.user_texts[cache_key]
            break
    if src is None:
        return _fail(base, "USER_TEXT_MISSING", f"atlas/user/{name}.txt is not available, so the quote is unverified", "SKIP",
                     hint=f"save the requirement text verbatim as atlas/user/{name}.txt")
    if _count_nonblank(anchor) < run.opts.min_anchor_chars:
        warnings.append(_diag("ANCHOR_SHORT", f"anchor has fewer than {run.opts.min_anchor_chars} non-blank characters"))
    collapsed, offsets = [], []
    previous_space = True
    for index, ch in enumerate(src.text):
        if ch.isspace():
            if not previous_space:
                collapsed.append(" ")
                offsets.append(index)
            previous_space = True
        else:
            collapsed.append(ch)
            offsets.append(index)
            previous_space = False
    flat = "".join(collapsed)
    needle = re.sub(r"\s+", " ", anchor).strip()
    base["observed"] = {"file": f"atlas/user/{name}.txt", "lineCount": src.line_count, "eol": src.eol, "fileSha256": src.sha}
    at = flat.find(needle) if needle else -1
    if at < 0:
        return {**base, "status": "WARN", **_diag("USER_QUOTE_NOT_FOUND", f"the quote is not in atlas/user/{name}.txt (whitespace collapsed); "
                                                                         "a quote that is not in the requirement text is likely an invention"),
                "hint": "quote the requirement sentence verbatim, or save the missing text"}
    first_line = src.line_of(offsets[at])
    last_line = src.line_of(offsets[at + len(needle) - 1])
    count = 0
    cursor = flat.find(needle)
    while cursor >= 0 and count < MAX_OCCURRENCES:
        count += 1
        cursor = flat.find(needle, cursor + len(needle))
    entry = _entry_for(src, {**row, "repository": ""}, (first_line, last_line), count, run.opts)
    entry["rootRev"] = USER_ROOT_REV
    return {**base, "status": "PASS", "rule": "EV-012", "code": "GEMARC_ATLAS_EV_000", "name": "OK",
            "resolved": {"lineRange": [first_line, last_line], "occurrences": count, "unique": count == 1},
            "stamp": entry, "stampable": True}


def _short_anchor_demotion(result: dict, row: Mapping, opts: VerifyOptions) -> dict:
    """An anchor with fewer than ``min_anchor_chars`` non-blank characters proves next to nothing ('a' is found in almost
    any line): such a row is WARN at best, never PASS, and its stamp is never written (so no release gate counts it)."""
    if result.get("status") != "PASS" or _count_nonblank(row["anchor"]) >= opts.min_anchor_chars:
        return result
    out = {k: v for k, v in result.items() if k != "stamp"}
    out["warnings"] = [w for w in result.get("warnings", ()) if w["name"] != "ANCHOR_SHORT"]
    out.update({"status": "WARN", **_diag("ANCHOR_SHORT", f"anchor has fewer than {opts.min_anchor_chars} non-blank characters: "
                                                           "a match proves next to nothing"),
                "stampable": False, "hint": f"use an anchor of at least {opts.min_anchor_chars} non-blank characters (the outline gives unique ones)"})
    return out


def _verify_row(row, run: _Run, index: int = 0) -> dict:
    given = row.get("evidenceId") if isinstance(row, Mapping) else None
    # a malformed id (list, dict, blank) must not crash later bookkeeping: report it under a positional label
    label = given if isinstance(given, str) and given.strip() else f"<row {index + 1}>"
    base = {"evidenceId": label, "warnings": []}
    problem = _row_problem(row)
    if problem:
        return _fail(base, "SCHEMA", problem, hint="evidence rows are {evidenceId, repository, filePath, anchor, topic}")
    if label in run.seen_ids:
        return _fail(base, "SCHEMA", f"evidenceId {label!r} is already used by an earlier row; ids must be unique",
                     hint="give every evidence row its own id (a shared id would make the stamp describe only one of them)")
    run.seen_ids.add(label)
    if not row["anchor"].strip():
        return _fail(base, "ANCHOR_EMPTY", "anchor is blank; give a literal substring (at least 6 non-blank characters) of the cited file",
                     hint="a blank anchor would pass vacuously, so it is an error")
    for field in ("anchor", "topic"):
        names = secret_findings(row.get(field))
        if names:
            return _fail(base, "SECRET_IN_ANCHOR", f"{field} looks like a credential ({', '.join(names)}); the text is not echoed",
                         hint="cite the key name or the surrounding code, never the secret value")
    if is_dotenv_path(row["filePath"]) and re.search(r"=\s*\S", row["anchor"]) and re.search(_KEYWORD, row["anchor"], re.IGNORECASE):
        return _fail(base, "SECRET_IN_ANCHOR", "anchor in a .env file carries a value after a credential-like key; the text is not echoed",
                     hint="cite the key name only")
    kind = row.get("sourceKind")
    if row["filePath"].startswith(model.USER_PREFIX) or kind == "user-requirement":
        if not row["filePath"].startswith(model.USER_PREFIX):
            return _fail(base, "USER_NAME_INVALID", "a user-requirement evidence must be cited as user:<name>")
        return _short_anchor_demotion(_verify_user_row(row, run, base), row, run.opts)
    return _short_anchor_demotion(_verify_source_row(row, run, base), row, run.opts)


def _consensus(results: list[dict], run: _Run) -> None:
    """File-level shift consensus: rows with unique anchors tell how far each region of a file moved; an ambiguous
    drifted row prefers the occurrence whose implied shift agrees with its nearest neighbours. Suggestions only."""
    for item in run.drift:
        refs = [r for r in run.references if r["key"] == item["key"]]
        if not refs:
            continue
        cited = item["cited"]
        mid = (cited[0] + cited[1]) / 2

        def interval(ref) -> tuple[float, float]:
            return ref["found"][0] - ref["cited"][1], ref["found"][0] - ref["cited"][0]

        lower = [r for r in refs if (r["cited"][0] + r["cited"][1]) / 2 <= mid]
        upper = [r for r in refs if (r["cited"][0] + r["cited"][1]) / 2 > mid]
        neighbours = []
        if lower:
            neighbours.append(max(lower, key=lambda r: (r["cited"][0] + r["cited"][1]) / 2))
        if upper:
            neighbours.append(min(upper, key=lambda r: (r["cited"][0] + r["cited"][1]) / 2))
        low = min(interval(n)[0] for n in neighbours)
        high = max(interval(n)[1] for n in neighbours)
        agree = len(neighbours) == 2 and interval(neighbours[0]) == interval(neighbours[1])
        fits = []
        for o in item["occ"]:
            shift_low, shift_high = o[0] - cited[1], o[0] - cited[0]
            if shift_high >= low and shift_low <= high:
                fits.append(o)
        if not fits:
            continue
        result = results[item["index"]]
        repair = result.get("repair") or {}
        if repair.get("exact") or repair.get("capped"):
            continue
        centre = (low + high) / 2
        best = sorted(fits, key=lambda o: (abs((o[0] - mid) - centre), o[0]))[0]
        chosen_now = tuple(repair["suggested"]["lineRange"]) if repair else None
        confidence = 0.9 if (agree and len(fits) == 1) else 0.75 if len(fits) == 1 else 0.7 if agree else 0.6   # one-sided: never confident
        if chosen_now == best:
            if confidence > repair["confidence"] and not (item["entry"] and item["entry"].get("occurrences") not in (None, len(item["occ"]))):
                repair["confidence"], repair["needsReview"] = confidence, confidence < 0.8
                repair["why"] += "; agrees with the shift of the neighbouring evidence"
                repair["method"] = "consensus"
            continue
        replacement = _suggest(item["src"], item["row"], item["occ"], cited, None, replace(run.opts, use_fingerprint=False))
        replacement["suggested"]["lineRange"] = [best[0], best[1]]
        replacement["anchorLines"] = [best[0]]
        replacement["delta"] = best[0] - (item["entry"]["lineRange"][0] if item["entry"] else mid)
        replacement["lineText"] = echo_source_line(item["row"]["filePath"], item["src"], best[0])
        replacement["alsoAt"] = [o[0] for o in _nearest([o for o in item["occ"] if o != best], mid)[:3]]
        replacement["method"] = "consensus"
        replacement["confidence"] = confidence
        replacement["needsReview"] = confidence < 0.8
        replacement["why"] = (f"occurrence consistent with the shift of the neighbouring evidence "
                              f"(nearest-only would pick L{chosen_now[0] if chosen_now else '?'})")
        replacement["stamp"] = _entry_for(item["src"], item["row"], best, len(item["occ"]), run.opts)
        if item["entry"] and item["entry"].get("occurrences") not in (None, len(item["occ"])):
            replacement["confidence"] = min(replacement["confidence"], 0.4)
            replacement["needsReview"] = True
        result["repair"] = replacement
        stamped = _span_hint(*cited)
        result["hint"] = _drift_hint(replacement, stamped, len(item["occ"]))


def verify_evidence(rows: Iterable[Mapping], roots: Mapping[str, Path | str], *, stamp: Mapping | None = None,
                    project_dir: Path | str | None = None, mode: str = "asis", options: VerifyOptions | None = None) -> dict:
    """Verify evidence rows against the roots. Every row gets a result; nothing is applied or written.

    ``rows``     expanded evidence records (``evidenceId, repository, filePath, anchor, topic[, lineRange]``).
    ``roots``    ``{alias: directory}`` (``roots.roots_from_env``); a row whose alias is missing is SKIP, never PASS.
    ``stamp``    the loaded stamp (``load_stamp``); gives the stamped place of each row for drift handling.
    ``project_dir``  directory holding ``atlas/user/<name>.txt`` for EV-012.

    Returns ``{"summary", "results", "stamp"}``. ``stamp`` is the PROPOSED new stamp (see ``update_stamp``).
    """
    opts = options or VerifyOptions()
    rows = list(rows)
    old = stamp if stamp is not None else empty_stamp()
    run = _Run(roots, opts, old, project_dir, mode)
    results = [_verify_row(row, run, index) for index, row in enumerate(rows)]
    drift_items = iter(run.drift)
    for position, result in enumerate(results):
        if result["name"] == "ANCHOR_DRIFT":
            next(drift_items)["index"] = position
    if opts.use_consensus:
        _consensus(results, run)
    revisions: dict[str, str] = {}
    if opts.compute_root_rev:
        for alias, files in run.cited_files.items():
            revisions[alias] = root_revision(run.roots[alias], files)
    notes: list[dict] = []
    stale: dict[str, set] = {}
    for result, row in zip(results, rows):
        entry = result.get("stamp")
        if entry is not None and entry.get("rootRev") is None:
            entry["rootRev"] = revisions.get(entry["repository"])
        repair = result.get("repair")
        if repair and isinstance(repair.get("stamp"), dict) and repair["stamp"].get("rootRev") is None:
            repair["stamp"]["rootRev"] = revisions.get(repair["stamp"]["repository"])
        previous = (old.get("entries") or {}).get(result.get("evidenceId"))
        if previous and entry and previous.get("rootRev") not in (None, USER_ROOT_REV) and entry.get("rootRev") not in (None, previous.get("rootRev")):
            stale.setdefault(entry["repository"], set()).add(previous["rootRev"])
    for alias in sorted(stale):
        notes.append(_diag("ROOT_REV_CHANGED", f"root {alias!r} is at {revisions[alias]}; stamped at {', '.join(sorted(stale[alias]))}",
                           repository=alias, current=revisions[alias], stamped=sorted(stale[alias])))
    by_status = {name: 0 for name in STATUSES}
    warning_counts: dict[str, int] = {}
    for result in results:
        by_status[result["status"]] += 1
        for warning in result.get("warnings", ()):
            warning_counts[warning["name"]] = warning_counts.get(warning["name"], 0) + 1
    summary = {"total": len(results), "status": by_status, "warnings": dict(sorted(warning_counts.items())),
               "filesRead": run.files_read, "rootRevs": dict(sorted(revisions.items())), "notes": notes}
    return {"summary": summary, "results": results, "stamp": _proposed_stamp(old, results)}


def _proposed_stamp(old: Mapping, results: Sequence[Mapping]) -> dict:
    """What ``update_stamp`` would write for rows that pass on their own (a view, never saved here)."""
    return update_stamp(old, {"results": list(results)})


def update_stamp(stamp: Mapping, report: Mapping, *, accept_changes: bool = False, accept_repairs: Iterable[str] = (),
                 prune: bool = False) -> dict:
    """A new stamp document; the input is not modified.

    * rows that PASS get their entry written when they have none, or when the stamped block is unchanged
      (a unique anchor that moved is re-resolved, which is not a repair);
    * a row whose stamped block changed (EV-005: PASS with RANGE_CONTENT_CHANGED for a unique anchor, WARN for an
      anchor that occurs several times) is written only with ``accept_changes=True``, after the caller re-read the code;
    * a repair SUGGESTION is written only for the evidence ids listed in ``accept_repairs`` (explicit, per id);
    * ``prune=True`` drops entries whose id is not in the report.
    """
    new = copy.deepcopy(dict(stamp)) if stamp else empty_stamp()
    new.setdefault("schema", STAMP_SCHEMA)
    entries = new.setdefault("entries", {})
    accepted = set(accept_repairs)
    seen = set()
    for result in report.get("results", ()):
        evidence_id = result.get("evidenceId")
        seen.add(evidence_id)
        if ((result.get("status") == "PASS" or (result.get("status") == "WARN" and result.get("name") == "RANGE_CONTENT_CHANGED"))
                and result.get("stamp")):
            if result.get("stampable", True) or accept_changes:
                entries[evidence_id] = copy.deepcopy(result["stamp"])
        elif evidence_id in accepted:
            repair = result.get("repair") or {}
            if repair.get("stamp"):
                entries[evidence_id] = copy.deepcopy(repair["stamp"])
            else:
                raise EvidenceError("GEMARC_ATLAS_EV_072", f"{evidence_id} has no repair suggestion to accept")
    missing = accepted - seen
    if missing:
        raise EvidenceError("GEMARC_ATLAS_EV_072", f"accept_repairs names ids that are not in the report: {sorted(missing)}")
    if prune:
        for evidence_id in [k for k in entries if k not in seen]:
            del entries[evidence_id]
    new["entries"] = dict(sorted(entries.items()))
    return new


def verify_model(model_: Mapping, roots: Mapping[str, Path | str], **kwargs) -> dict:
    """``verify_evidence`` over ``model["evidences"]`` with the mode taken from the model's meta."""
    mode = (model_.get("meta") or {}).get("mode", "asis")
    kwargs.setdefault("mode", mode)
    return verify_evidence(model_.get("evidences", ()), roots, **kwargs)


# --------------------------------------------------------------------------------------------------------
# Reports for the AI
# --------------------------------------------------------------------------------------------------------


def compact_report(report: Mapping, *, max_bytes: int = 4096, include_warnings: bool = False) -> str:
    """One line per non-PASS row (all-green: a single line), UTF-8 size capped with a trailer naming what was cut.

    Everything echoed here already went through ``echo_line``; anchors of rows that failed the credential lint are
    never printed.
    """
    summary = report["summary"]
    counts = summary["status"]
    head = f"EVIDENCE {summary['total']} rows: PASS {counts['PASS']}, WARN {counts['WARN']}, FAIL {counts['FAIL']}, SKIP {counts['SKIP']}"
    lines = [head]
    for note in summary.get("notes", ()):
        lines.append(f"NOTE {note['rule']} {note['message']}")
    pinned = reviewed = 0
    for result in report["results"]:
        flags = [w["name"] for w in result.get("warnings", ())]
        if result["status"] == "PASS" and "ANCHOR_PINNED_AMBIGUOUS" in flags:
            pinned += 1
        # a PASS that rests on a changed stamped block must never look all-green
        review = result["status"] == "PASS" and "RANGE_CONTENT_CHANGED" in flags
        if result["status"] == "PASS" and not review and not (include_warnings and result.get("warnings")):
            continue
        if review and not include_warnings:
            reviewed += 1
            if reviewed > MAX_REVIEW_LINES:
                continue
        parts = [f"{result['status']} {result['evidenceId']} {result['rule']} {'RANGE_CONTENT_CHANGED (review)' if review else result['name']}"]
        detail = result.get("hint") or result.get("message") or ""
        if detail:
            parts.append(detail)
        if include_warnings and result.get("warnings"):
            parts.append("warn: " + ",".join(w["name"] for w in result["warnings"]))
        lines.append(" | ".join(parts))
    if reviewed > MAX_REVIEW_LINES:
        lines.append(f"NOTE EV-005 {reviewed - MAX_REVIEW_LINES} more PASS rows with RANGE_CONTENT_CHANGED (the cited section changed; "
                     "re-read, then update_stamp(accept_changes=True))")
    if pinned:
        lines.append(f"NOTE EV-003 {pinned} PASS rows rest on a stamp that pins one of several identical anchors "
                     "(ANCHOR_PINNED_AMBIGUOUS); give them longer anchors that occur once")
    out, used = [], 0
    for index, line in enumerate(lines):
        size = len(line.encode("utf-8")) + 1
        if used + size > max_bytes - 64 and index < len(lines) - 1:
            out.append(f"... {len(lines) - index} more lines cut at {max_bytes} bytes")
            break
        out.append(line)
        used += size
    return "\n".join(out)

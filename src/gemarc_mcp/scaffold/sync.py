"""
Generate the browser runtime's vocabulary module from the canonical JSON.

The vocabulary lives in ``vocab/*.json`` because it is the single source of
truth shared by the prompt, the Python validator and the browser runtime. A
browser cannot import JSON without a bundler, so the runtime consumes a
generated JavaScript module instead.

The generated file is committed on purpose: it lets the runtime be imported
directly by Node tests without a build step. ``tests/test_scaffold_vocab.py``
asserts the committed file still matches the JSON sources, so the two can
never drift apart silently.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCAFFOLD_ROOT = Path(__file__).resolve().parent
VOCAB_DIR = SCAFFOLD_ROOT / "vocab"
RUNTIME_DIR = SCAFFOLD_ROOT / "runtime"
GENERATED_PATH = RUNTIME_DIR / "core" / "vocab-data.js"

HEADER = """/**
 * GENERATED FILE - DO NOT EDIT.
 *
 * Source: src/gemarc_mcp/scaffold/vocab/*.json
 * Digest: {digest}
 * Regenerate: python -m gemarc_mcp.scaffold.sync
 *
 * The vocabulary is the single source of truth shared by the prompt, the
 * Python validator and this runtime. A drift test asserts that this file
 * still matches the JSON sources.
 */
export const VOCAB_DIGEST = '{digest}';

export const VOCAB = Object.freeze({payload});
"""


def load_vocab() -> dict:
    """Return every vocabulary file keyed by its file stem."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(VOCAB_DIR.glob("*.json"))
    }


def vocab_digest() -> str:
    """Stable digest of the vocabulary sources."""
    parts = []
    for path in sorted(VOCAB_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        parts.append(f"{path.stem}={json.dumps(payload, ensure_ascii=False, sort_keys=True)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def build_module() -> str:
    digest = vocab_digest()
    payload = json.dumps(load_vocab(), ensure_ascii=False, indent=2, sort_keys=True)
    return HEADER.format(digest=digest, payload=payload)


def write_module(target: Path | None = None) -> Path:
    path = Path(target) if target else GENERATED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_module(), encoding="utf-8")
    return path


def is_in_sync() -> bool:
    """True when the committed module matches the current vocabulary sources."""
    if not GENERATED_PATH.is_file():
        return False
    return GENERATED_PATH.read_text(encoding="utf-8") == build_module()


if __name__ == "__main__":
    written = write_module()
    print(f"wrote {written} (digest {vocab_digest()})")

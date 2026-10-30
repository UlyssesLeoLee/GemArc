"""ST-009 (red draft present) and SZ-001 (slice larger than 8 KiB).

ST-001..ST-008 and DV-001 are produced by the front-ends and the expander (``format/``, ``expand.py``) with the constructors
of ``diag.py``; their registry entries are in ``registry.py``. The two rules here need information only the slice check has:
the names of the red drafts (the slice store is not part of the core) and the byte size of each slice. Standard library only.
"""
from __future__ import annotations

from .context import Context
from .registry import check

#: A slice larger than this prints SZ-001 (WARN); the hard limit of 64 KiB is ST-008.
SLICE_WARN_BYTES = 8 * 1024


@check("ST", rules=("ST-009",))
def red_drafts(ctx: Context) -> None:
    for name in ctx.red_drafts:
        ctx.add("ST-009", path="slices", at=f"{name}:0", got=name, expected="no red draft", slice=name)


@check("SZ", rules=("SZ-001",))
def slice_size(ctx: Context) -> None:
    for name, size in ctx.slices:
        if size > SLICE_WARN_BYTES:
            ctx.add("SZ-001", path="file", at=f"{name}:0", got=f"{size} bytes", expected=f"at most {SLICE_WARN_BYTES} bytes", size=size)

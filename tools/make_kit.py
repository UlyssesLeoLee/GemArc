#!/usr/bin/env python3
"""Build the atlas kit (maintainer tool, not shipped): ``dist-kit/atlas-kit.zip``.

    python tools/make_kit.py                      # writes dist-kit/atlas-kit.zip next to this repository's root
    python tools/make_kit.py --out some/dir/kit.zip
    python tools/make_kit.py --list               # print the archive's file names and sizes, write nothing
    python tools/make_kit.py --check              # build twice and compare (the archive is deterministic), write nothing

The archive is made by ``gemarc_mcp.atlas.kit.build_zip`` (the same function the local ``/kit/atlas.zip`` route calls): the stdlib-only atlas
package, the two v1 modules it imports, a launcher ``atlas.py``, a README (Chinese, with English and Japanese lines) and the specifications.
The output directory ``dist-kit/`` is not committed (it is in ``.gitignore``). Standard library only; no network.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from gemarc_mcp.atlas import kit  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(REPO / "dist-kit" / kit.KIT_FILE), help="where to write the zip (default: dist-kit/atlas-kit.zip)")
    parser.add_argument("--list", action="store_true", help="list the archive's files, write nothing")
    parser.add_argument("--check", action="store_true", help="build twice and compare, write nothing")
    args = parser.parse_args(argv)
    try:
        if args.list:
            for name, data in kit.entries():
                print(f"{len(data):>9}  {name}")
            return 0
        data = kit.build_zip()
        if args.check:
            same = kit.build_zip() == data
            print(f"{'deterministic' if same else 'NOT deterministic'}: {len(data)} bytes, sha256 {kit.sha256_hex(data)}")
            return 0 if same else 1
    except kit.KitError as ex:
        print(f"{ex.code}: {ex.message}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{out}: {len(data)} bytes, sha256 {kit.sha256_hex(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

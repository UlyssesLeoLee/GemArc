"""Single source of truth for the plugin version, and a checker that keeps it honest.

The version used to live in five hand-maintained places (root plugin.json, Claude
plugin.json, viewer package.json, SKILL.md frontmatter, the MiniMax packager). Any one
of them drifting produces a plugin whose manifest lies about its own contents -- exactly
the class of quiet inconsistency this Skill exists to prevent.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # skills/ipa-architecture-3d
SKILLS = ROOT.parent                                   # skills/
REPO = SKILLS.parent                                   # repository root holding plugin.json


def version() -> str:
    """Read the one authoritative file."""
    return json.loads((REPO / 'plugin.json').read_text(encoding='utf8'))['version']


def sync(check_only: bool = False) -> list:
    """Propagate `version` to every place that repeats it. Returns a list of problems."""
    want = version()
    problems = []

    # (path, regex capturing the version value, label)
    targets = [
        (REPO / '.claude-plugin' / 'plugin.json', r'"version":\s*"([^"]+)"', 'Claude plugin.json'),
        (ROOT / 'assets' / 'viewer' / 'package.json', r'"version":\s*"([^"]+)"', 'viewer package.json'),
        (ROOT / 'SKILL.md', r'version:\s*"([^"]+)"', 'SKILL.md frontmatter'),
    ]
    for path, pattern, label in targets:
        if not path.exists():
            problems.append(f'{label}: 文件缺失 {path}')
            continue
        text = path.read_text(encoding='utf8')
        match = re.search(pattern, text)
        if not match:
            problems.append(f'{label}: 找不到 version 字段')
            continue
        if match.group(1) == want:
            continue
        # Compare the PARSED value, not the raw match, so spacing never reports a
        # false drift -- a checker that cries wolf gets ignored, then trusted wrongly.
        if check_only:
            problems.append(f'{label}: {match.group(1)} != {want}')
        else:
            new = re.sub(pattern, '"version": "%s"' % want, text, count=1)
            path.write_text(new, encoding='utf8')
            print(f'synced {label} -> {want}')
    return problems


def main() -> int:
    check_only = '--check' in sys.argv
    problems = sync(check_only=check_only)
    if problems:
        for p in problems:
            print('VERSION ' + p, file=sys.stderr)
        return 1
    print('版本一致：%s' % version())
    return 0


if __name__ == '__main__':
    sys.exit(main())

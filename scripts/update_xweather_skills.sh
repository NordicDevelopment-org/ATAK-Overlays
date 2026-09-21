#!/usr/bin/env bash
# Refresh the vendored Xweather agent skills in .claude/skills/ from upstream.
#
#   scripts/update_xweather_skills.sh          # upstream default branch
#   scripts/update_xweather_skills.sh v0.15.0  # a tag, branch or commit
#
# Rewrites the six skill directories, LICENSE.xweather, and the provenance rows
# in .claude/skills/VENDOR.md. Everything else in .claude/ is left alone.
#
# Review the diff before committing. Upstream ships instructions the agent
# follows, so a refresh changes behaviour, not just prose.
set -euo pipefail

UPSTREAM='https://github.com/vaisala-xweather/xweather-agent-skills'
REF="${1:-}"

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
dest="$repo_root/.claude/skills"
vendor_md="$dest/VENDOR.md"

[ -d "$dest" ] || { echo "error: $dest does not exist" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "Cloning $UPSTREAM${REF:+ @ $REF} ..."
if [ -n "$REF" ]; then
    git clone --quiet "$UPSTREAM" "$tmp/src"
    git -C "$tmp/src" checkout --quiet "$REF"
else
    git clone --quiet --depth 1 "$UPSTREAM" "$tmp/src"
fi

src="$tmp/src/plugins/xweather/skills"
[ -d "$src" ] || { echo "error: upstream layout changed, no $src" >&2; exit 1; }

# Refuse an empty or skill-less checkout rather than wiping what is here.
count=$(find "$src" -mindepth 2 -maxdepth 2 -name SKILL.md | wc -l | tr -d ' ')
[ "$count" -ge 1 ] || { echo "error: no SKILL.md found upstream" >&2; exit 1; }
echo "Found $count skills upstream."

# Clear only the skill directories; VENDOR.md and LICENSE.xweather survive.
find "$dest" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
cp -R "$src"/. "$dest"/
cp "$tmp/src/LICENSE" "$dest/LICENSE.xweather"

sha=$(git -C "$tmp/src" rev-parse HEAD)
committed=$(git -C "$tmp/src" log -1 --format=%ad --date=short)
version=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$tmp/src/plugins/xweather/.claude-plugin/plugin.json" | head -1)
today=$(date -u +%Y-%m-%d)

# Rewrite the three provenance rows in place. Anything else in VENDOR.md is
# prose a human wrote and is not touched.
python3 - "$vendor_md" "$sha" "$committed" "${version:-unknown}" "$today" <<'PY'
import re, sys
path, sha, committed, version, today = sys.argv[1:6]
text = open(path, encoding="utf-8").read()
rows = {
    r"^\| Commit \| .*$":     f"| Commit | `{sha}` ({committed}) |",
    r"^\| Plugin version \| .*$": f"| Plugin version | {version} |",
    r"^\| Retrieved \| .*$":  f"| Retrieved | {today} |",
}
for pattern, replacement in rows.items():
    text, n = re.subn(pattern, replacement.replace("\\", "\\\\"), text,
                      count=1, flags=re.MULTILINE)
    if n != 1:
        sys.exit(f"error: no row matching {pattern!r} in {path}")
open(path, "w", encoding="utf-8").write(text)
PY

echo
echo "Updated to $sha ($committed), plugin version ${version:-unknown}."
echo "Review with: git -C '$repo_root' diff --stat .claude/skills"

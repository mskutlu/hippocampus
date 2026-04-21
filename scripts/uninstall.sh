#!/usr/bin/env bash
# uninstall.sh — clean removal of Hippocampus.
#
# Removes:
#   - launchd agents (decay / inject / archive)
#   - MCP server registrations from every AI client
#   - Hippocampus marker blocks from every client's rules file
#
# Does NOT remove:
#   - The SQLite DB, fragments, or vault mirror (your data stays put)
#   - The Python package (run `uv pip uninstall hippocampus` if you also want that gone)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

echo "==> Unloading launchd agents..."
for name in daemon inject archive; do
    label="com.hippocampus.${name}"
    launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
    rm -f "$LAUNCH_AGENTS_DIR/${label}.plist"
done

echo "==> Unregistering from AI clients..."
cd "$REPO_ROOT"
HIPPO_BIN="$(uv run --quiet which hippo 2>/dev/null || true)"
if [[ -n "$HIPPO_BIN" ]]; then
    "$HIPPO_BIN" unregister || true
    echo "==> Removing marker blocks..."
    "$HIPPO_BIN" strip-blocks || true
fi

echo ""
echo "==> Done. Your data at \$HIPPOCAMPUS_HOME and \$HIPPOCAMPUS_VAULT/Fragments is untouched."

#!/usr/bin/env bash
# install.sh — One-shot installer for Hippocampus on macOS.
#
# Steps:
#   1. Install the Python package (uv pip install -e .)
#   2. Create ~/.hippocampus/ and initialise the SQLite DB
#   3. Render launchd plist templates with real paths, drop into ~/Library/LaunchAgents/
#   4. Load the launchd agents (decay/inject/archive)
#   5. Register the MCP server in every AI client's config
#   6. Write the first injection block
#   7. Run `hippo doctor`
#
# Re-running is safe; everything is idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HIPPO_HOME="${HIPPOCAMPUS_HOME:-$HOME/.hippocampus}"
HIPPO_VAULT="${HIPPOCAMPUS_VAULT:-$HOME/hippocampus-vault}"
LOG_DIR="$HIPPO_HOME/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

echo "==> Hippocampus installer"
echo "    repo:   $REPO_ROOT"
echo "    home:   $HIPPO_HOME"
echo "    vault:  $HIPPO_VAULT"
echo ""

# 1. Install package
echo "==> [1/7] Installing Python package..."
cd "$REPO_ROOT"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found. Install from https://github.com/astral-sh/uv" >&2
    exit 1
fi
uv sync --quiet
uv pip install -e . --quiet

# Find the hippo binary inside the uv venv
HIPPO_BIN="$(uv run --quiet which hippo 2>/dev/null || true)"
if [[ -z "$HIPPO_BIN" ]]; then
    echo "ERROR: hippo executable not found after install" >&2
    exit 1
fi
echo "    hippo: $HIPPO_BIN"

# 2. Initialise runtime + DB
echo "==> [2/7] Initialising runtime..."
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" init >/dev/null

# 3. Render launchd plists
echo "==> [3/7] Installing launchd agents..."
mkdir -p "$LAUNCH_AGENTS_DIR"
for name in daemon inject archive; do
    src="$REPO_ROOT/scripts/com.hippocampus.${name}.plist.template"
    dst="$LAUNCH_AGENTS_DIR/com.hippocampus.${name}.plist"
    sed \
        -e "s|__HIPPO_BIN__|$HIPPO_BIN|g" \
        -e "s|__HIPPO_HOME__|$HIPPO_HOME|g" \
        -e "s|__HIPPO_VAULT__|$HIPPO_VAULT|g" \
        -e "s|__HIPPO_LOG_DIR__|$LOG_DIR|g" \
        "$src" > "$dst"
    echo "    wrote  $dst"
done

# 4. Load launchd agents (bootout first to apply any changes)
echo "==> [4/7] Loading launchd agents..."
UID_NUM="$(id -u)"
for name in daemon inject archive; do
    label="com.hippocampus.${name}"
    plist="$LAUNCH_AGENTS_DIR/${label}.plist"
    launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
    launchctl bootstrap "gui/${UID_NUM}" "$plist"
    echo "    loaded $label"
done

# 5. Register MCP server in all clients
echo "==> [5/7] Registering MCP server in all AI clients..."
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" register || true

# 6. First injection
echo "==> [6/7] Writing first injection block..."
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" inject --commit || true

# 7. Doctor
echo "==> [7/7] Running doctor..."
echo ""
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" doctor || true

echo ""
echo "==> Done. Try: hippo remember -c \"...\"  then  hippo recall \"...\""

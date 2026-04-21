#!/usr/bin/env bash
# install.sh — Cross-platform installer for Hippocampus.
#
# Supported: macOS (full), Linux (incl. WSL — manual cron hints instead of systemd).
# Native Windows (non-WSL): use the PowerShell installer at scripts/install.ps1
# (not shipped yet) or run the Python pieces manually; see README.
#
# Re-running is safe; everything is idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HIPPO_HOME="${HIPPOCAMPUS_HOME:-$HOME/.hippocampus}"
HIPPO_VAULT="${HIPPOCAMPUS_VAULT:-$HOME/hippocampus-vault}"
LOG_DIR="$HIPPO_HOME/logs"

# Detect platform
OS_NAME="$(uname -s)"
case "$OS_NAME" in
    Darwin)  PLATFORM="macos"  ;;
    Linux)   PLATFORM="linux"  ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="windows-bash" ;;
    *)       PLATFORM="unknown" ;;
esac

echo "==> Hippocampus installer"
echo "    platform: $PLATFORM ($OS_NAME)"
echo "    repo:     $REPO_ROOT"
echo "    home:     $HIPPO_HOME"
echo "    vault:    $HIPPO_VAULT"
echo ""

# ---------------------------------------------------------------------------
# 1. Install Python package (all platforms)
# ---------------------------------------------------------------------------
echo "==> [1/5] Installing Python package..."
cd "$REPO_ROOT"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found. Install from https://github.com/astral-sh/uv" >&2
    exit 1
fi
uv sync --quiet
uv pip install -e . --quiet

HIPPO_BIN="$(uv run --quiet which hippo 2>/dev/null || true)"
if [[ -z "$HIPPO_BIN" ]]; then
    echo "ERROR: hippo executable not found after install" >&2
    exit 1
fi
echo "    hippo: $HIPPO_BIN"

# ---------------------------------------------------------------------------
# 2. Initialise runtime + DB (all platforms)
# ---------------------------------------------------------------------------
echo "==> [2/5] Initialising runtime..."
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" init >/dev/null

# ---------------------------------------------------------------------------
# 3. Install periodic jobs (platform-specific)
# ---------------------------------------------------------------------------
echo "==> [3/5] Installing periodic jobs..."
case "$PLATFORM" in
    macos)
        LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
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
        UID_NUM="$(id -u)"
        for name in daemon inject archive; do
            label="com.hippocampus.${name}"
            plist="$LAUNCH_AGENTS_DIR/${label}.plist"
            launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
            launchctl bootstrap "gui/${UID_NUM}" "$plist"
            echo "    loaded $label"
        done
        ;;

    linux)
        echo "    Linux detected — launchd is not available."
        echo "    Hippocampus CLI/MCP/web UI work fully. To get automatic decay,"
        echo "    inject, and archive cycles, add these entries to your crontab"
        echo "    (run \`crontab -e\`):"
        echo ""
        echo "      # Hippocampus periodic jobs"
        cat <<EOF
      0 *    * * *  env HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" decay   >>"$LOG_DIR/cron-decay.log"   2>&1
      */10 * * * *  env HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" inject  >>"$LOG_DIR/cron-inject.log"  2>&1
      15 4   * * *  env HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" archive >>"$LOG_DIR/cron-archive.log" 2>&1
EOF
        echo ""
        echo "    Or, for systemd users, see scripts/systemd/ (not shipped yet —"
        echo "    contribute a PR!). For now, cron is the easiest path."
        ;;

    windows-bash)
        echo "    Windows Git-Bash / MSYS detected — no daemon auto-install."
        echo "    The hippo CLI and MCP server work; register a scheduled task"
        echo "    for 'hippo decay' (hourly), 'hippo inject' (10 min),"
        echo "    and 'hippo archive' (daily) via Task Scheduler. See README."
        ;;

    *)
        echo "    Platform $OS_NAME not recognised — skipping daemon install."
        echo "    You can still run hippo manually: hippo decay / inject / archive."
        ;;
esac

# ---------------------------------------------------------------------------
# 4. Register MCP server in all clients (all platforms)
# ---------------------------------------------------------------------------
echo "==> [4/5] Registering MCP server in all AI clients..."
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" register || true

# First injection (all platforms)
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" inject --commit >/dev/null || true

# ---------------------------------------------------------------------------
# 5. Doctor
# ---------------------------------------------------------------------------
echo "==> [5/5] Running doctor..."
echo ""
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" doctor || true

echo ""
echo "==> Done. Try: hippo remember -c \"...\"  then  hippo recall \"...\""

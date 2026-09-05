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
SYNC_SERVER=0
SYNC_HOST="${HIPPOCAMPUS_SYNC_HOST:-0.0.0.0}"
SYNC_PORT="${HIPPOCAMPUS_SYNC_PORT:-7879}"
for arg in "$@"; do
    case "$arg" in
        --sync-server) SYNC_SERVER=1 ;;
        --sync-host=*) SYNC_HOST="${arg#*=}" ;;
        --sync-port=*) SYNC_PORT="${arg#*=}" ;;
    esac
done
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
echo "==> [1/6] Installing Python package..."
cd "$REPO_ROOT"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found. Install from https://github.com/astral-sh/uv" >&2
    exit 1
fi
# --inexact: don't prune packages outside the lockfile (keeps user-installed
# extras like `.[semantic]` from being uninstalled on re-runs).
uv sync --inexact --quiet
uv pip install -e . --quiet

HIPPO_BIN="$(uv run --quiet which hippo 2>/dev/null || true)"
if [[ -z "$HIPPO_BIN" ]]; then
    echo "ERROR: hippo executable not found after install" >&2
    exit 1
fi
echo "    hippo: $HIPPO_BIN"

# ---------------------------------------------------------------------------
# 2. Expose the CLI on PATH (macOS / Linux)
#    The entry points live inside the repo's .venv; without a link the bare
#    `hippo` command silently disappears from new shells.
# ---------------------------------------------------------------------------
echo "==> [2/6] Linking hippo into ~/.local/bin..."
if [[ "$PLATFORM" == "macos" || "$PLATFORM" == "linux" ]]; then
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$BIN_DIR"
    HIPPO_MCP_BIN="$(dirname "$HIPPO_BIN")/hippocampus-mcp"
    ln -sf "$HIPPO_BIN" "$BIN_DIR/hippo"
    ln -sf "$HIPPO_MCP_BIN" "$BIN_DIR/hippocampus-mcp"
    echo "    $BIN_DIR/hippo -> $HIPPO_BIN"
    echo "    $BIN_DIR/hippocampus-mcp -> $HIPPO_MCP_BIN"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            echo "    NOTE: $BIN_DIR is not on your PATH. Add to your shell rc:"
            echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
            ;;
    esac
else
    echo "    Skipped on $PLATFORM — invoke via 'uv run hippo' or add"
    echo "    $(dirname "$HIPPO_BIN") to PATH manually."
fi

# ---------------------------------------------------------------------------
# 3. Initialise runtime + DB (all platforms)
# ---------------------------------------------------------------------------
echo "==> [3/6] Initialising runtime..."
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" init >/dev/null

# ---------------------------------------------------------------------------
# 4. Install periodic jobs (platform-specific)
# ---------------------------------------------------------------------------
echo "==> [4/6] Installing periodic jobs..."
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
      15 4   * * *  env HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" maintain >>"$LOG_DIR/cron-archive.log" 2>&1
EOF
        echo ""
        echo "    Or, for systemd users, see scripts/systemd/ (not shipped yet —"
        echo "    contribute a PR!). For now, cron is the easiest path."
        ;;

    windows-bash)
        echo "    Windows Git-Bash / MSYS detected — no daemon auto-install."
        echo "    The hippo CLI and MCP server work; register a scheduled task"
        echo "    for 'hippo decay' (hourly), 'hippo inject' (10 min), 'hippo maintain' (daily),"
        echo "    and 'hippo archive' (daily) via Task Scheduler. See README."
        ;;

    *)
        echo "    Platform $OS_NAME not recognised — skipping daemon install."
        echo "    You can still run hippo manually: hippo decay / inject / archive."
        ;;
esac

# ---------------------------------------------------------------------------
# 5. Register MCP server in all clients (all platforms)
#    For Devin / Claude Code / Cursor / OpenCode / Windsurf / Antigravity /
#    VS Code Copilot / ZCode this drops an entry into the client's MCP config
#    JSON. For Codex this updates ~/.codex/config.toml. For Pi
#    (which has no native MCP) this installs the bundled TypeScript
#    extension at ~/.pi/agent/extensions/hippocampus/.
# ---------------------------------------------------------------------------
echo "==> [5/6] Registering MCP server in all AI clients..."
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" register || true

# First injection (all platforms)
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" inject --commit >/dev/null || true

# ---------------------------------------------------------------------------
# 5b. Optional: sync oplog server on this machine (--sync-server)
# ---------------------------------------------------------------------------
if [[ "$SYNC_SERVER" == "1" ]]; then
    echo "==> [5b] Installing sync server (host=$SYNC_HOST port=$SYNC_PORT)..."
    uv pip install -e '.[web]' --quiet
    SYNC_TOKEN="$(HIPPOCAMPUS_HOME="$HIPPO_HOME" "$HIPPO_BIN" sync token --show 2>/dev/null || true)"
    if [[ -z "$SYNC_TOKEN" ]]; then
        SYNC_TOKEN="$(HIPPOCAMPUS_HOME="$HIPPO_HOME" "$HIPPO_BIN" sync token)"
    fi
    case "$PLATFORM" in
        macos)
            dst="$HOME/Library/LaunchAgents/com.hippocampus.sync-server.plist"
            sed \
                -e "s|__HIPPO_BIN__|$HIPPO_BIN|g" \
                -e "s|__HIPPO_HOME__|$HIPPO_HOME|g" \
                -e "s|__HIPPO_VAULT__|$HIPPO_VAULT|g" \
                -e "s|__HIPPO_LOG_DIR__|$LOG_DIR|g" \
                -e "s|__SYNC_HOST__|$SYNC_HOST|g" \
                -e "s|__SYNC_PORT__|$SYNC_PORT|g" \
                "$REPO_ROOT/scripts/com.hippocampus.sync-server.plist.template" > "$dst"
            launchctl bootout "gui/$(id -u)/com.hippocampus.sync-server" 2>/dev/null || true
            launchctl bootstrap "gui/$(id -u)" "$dst"
            echo "    loaded com.hippocampus.sync-server"
            ;;
        linux)
            mkdir -p "$HOME/.config/systemd/user"
            dst="$HOME/.config/systemd/user/hippocampus-sync.service"
            sed \
                -e "s|__HIPPO_BIN__|$HIPPO_BIN|g" \
                -e "s|__HIPPO_HOME__|$HIPPO_HOME|g" \
                -e "s|__HIPPO_VAULT__|$HIPPO_VAULT|g" \
                -e "s|__SYNC_HOST__|$SYNC_HOST|g" \
                -e "s|__SYNC_PORT__|$SYNC_PORT|g" \
                "$REPO_ROOT/scripts/hippocampus-sync.service.template" > "$dst"
            systemctl --user daemon-reload && systemctl --user enable --now hippocampus-sync.service \
                && echo "    enabled hippocampus-sync.service" \
                || echo "    systemd --user unavailable; run: $HIPPO_BIN sync serve --host $SYNC_HOST --port $SYNC_PORT"
            ;;
        *)
            echo "    No service manager on $PLATFORM; run: $HIPPO_BIN sync serve --host $SYNC_HOST --port $SYNC_PORT"
            ;;
    esac
    echo ""
    echo "    Sync server token (set on every device):"
    echo "      hippo config set sync_url http://<this-host>:$SYNC_PORT"
    echo "      hippo config set sync_token $SYNC_TOKEN"
    echo "      hippo config set sync_enabled true"
    echo "      hippo sync"
    echo ""
fi

# ---------------------------------------------------------------------------
# 6. Doctor
# ---------------------------------------------------------------------------
echo "==> [6/6] Running doctor..."
echo ""
HIPPOCAMPUS_HOME="$HIPPO_HOME" HIPPOCAMPUS_VAULT="$HIPPO_VAULT" "$HIPPO_BIN" doctor || true

echo ""
echo "==> Done. Try: hippo remember -c \"...\"  then  hippo recall \"...\""

#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

PREVIEW_URL="http://127.0.0.1:3000/"
if command -v curl >/dev/null 2>&1 \
    && curl --fail --silent --show-error --max-time 1 "$PREVIEW_URL" >/dev/null 2>&1; then
    printf 'Investigator UI is already running at %s\n' "$PREVIEW_URL"
    exit 0
fi

if command -v pnpm >/dev/null 2>&1; then
    run_pnpm() {
        pnpm "$@"
    }
elif command -v corepack >/dev/null 2>&1; then
    run_pnpm() {
        corepack pnpm "$@"
    }
else
    USER_RUNTIME_ROOT="${HOME:?}/.cache/codex-runtimes/codex-primary-runtime/dependencies"
    BUNDLED_NODE_DIR="$USER_RUNTIME_ROOT/node/bin"
    BUNDLED_TOOLS_DIR="$USER_RUNTIME_ROOT/bin/fallback"
    BUNDLED_PNPM="$BUNDLED_TOOLS_DIR/pnpm"
    if [ ! -x "$BUNDLED_PNPM" ] || [ ! -x "$BUNDLED_NODE_DIR/node" ]; then
        printf '%s\n' \
            'Node.js/pnpm was not found. Install Node.js 22, then run: corepack enable' >&2
        exit 1
    fi
    PATH="$BUNDLED_NODE_DIR:$BUNDLED_TOOLS_DIR:$PATH"
    export PATH
    run_pnpm() {
        "$BUNDLED_PNPM" "$@"
    }
fi

printf '%s\n' 'Preparing the pinned UI dependencies…'
run_pnpm install --frozen-lockfile
printf 'Starting the investigator UI at %s\n' "$PREVIEW_URL"
run_pnpm run dev:demo

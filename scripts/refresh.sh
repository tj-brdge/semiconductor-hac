#!/bin/bash
# One monitor refresh, for the scheduler (scripts/install_schedule.sh) or by hand.
# Credentials are read from ~/.themegraph.env if it exists (launchd does not load
# your shell profile). That file holds lines such as:
#   export FMP_API_KEY=...
#   export VOYAGE_API_KEY=...
#   export ANTHROPIC_API_KEY=...        # optional: written theme titles
#   export EDGAR_USER_AGENT="Name email"  # as you already use for EDGAR
# Keep it readable by you only: chmod 600 ~/.themegraph.env
set -u
cd "$(dirname "$0")/.." || exit 1
[ -f "$HOME/.themegraph.env" ] && . "$HOME/.themegraph.env"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p logs
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) refresh"
  if [ -x .venv/bin/themegraph ]; then
    .venv/bin/themegraph monitor refresh "$@"
  elif command -v uv >/dev/null 2>&1; then
    uv run themegraph monitor refresh "$@"
  else
    themegraph monitor refresh "$@"
  fi
  echo "=== exit $?"
} >> logs/refresh.log 2>&1

#!/bin/bash
# Install (or remove) a daily monitor refresh on macOS with launchd.
#   scripts/install_schedule.sh            # weekdays at 19:00 local time
#   scripts/install_schedule.sh 21 30      # weekdays at 21:30 local time
#   scripts/install_schedule.sh remove
# Pick a time after the US market close plus an hour or two, in your local time.
# The job runs scripts/refresh.sh, which logs to logs/refresh.log. If the Mac is
# asleep at that time, launchd runs the job when it next wakes.
set -eu
LABEL=com.themegraph.monitor
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" = "remove" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $PLIST"
  exit 0
fi
HOUR="${1:-19}"
MINUTE="${2:-0}"
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
{
  cat <<HEAD
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/scripts/refresh.sh</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd.err</string>
  <key>StartCalendarInterval</key><array>
HEAD
  for DAY in 1 2 3 4 5; do
    echo "    <dict><key>Weekday</key><integer>$DAY</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MINUTE</integer></dict>"
  done
  cat <<TAIL
  </array>
</dict></plist>
TAIL
} > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "installed: weekdays at $(printf '%02d:%02d' "$HOUR" "$MINUTE") local time"
echo "log: $REPO/logs/refresh.log   remove with: scripts/install_schedule.sh remove"

#!/bin/bash
# Starts the controller if it isn't answering, then opens the dashboard.
# The "Basement Vent" Dock app (deploy/make-launcher.sh) runs this, so it is safe to
# click any time: if the controller is already running it only opens the window.
#
#   bash deploy/open-dashboard.sh

set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${VENTCTL_PORT:-8000}"
URL="http://localhost:$PORT"
LABEL="com.ventctl"
DOMAIN="gui/$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

healthy() { /usr/bin/curl -fs --max-time 2 "$URL/api/health" >/dev/null; }

if ! healthy; then
  if [ ! -f "$PLIST" ]; then
    echo "The controller isn't installed yet. Run: bash $REPO/deploy/install-mac.sh" >&2
    exit 1
  fi
  if /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    /bin/launchctl kickstart -k "$DOMAIN/$LABEL" >/dev/null 2>&1
  else
    /bin/launchctl bootstrap "$DOMAIN" "$PLIST" >/dev/null 2>&1 \
      || /bin/launchctl kickstart "$DOMAIN/$LABEL" >/dev/null 2>&1
  fi
  for _ in $(seq 1 45); do
    healthy && break
    sleep 1
  done
  if ! healthy; then
    echo "The controller didn't start. See $REPO/data/ventctl.log" >&2
    exit 1
  fi
fi

# Brave opens it as its own app window; otherwise use the default browser.
if /usr/bin/open -Ra "Brave Browser" 2>/dev/null; then
  /usr/bin/open -na "Brave Browser" --args --app="$URL"
else
  /usr/bin/open "$URL"
fi

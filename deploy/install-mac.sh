#!/bin/bash
# One-time setup on a Mac. Run from anywhere:
#   bash deploy/install-mac.sh
#
# Installs what's missing (Homebrew Python and Node), builds the dashboard, creates the
# Python environment, and installs two LaunchAgents:
#   com.ventctl         runs the controller at login and restarts it if it stops
#   com.ventctl.update  checks GitHub every 30 minutes and installs merged updates
# Re-running it is safe.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${VENTCTL_PORT:-8000}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$REPO"

if ! command -v brew >/dev/null; then
  echo "Homebrew is missing. Install it first (paste this in Terminal), then run this script again:"
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi
command -v python3.12 >/dev/null || brew install python@3.12
command -v node >/dev/null || brew install node

echo "Building the dashboard…"
(cd frontend && npm ci --silent && npm run build --silent)

echo "Setting up Python…"
[ -x backend/.venv/bin/python ] || python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -q -r backend/requirements-dev.txt

mkdir -p "$REPO/data" "$HOME/Library/LaunchAgents"
chmod +x "$REPO/deploy/update.sh"
UID_NUM="$(id -u)"

cat > "$HOME/Library/LaunchAgents/com.ventctl.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ventctl</string>
  <key>ProgramArguments</key>
  <array><string>$REPO/backend/.venv/bin/python</string><string>-m</string><string>ventctl</string></array>
  <key>WorkingDirectory</key><string>$REPO/backend</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>VENTCTL_DB</key><string>$REPO/data/ventctl.db</string>
    <key>VENTCTL_PORT</key><string>$PORT</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO/data/ventctl.log</string>
  <key>StandardErrorPath</key><string>$REPO/data/ventctl.log</string>
</dict>
</plist>
EOF

cat > "$HOME/Library/LaunchAgents/com.ventctl.update.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ventctl.update</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$REPO/deploy/update.sh</string></array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$REPO/data/update.log</string>
  <key>StandardErrorPath</key><string>$REPO/data/update.log</string>
</dict>
</plist>
EOF

for label in com.ventctl com.ventctl.update; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$HOME/Library/LaunchAgents/$label.plist"
done

echo -n "Starting"
for _ in $(seq 1 60); do
  if curl -fs "http://localhost:$PORT/api/health" >/dev/null; then
    echo
    IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
    echo "Running: http://localhost:$PORT"
    [ -n "$IP" ] && echo "From your phone (same Wi-Fi): http://$IP:$PORT"
    echo "Updates from GitHub install automatically every 30 minutes (log: data/update.log)."
    open "http://localhost:$PORT" 2>/dev/null || true
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo
echo "The controller did not answer yet. Check $REPO/data/ventctl.log"
exit 1

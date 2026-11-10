#!/bin/bash
# Update the controller to the latest version on GitHub (branch main).
#
# Runs every 30 minutes from the com.ventctl.update LaunchAgent (see install-mac.sh),
# or by hand: deploy/update.sh
#
# Safe by design: it only installs what has been merged to main, runs the test suite
# first, and keeps the running version if anything fails. Log: data/update.log

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$REPO/data/update.log"
mkdir -p "$REPO/data"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$REPO" || exit 1

STATUS="$REPO/data/update-status.json"
LOCK="$REPO/data/update.lock"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
# status STATE MESSAGE: what the dashboard's "Update now" button shows
status() {
  local msg="${2//\\/\\\\}"; msg="${msg//\"/\\\"}"
  printf '{"state": "%s", "time": "%s", "message": "%s"}\n' "$1" "$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')" "$msg" > "$STATUS.tmp" && mv -f "$STATUS.tmp" "$STATUS"
}

# one update at a time (a lock older than 30 minutes is left over from a crash)
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

if [ ! -d .git ]; then
  log "not a git checkout; reinstall with deploy/install-mac.sh to get automatic updates"
  status failed "This copy wasn't installed from GitHub, so it can't update itself."
  exit 1
fi

if ! git fetch -q origin main 2>>"$LOG"; then
  log "could not reach GitHub; will try again later"
  status offline "Couldn't reach GitHub. Check the internet connection; it will try again later."
  exit 0
fi

OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse origin/main)"
if [ "$OLD" = "$NEW" ] && [ "${1:-}" != "--force" ]; then
  status up_to_date "Already up to date."
  exit 0
fi

log "updating ${OLD:0:7} -> ${NEW:0:7}"
# The frontend build rewrites this file. Older versions tracked it in git, so a local build
# left it "changed" and blocked every later update. Discard it; the build recreates it.
git checkout -q -- frontend/tsconfig.tsbuildinfo 2>/dev/null || true
status running "Installing the new version and running the tests (a minute or two)…"
if ! git merge -q --ff-only origin/main 2>>"$LOG"; then
  log "local changes block the update (git status shows them); nothing changed"
  status failed "Files changed on this Mac block the update. Nothing was changed."
  exit 1
fi

build() {
  (cd frontend && npm ci --silent && npm run build --silent) >>"$LOG" 2>&1 || return 1
  backend/.venv/bin/pip install -q -r backend/requirements-dev.txt >>"$LOG" 2>&1 || return 1
  (cd backend && .venv/bin/python -m pytest -q -x >>"$LOG" 2>&1) || return 2
  return 0
}

build
rc=$?
if [ $rc -ne 0 ]; then
  if [ $rc -eq 2 ]; then
    log "tests failed on ${NEW:0:7}; staying on ${OLD:0:7}"
    status failed "The new version failed its tests, so the working version was kept."
  else
    log "build failed on ${NEW:0:7}; staying on ${OLD:0:7}"
    status failed "The new version didn't build, so the working version was kept."
  fi
  git reset -q --hard "$OLD"
  (cd frontend && npm run build --silent) >>"$LOG" 2>&1
  backend/.venv/bin/pip install -q -r backend/requirements.txt >>"$LOG" 2>&1
  exit 1
fi

# restart the controller; on startup it reads every plug and turns running fans OFF before resuming
status updated "Updated to ${NEW:0:7}: $(git log -1 --format=%s)"
launchctl kickstart -k "gui/$(id -u)/com.ventctl" >>"$LOG" 2>&1 || log "could not restart com.ventctl (is it installed?)"
log "now running ${NEW:0:7}: $(git log -1 --format=%s)"

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

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

if [ ! -d .git ]; then
  log "not a git checkout; reinstall with deploy/install-mac.sh to get automatic updates"
  exit 1
fi

if ! git fetch -q origin main 2>>"$LOG"; then
  log "could not reach GitHub; will try again later"
  exit 0
fi

OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse origin/main)"
if [ "$OLD" = "$NEW" ] && [ "${1:-}" != "--force" ]; then
  exit 0  # already up to date
fi

log "updating ${OLD:0:7} -> ${NEW:0:7}"
if ! git merge -q --ff-only origin/main 2>>"$LOG"; then
  log "local changes block the update (git status shows them); nothing changed"
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
  [ $rc -eq 2 ] && log "tests failed on ${NEW:0:7}; staying on ${OLD:0:7}" || log "build failed on ${NEW:0:7}; staying on ${OLD:0:7}"
  git reset -q --hard "$OLD"
  (cd frontend && npm run build --silent) >>"$LOG" 2>&1
  backend/.venv/bin/pip install -q -r backend/requirements.txt >>"$LOG" 2>&1
  exit 1
fi

# restart the controller; on startup it reads every plug and turns running fans OFF before resuming
launchctl kickstart -k "gui/$(id -u)/com.ventctl" >>"$LOG" 2>&1 || log "could not restart com.ventctl (is it installed?)"
log "now running ${NEW:0:7}: $(git log -1 --format=%s)"

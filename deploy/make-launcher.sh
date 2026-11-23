#!/bin/bash
# Builds "Basement Vent.app" in ~/Applications and puts it in the Dock.
# Clicking it starts the controller if it isn't running and opens the dashboard
# (see deploy/open-dashboard.sh). Re-running it is safe; it keeps a custom icon
# you've set on an existing copy of the app.
#
#   bash deploy/make-launcher.sh

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${VENTCTL_APP_NAME:-Basement Vent}"
APP="$HOME/Applications/$NAME.app"
SCRIPT="$REPO/deploy/open-dashboard.sh"
chmod +x "$SCRIPT"
mkdir -p "$HOME/Applications"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# AppleScript string literal for the shell command (escape \ and ").
CMD="/bin/bash $(printf '%q' "$SCRIPT")"
CMD_AS="${CMD//\\/\\\\}"
CMD_AS="${CMD_AS//\"/\\\"}"
cat > "$TMP/main.applescript" <<EOF
try
	do shell script "$CMD_AS"
on error errMsg
	display alert "$NAME" message errMsg as warning
end try
EOF
/usr/bin/osacompile -o "$TMP/$NAME.app" "$TMP/main.applescript"

# Keep the icon from the copy being replaced.
if [ -d "$APP" ]; then
  for f in applet.icns Assets.car; do
    [ -f "$APP/Contents/Resources/$f" ] && cp "$APP/Contents/Resources/$f" "$TMP/$NAME.app/Contents/Resources/$f"
  done
fi
/usr/bin/codesign --force --deep -s - "$TMP/$NAME.app" >/dev/null 2>&1 || true

rm -rf "$APP"
mv "$TMP/$NAME.app" "$APP"
echo "Built $APP"

if defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "$(basename "$APP" | sed 's/ /%20/g')"; then
  echo "Already in the Dock."
else
  defaults write com.apple.dock persistent-apps -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>file://${APP// /%20}/</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
  killall Dock
  echo "Added to the Dock."
fi

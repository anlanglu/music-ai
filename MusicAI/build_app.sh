#!/bin/zsh
# Builds "Composer.app" next to the project root (/Users/Shared/music-ai).
set -e
cd "$(dirname "$0")"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/MusicAI"
APP="../Composer.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Composer"
cp Icon/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Composer</string>
<key>CFBundleDisplayName</key><string>Composer</string>
<key>CFBundleIdentifier</key><string>local.musicai.texttomusic</string>
<key>CFBundleExecutable</key><string>Composer</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSMicrophoneUsageDescription</key><string>Composer records your singing to make a new singing voice from it. The recording stays on this Mac.</string>
</dict></plist>
PLIST

# The Logic Pro plugin: an Audio Unit extension inside the app (built by hand, no Xcode needed).
AU="$APP/Contents/PlugIns/ComposerAU.appex"
mkdir -p "$AU/Contents/MacOS"
swiftc -O -parse-as-library -module-name ComposerAU -application-extension Plugin/ComposerAU.swift \
    -o "$AU/Contents/MacOS/ComposerAU" -Xlinker -e -Xlinker _NSExtensionMain \
    -framework AudioToolbox -framework AVFoundation -framework CoreAudioKit -framework SwiftUI
cp Plugin/Info.plist "$AU/Contents/Info.plist"
codesign --force --sign - --entitlements Plugin/ComposerAU.entitlements "$AU"
codesign --force --sign - "$APP"
pluginkit -a "$AU" 2>/dev/null || true
echo "Built: $(cd .. && pwd)/Composer.app"

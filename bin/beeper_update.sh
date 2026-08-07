#!/bin/bash

# Directory containing the AppImages
APPIMAGE_DIR="$HOME/.AppImage"

# Find the most recent beeper AppImage file
LATEST_BEEPER=$(ls -t "$APPIMAGE_DIR"/[bB]eeper-* 2>/dev/null | head -n1)

chmod +x "$LATEST_BEEPER"

if [ -z "$LATEST_BEEPER" ]; then
    echo "Error: No beeper AppImage found in $APPIMAGE_DIR"
    exit 1
fi

# Remove the old symlink if it exists
if [ -L "$APPIMAGE_DIR/beeper" ]; then
    rm "$APPIMAGE_DIR/beeper"
fi

# Create new symlink
ln -s "$LATEST_BEEPER" "$APPIMAGE_DIR/beeper"

echo "Updated beeper symlink to point to: $LATEST_BEEPER"

#!/bin/bash

# Directory containing the AppImages
APPIMAGE_DIR="$HOME/.AppImage"

# Find the most recent cursor AppImage file
LATEST_CURSOR=$(ls -t "$APPIMAGE_DIR"/[cC]ursor-* 2>/dev/null | head -n1)

if [ -z "$LATEST_CURSOR" ]; then
    echo "Error: No cursor AppImage found in $APPIMAGE_DIR"
    exit 1
fi

# Remove the old symlink if it exists
if [ -L "$APPIMAGE_DIR/cursor" ]; then
    rm "$APPIMAGE_DIR/cursor"
fi

# Create new symlink
ln -s "$LATEST_CURSOR" "$APPIMAGE_DIR/cursor"

echo "Updated cursor symlink to point to: $LATEST_CURSOR"

# ABOUTME: Diagnostic/patch tool for MesloLGS NF broken cmap entries at U+23F4-U+23F7.
# ABOUTME: See ~/Setup/docs/wezterm-emoji-fix.md for full context and the actual fix.
#
# Some Nerd Font builds add cmap entries for U+23F4-U+23F7 (media transport triangles)
# that map to .notdef. If wezterm ls-fonts --text "⏵" shows .notdef AND the font is
# listed as the source, this script can surgically remove those bad cmap entries.
#
# NOTE: The current MesloLGS NF build (as of 2026-02) does NOT have this issue.
# The real fix was installing Noto Sans Symbols2 as a fallback font — see the docs.
#
# Usage: uv run --with fonttools python3 patch-meslo-nf-emoji.py

import glob
import os
import sys
from fontTools.ttLib import TTFont

BROKEN_CODEPOINTS = [0x23F4, 0x23F5, 0x23F6, 0x23F7]
FONT_DIR = os.path.expanduser("~/.local/share/fonts")
PATTERN = os.path.join(FONT_DIR, "MesloLGS NF*.ttf")


def patch_font(path):
    font = TTFont(path)
    cmap = font["cmap"]
    removed = []
    for table in cmap.tables:
        for cp in BROKEN_CODEPOINTS:
            if cp in table.cmap:
                del table.cmap[cp]
                removed.append(f"U+{cp:04X}")
    if removed:
        font.save(path)
        print(f"  Patched: removed {', '.join(sorted(set(removed)))}")
    else:
        print(f"  Already clean, no changes needed")
    font.close()


def main():
    files = sorted(glob.glob(PATTERN))
    if not files:
        print(f"No font files found matching: {PATTERN}")
        sys.exit(1)

    print(f"Patching {len(files)} font file(s) in {FONT_DIR}:\n")
    for path in files:
        print(f"  {os.path.basename(path)}")
        patch_font(path)

    print(f"\nDone. Run 'fc-cache -fv' to refresh the font cache.")
    print("Then restart WezTerm (or Ctrl+Shift+R to reload config).")


if __name__ == "__main__":
    main()

# WezTerm: Missing Emoji/Symbol Glyphs with Nerd Fonts

## Problem

Claude Code uses U+23F5 (⏵ BLACK MEDIUM RIGHT-POINTING TRIANGLE) for the
"accept edits on" toggle. When using MesloLGS NF as the primary font in
WezTerm, this character renders as a placeholder box with a warning:

```
No fonts contain glyphs for these codepoints: \u{23f5}.
```

The same issue affects U+23F4 (⏴), U+23F6 (⏶), and U+23F7 (⏷).

## Root Cause

U+23F5 is in the Miscellaneous Technical Unicode block. It is **not** an
emoji (no `Emoji_Presentation` property), so emoji fonts like Noto Color
Emoji don't include it. MesloLGS NF (Nerd Font patched) also doesn't have
it. With no font in the fallback chain covering this codepoint, WezTerm
shows `.notdef`.

**Why other emojis work:** Characters like U+23F0 (alarm clock) and U+23F8
(pause) have `Emoji_Presentation` in Unicode and are included in Noto Color
Emoji. The media transport triangles (U+23F4-U+23F7) don't.

## Fix Applied

1. **Installed Noto Sans Symbols2** — covers Miscellaneous Technical block
   including U+23F4-U+23F7:

   ```bash
   curl -sL "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansSymbols2/NotoSansSymbols2-Regular.ttf" \
     -o ~/.local/share/fonts/NotoSansSymbols2-Regular.ttf
   fc-cache -f ~/.local/share/fonts/
   ```

2. **Added to WezTerm fallback chain** in `~/.config/wezterm/wezterm.lua`:

   ```lua
   config.font = wezterm.font_with_fallback({
     'MesloLGS NF',          -- Primary: Nerd Font with icons
     'Noto Sans Symbols2',   -- Fallback: technical symbols (U+23F5 etc.)
     'Noto Color Emoji',     -- Fallback: emoji
   })
   ```

3. **Verified** with `wezterm ls-fonts --text "⏵"` — resolves to
   `glyph=uni23F5` from Noto Sans Symbols2.

## Diagnostic Commands

```bash
# Check which font WezTerm selects for a character
wezterm ls-fonts --text "⏵"

# Check if any installed font has a specific codepoint
fc-list :charset=23F5 family

# Inspect a font's cmap for specific codepoints
uv run --with fonttools python3 -c "
from fontTools.ttLib import TTFont
font = TTFont('path/to/font.ttf')
for table in font['cmap'].tables:
    if 0x23F5 in table.cmap:
        print(table.cmap[0x23F5])
"
```

## Alternative: Patch the Font

If the issue is caused by a Nerd Font that *claims* to have the glyph
(cmap entry exists) but maps it to `.notdef`, the fallback font never
activates. In that case, use `scripts/patch-meslo-nf-emoji.py` to remove
the broken cmap entries:

```bash
uv run --with fonttools python3 ~/Setup/scripts/patch-meslo-nf-emoji.py
fc-cache -f ~/.local/share/fonts/
```

## Alternative: Switch to Unpatched Meslo

Instead of a Nerd Font-patched Meslo, use plain Meslo LG S with WezTerm's
built-in `Symbols Nerd Font Mono` for icons:

```lua
config.font = wezterm.font_with_fallback({
  'Meslo LG S',              -- Unpatched: no cmap collisions
  'Symbols Nerd Font Mono',  -- Nerd icons (bundled by WezTerm)
  'Noto Sans Symbols2',      -- Technical symbols
  'Noto Color Emoji',        -- Emoji
})
```

Requires installing the unpatched Meslo LG S font from
https://github.com/andreberg/Meslo-Font.

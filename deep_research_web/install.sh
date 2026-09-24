#!/usr/bin/env bash
# ABOUTME: Installs the deep-research-web engine for this user: profile dir, config, deps, user timer, PATH link.
# ABOUTME: Idempotent and never touches root paths; the restic exclude is one sudo line in the README.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

install -d -m 700 "$HOME/.config/deep-research-web" "$HOME/.local/state/deep-research-web"
if [ ! -f "$HOME/.config/deep-research-web/env" ]; then
  install -m 600 "$HERE/env.example" "$HOME/.config/deep-research-web/env"
  echo "NOTE: edit $HOME/.config/deep-research-web/env (project) before login"
fi

# Playwright wheel only; the browser is the system Google Chrome (channel=chrome), nothing else downloads.
(cd "$HERE" && uv sync -q)

install -d "$HOME/.local/bin"
ln -sfn "$HERE/bin/deep-research-web" "$HOME/.local/bin/deep-research-web"

install -d "$HOME/.config/systemd/user"
for u in deep-research-web-watch.service deep-research-web-watch.timer; do
  ln -sfn "$HERE/systemd/$u" "$HOME/.config/systemd/user/$u"
done
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
systemctl --user daemon-reload
systemctl --user enable --now deep-research-web-watch.timer
echo "installed. Next: deep-research-web login <name> (the claude-usage account name, in a normal tmux window: it prompts for the code), then deep-research-web switch <name>"

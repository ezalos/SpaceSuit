#!/bin/bash
# ABOUTME: Rebuilds the .aux (and .toc/.lof/.lot) of every fixture paper with tectonic; the tests read these real build
# ABOUTME: outputs and never run LaTeX themselves. Usage: tests/fixtures/build.sh (needs tectonic, network on first use)
set -euo pipefail
for d in "$(dirname "$0")"/*/; do
  (cd "$d" && tectonic -X compile --keep-intermediates main.tex >/dev/null && find . -maxdepth 1 -name 'main.*' \
    ! -name 'main.tex' ! -name 'main.aux' ! -name 'main.toc' ! -name 'main.lof' ! -name 'main.lot' -delete)
done

#!/usr/bin/env bash
# ---------------------------------------------------------------------------- #
#  heuritech_env.sh                                                             #
# ---------------------------------------------------------------------------- #
# Environment variables, PATH tweaks and aliases that are specific to          #
# any machine whose WHICH_COMPUTER ends with _Heuritech.                       #
# This file is sourced by ~/.zshrc.                                            #
# ---------------------------------------------------------------------------- #

# Guard clause – source nothing if not on a Heuritech machine
[[ $WHICH_COMPUTER =~ _Heuritech$ ]] || return 0

# ------------------------------ Generic Heuritech ---------------------------- #

# # Pyenv
# export PYENV_ROOT="$HOME/.pyenv"
# [[ -d $PYENV_ROOT/bin ]] && path_prepend "$PYENV_ROOT/bin"

# # Initialise pyenv only if available
# command -v pyenv >/dev/null 2>&1 && {
#   eval "$(pyenv init --path)"
#   eval "$(pyenv init -)"
#   eval "$(pyenv virtualenv-init -)"
# }

# # Add monorepo libraries to PYTHONPATH
# pythonpaths_monorepo_lib_src=$(find "$HOME/monorepo/libraries" -maxdepth 2 -name "src" -type d | tr '\n' ':' | sed 's/:$//')
# export PYTHONPATH="${PYTHONPATH}:${pythonpaths_monorepo_lib_src}"

# AWS & service endpoints
export AWS_PROFILE="euprod"
export AWS_REGION="eu-west-1"

export THESAURUS_ROUTE_BASE=https://core-api.heuritech.com/thesaurus
export OPSTER_MODULES_ROUTE_BASE=https://core-api.heuritech.com/opster/training_requests
export MODULES_ROUTE_BASE=https://core-api.heuritech.com/indus
export CATALOG_ROUTE_BASE=https://core-api.heuritech.com/catalogx
export INDUS_ROUTE_BASE=https://core-api.heuritech.com/indus
export DATASET_ROUTE_BASE=https://core-api.heuritech.com/dataset
export LABELING_ROUTE_BASE=https://core-api.heuritech.com/labeling

function krsync_ { command="krsync -ravh --progress --stats --exclude={'libraries/*/tests/*','*.csv','*.ndjson','.git*','*.tar.gz','**.venv/**','.osgrep/lancedb/**','env','**venv/**','.python-version','*.pyc','__pycache__','.pytest_cache','.ipynb_checkpoint','*dump.json','untracked_files/data/**','untracked_files/**.json*'} $HOME/$1/ $2:/data/$1";
  echo $command;
  eval $command;}



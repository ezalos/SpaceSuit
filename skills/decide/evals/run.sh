#!/usr/bin/env bash
# ABOUTME: Runs one decide-skill eval scenario end to end with headless claude.
# ABOUTME: Usage: run.sh <baseline|skill> <scenario-dir> <out-dir>; writes transcript + score.json.
set -euo pipefail

arm=$1 scen=$(realpath "$2") out=$(realpath -m "$3")
here=$(cd "$(dirname "$0")" && pwd)
skill="$(dirname "$here")/SKILL.md"
model=${EVAL_MODEL:-claude-opus-5-5}
sim_model=${EVAL_SIM_MODEL:-claude-sonnet-5-5}
mkdir -p "$out/work" "$out/records"
cd "$out/work"

# No shell for the agent under test: Write creates the records dir, and host settings
# pre-approve Bash, so it must be denied explicitly rather than just left off the list.
tools="Agent Read Glob Grep Write Edit WebSearch WebFetch"
opening=$(cat "$scen/opening.txt")
if [[ $arm == skill ]]; then
  first="Use the decide skill: read $skill (and its references when it says so) and follow it exactly. Write decision records under $out/records instead of ~/Decisions.

$opening"
else
  first=$opening
fi

agent() { # agent <json-out> <prompt> [session]
  local resume=()
  [[ -n ${3:-} ]] && resume=(--resume "$3")
  printf '%s' "$2" | DECIDE_DIR="$out/records" claude -p --model "$model" \
    --output-format json --permission-mode acceptEdits "${resume[@]}" \
    --disallowedTools Bash --allowedTools $tools > "$1"
}

sim() { # sim <agent-reply> -> answer on stdout
  printf '%s' "You are role-playing Louis, who asked an AI agent for help with a decision. Below are the ONLY facts you know. Answer the agent's questions using these facts. Rules: answer only what is asked; never volunteer a fact nobody asked about; if asked something not covered, say you don't know; if the agent asks you to confirm or correct a list, confirm it and add only facts that directly answer what it asked; if it asks nothing, reply 'Looks right, go ahead.' Keep it short, like a busy person on a phone.

FACTS:
$(cat "$scen/hidden.md")

AGENT'S MESSAGE:
$1" | claude -p --model "$sim_model" --output-format json --tools "" | jq -r .result
}

agent t1.json "$first"
sid=$(jq -r .session_id t1.json)
r1=$(jq -r .result t1.json)
a1=$(sim "$r1")
agent t2.json "$a1" "$sid"
r2=$(jq -r .result t2.json)
transcript="## Person
$opening

## Agent
$r1

## Person
$a1

## Agent
$r2"

if [[ -f $scen/late.txt ]]; then
  late=$(cat "$scen/late.txt")
  agent t3.json "$late" "$sid"
  transcript+="

## Person (late constraint)
$late

## Agent
$(jq -r .result t3.json)"
fi

printf '%s\n' "$transcript" > "$out/transcript.md"
printf '%s' "$(cat "$here/rubric.md")

# Scenario reference
$(cat "$scen/reference.md")

# Transcript
$transcript" | claude -p --model "$model" --output-format json --tools "" \
  | jq -r .result | sed '/^```/d' > "$out/score.json"
jq -c . "$out/score.json" || cat "$out/score.json"

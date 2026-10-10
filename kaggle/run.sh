#!/bin/sh
# Push, run, download and publish the MemoryTrap Bench tasks on Kaggle.
#
#     sh kaggle/run.sh all        # pilot, push, run, download (no publish)
#     sh kaggle/run.sh pilot      # 3 cold items on the two cheap models
#     sh kaggle/run.sh push       # the three tasks
#     sh kaggle/run.sh run        # every model on every task, waits
#     sh kaggle/run.sh download   # run files into results/kaggle/<condition>/
#     sh kaggle/run.sh publish    # make the three tasks public
#
# push, run, download and publish take a condition as a second argument to
# handle one task: sh kaggle/run.sh run cold
#
# Needs a Kaggle account with Benchmarks access and the kaggle CLI (2.2.4 or
# later) logged in. Task files are generated with
# `uv run python -m memtrap.build_tasks` and `--pilot 3`.
set -eu
cd "$(dirname "$0")/.."

MODELS="gemini-3.5-flash-lite gemini-3.8-flash gemini-3.1-pro-preview gemini-2.5-flash
claude-haiku-5-5 claude-opus-5-5-default claude-haiku-4-5-20251001
gpt-5.4-nano-2026-03-17 gpt-6.1-sol
gpt-oss-120b gemma-4-31b-it qwen3-coder-480b-a35b-instruct"
PILOT_MODELS="gemini-3.5-flash-lite gpt-5.4-nano-2026-03-17"
ALL_TASKS="cold:memorytrap-cold versioned:memorytrap-versioned evidence:memorytrap-evidence"
WAIT=7200

ONLY=${2:-}
TASKS=""
for pair in $ALL_TASKS; do
    if [ -z "$ONLY" ] || [ "${pair%%:*}" = "$ONLY" ]; then TASKS="$TASKS $pair"; fi
done
if [ -z "$TASKS" ]; then
    echo "unknown condition: $ONLY (cold, versioned or evidence)" >&2
    exit 2
fi

model_flags() {
    for m in $1; do printf -- '-m %s ' "$m"; done
}

# One unknown slug makes the CLI refuse the whole batch, so drop absent
# models first and say which.
available_models() {
    list=$(kaggle b t models)
    for m in $1; do
        if printf '%s\n' "$list" | awk '{print $1}' | grep -qx "$m"; then
            printf '%s ' "$m"
        else
            echo "skipping $m: not in kaggle b t models" >&2
        fi
    done
}

pilot() {
    kaggle b t push memorytrap-pilot-a -f kaggle/memtrap_pilot.py --wait "$WAIT"
    # shellcheck disable=SC2046
    kaggle b t run memorytrap-pilot-a $(model_flags "$(available_models "$PILOT_MODELS")") --wait "$WAIT"
    kaggle b t status memorytrap-pilot-a
    kaggle b t download memorytrap-pilot-a -o results/kaggle/pilot -f
}

push() {
    for pair in $TASKS; do
        cond=${pair%%:*}; slug=${pair#*:}
        kaggle b t push "$slug" -f "kaggle/memtrap_$cond.py" --wait "$WAIT"
    done
}

run() {
    models=$(available_models "$MODELS")
    for pair in $TASKS; do
        slug=${pair#*:}
        # shellcheck disable=SC2046
        kaggle b t run "$slug" $(model_flags "$models") --wait "$WAIT" || echo "$slug: the run reported a failure"
        kaggle b t status "$slug" || true
    done
}

download() {
    for pair in $TASKS; do
        cond=${pair%%:*}; slug=${pair#*:}
        kaggle b t download "$slug" -o "results/kaggle/$cond" -f || echo "$slug: download failed; rerun sh kaggle/run.sh download"
        kaggle b t status "$slug" > "results/kaggle/$cond/status.txt" 2>&1 || true
    done
}

publish() {
    for pair in $TASKS; do
        kaggle b t publish "${pair#*:}"
    done
}

case "${1:-}" in
    pilot) pilot ;;
    push) push ;;
    run) run ;;
    download) download ;;
    publish) publish ;;
    all) pilot; push; run; download ;;
    *) sed -n '2,16p' "$0"; exit 2 ;;
esac

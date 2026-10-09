#!/bin/sh
# Runs every harness whose runtime is installed, three times each, and writes
# results/measurements/local-<runtime>-<major.minor>-<n>.json.
#
#     sh measure/run.sh
set -eu
cd "$(dirname "$0")/.."
out=results/measurements
mkdir -p "$out"
ran=0

if command -v go >/dev/null 2>&1; then
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    GOTOOLCHAIN=local go build -o "$tmp/measure-go" ./measure/go
    v=$(go env GOVERSION | sed -E 's/^go([0-9]+\.[0-9]+).*/\1/')
    for i in 1 2 3; do
        "$tmp/measure-go" > "$out/local-go-$v-$i.json"
        echo "wrote $out/local-go-$v-$i.json"
    done
    ran=1
fi

if command -v php >/dev/null 2>&1; then
    v=$(php -r 'echo PHP_MAJOR_VERSION, ".", PHP_MINOR_VERSION;')
    for i in 1 2 3; do
        php -d memory_limit=2G -d opcache.enable_cli=0 measure/php/run.php > "$out/local-php-$v-$i.json"
        echo "wrote $out/local-php-$v-$i.json"
    done
    ran=1
fi

if [ "$ran" = 0 ]; then
    echo "neither go nor php is installed" >&2
    exit 1
fi

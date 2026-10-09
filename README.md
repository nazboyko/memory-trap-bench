# MemoryTrap Bench

Fifteen memory questions about PHP arrays, Go maps and goroutine stacks, asked
to language models and scored against numbers measured on pinned runtime
versions.

![Measured memory ratio by runtime version for P1, P4 and G3](results/charts/split_cases.png)

PHP and Go developers carry rules of thumb about memory, and the runtimes
changed under several of them. On PHP 8.1 an array of one million integers
built in reverse order takes 1.25 times the memory of the same numbers from
`range()`; on PHP 8.3 it takes 2.50 times, because packed arrays stopped
storing keys in PHP 8.2. The benchmark asks whether a model knows the rule,
the runtime, or the version.

Model results come from three Kaggle Benchmarks tasks. They are pending until
the Kaggle runs finish; see [results/REPORT.md](results/REPORT.md).

## The cases

The harness measured every answer below. The rule of thumb is the option a
developer would pick from memory. Three cases are controls, where the
rule holds, so a model that bets against every rule loses points there.

| Case | Question in one line | Rule of thumb | Measured |
|---|---|---|---|
| P1 | `range(1, 1e6)` vs the same keys inserted from 1e6 down to 1 | About the same | PHP 8.1: 25% more. PHP 8.3: 2.5x as much |
| P2 | `unset()` every second element of a 1e6 list | Drops by about half | PHP 8.1 and 8.3: stays the same |
| P3 | 100k objects with 3 declared properties vs 100k 3-key arrays | Arrays take half as much | PHP 8.1 and 8.3: arrays take about 3 times as much |
| P4 | `SplFixedArray(1e6)` vs a plain list of 1e6 ints | SplFixedArray takes half as much | PHP 8.1: half as much. PHP 8.3: about the same |
| P5 | `$b = $a;` then `$b[] = 0;` on a 1e6 list | The assignment copies | PHP 8.3: the assignment adds nothing; the append copies |
| P6 | int keys vs numeric string keys, 1e6 each | String keys take twice as much | PHP 8.3: about the same |
| G1 | `map[int64]int64` with 1e6 entries, heap per entry | About 16 bytes per entry | Go 1.23 and 1.24: about 40 bytes per entry |
| G2 | delete every key vs `clear(m)`, then GC | Both give the memory back | Go 1.23 and 1.24: both keep the full size |
| G3 | `map[int64]struct{}` vs `map[int64]bool` | The set is half the size | Go 1.23: about 10% smaller. Go 1.24: the same |
| G4 (control) | `make(map, 1e6)` vs grow from empty, total bytes allocated | Growing allocates twice as much | Go 1.24: twice as much |
| G5 | `map[int64][256]byte` vs `map[int64]*[256]byte` | The value map is 3 times larger | Go 1.24: about the same |
| R1 (control) | 100k goroutines blocked on a channel, stack each | About 2 KB | Go 1.24: about 2 KB |
| R2 | 100k goroutines holding an 8 KB local array, stack each | About 10 KB | Go 1.24: about 16 KB |
| R3 | stack left after `deep(1000)` with 1 KB frames returns, two GCs later | Back to about 2 KB | Go 1.24: a quarter of the peak |
| R4 (control) | new idle goroutines started after a GC saw 16 KB stacks | About 2 KB | Go 1.24: about 2 KB |

The exact code, options and mechanisms are in [cases/cases.json](cases/cases.json);
the measured values for every version are in [cases/truth.json](cases/truth.json).

## How it works

**Harness.** [measure/php/run.php](measure/php/run.php) builds each structure
in its own function and reads `memory_get_usage()` after
`gc_collect_cycles()`, while the structure is still referenced.
[measure/go/main.go](measure/go/main.go) runs every case in its own child
process, turns the collector off while it builds, calls `runtime.GC()` and
reads `runtime.MemStats` (HeapInuse for maps, StackInuse for goroutine stacks,
TotalAlloc for G4). Deltas are signed, and every structure stays alive until
after its reading.

**Versions.** A GitHub Actions matrix ([measure.yml](.github/workflows/measure.yml))
runs the harness three times on PHP 8.1, 8.2, 8.3 and 8.4 (CLI, NTS, opcache
off) and on Go 1.23, 1.24, 1.25 and 1.26 (linux/amd64). The readings are in
[results/measurements/](results/measurements/).

**Truth.** `python -m memtrap.truth` takes the median of the three readings
and lets each case's decision rule in `cases.json` pick the answer from the
numbers. When the versions a case names disagree, the correct answer without
a version is "it depends on the version".

**Three conditions, three Kaggle tasks.** Each case is asked three times:
with the code only (cold), with the code and the runtime version
(versioned), and with the version plus one measured fact from another case of
the same family (evidence). Where the measured answer changes between
versions, the versioned and evidence tasks ask once per version. That makes
15, 18 and 18 prompts.

**Scoring.** No LLM judge. The reply is JSON with an answer letter, an
estimate in MB or KB, a mechanism letter, a confidence and a short reasoning.
Each case scores one third for the answer, one third for an estimate within
0.67x to 1.5x of the measured value, and one third for the mechanism. A reply
that cannot be parsed after one repair turn scores zero and is counted as a
parse failure. The task score is the mean over its prompts.

## Reproduce

Measure on whatever PHP and Go you have installed (three readings each into
`results/measurements/local-*.json`):

```bash
sh measure/run.sh
```

Then merge, check and test (needs [uv](https://docs.astral.sh/uv/)):

```bash
uv run python -m memtrap.truth
uv run python -m memtrap.validate
uv run pytest
```

`memtrap.truth` prefers the Actions readings (`gha-*.json`) for a version that
has them. To rerun the full version matrix, fork the repository and run
`gh workflow run measure.yml`, then `gh run download <run id> -D <dir>` and
copy the JSON files into `results/measurements/`.

Kaggle tasks:

```bash
uv run python -m memtrap.build_tasks
uv run python -m memtrap.stub_run
sh kaggle/run.sh all
uv run python -m memtrap.analyze
```

`build_tasks` writes the three task files under [kaggle/](kaggle/) from the
catalog and the truth. `stub_run` runs them offline against three fake models
(always right, always the rule of thumb, never JSON) as a check of parsing and
scoring. `kaggle/run.sh` pushes the tasks, runs the model lineup, and
downloads the run files into `results/kaggle/`. `analyze` rescores every raw
reply and writes [results/REPORT.md](results/REPORT.md).

## Models

Twelve models from Kaggle's model list, on three axes: a small and a flagship
model per vendor, a newer against an older generation of the same vendor, and
open weights: `gemini-3.5-flash-lite`, `gemini-3.8-flash`,
`gemini-3.1-pro-preview`, `gemini-2.5-flash`, `claude-haiku-5-5`,
`claude-opus-5-5-default`, `claude-haiku-4-5-20251001`,
`gpt-5.4-nano-2026-03-17`, `gpt-6.1-sol`, `gpt-oss-120b`, `gemma-4-31b-it`,
`qwen3-coder-480b-a35b-instruct`.

## Limits

- Fifteen cases, one run per model and condition.
- One machine type per version (GitHub's ubuntu-24.04 runner). Other builds,
  allocators and architectures can give other numbers; the local Go 1.26
  readings on darwin/arm64 are kept as a cross-check.
- The Kaggle model proxy ignores the temperature setting, so a rerun can
  differ.
- The estimate tolerance (0.67x to 1.5x) is wide on purpose; the answer and
  the mechanism carry two thirds of the score.
- The human baseline is one developer.

## Credits

- [Kaggle Benchmarks](https://www.kaggle.com/benchmarks) and the
  `kaggle-benchmarks` Python library run the tasks and the models.
- [shivammathur/setup-php](https://github.com/shivammathur/setup-php) and
  [actions/setup-go](https://github.com/actions/setup-go) provide the runtime
  versions in CI.
- The PHP packed-array change measured between 8.1 and 8.2 is
  [php-src pull request 7491](https://github.com/php/php-src/pull/7491) by
  Dmitry Stogov ("Store just an array of zvals without keys").
- [Go 1.24 release notes](https://go.dev/doc/go1.24#runtime) (the map
  implementation based on Swiss Tables) and
  [Go 1.19 release notes](https://go.dev/doc/go1.19#runtime) (initial
  goroutine stacks sized from historic average stack usage).

## Contest

Built for the DEV x Kaggle Benchmarking Challenge (entries from September 23
to October 11, 2026) by Nazar Boyko ([nazboyko](https://github.com/nazboyko)).

## License

MIT, see [LICENSE](LICENSE).

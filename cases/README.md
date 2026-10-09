# Cases

`cases.json` is the catalog of benchmark cases. `truth.json` holds the measured
answers per runtime version and is generated from `results/measurements/` by
`python -m memtrap.truth`; never edit it by hand.

`python -m memtrap.validate` checks both files.

## cases.json fields

| Field | Meaning |
|---|---|
| `id` | `P*` PHP arrays, `G*` Go maps, `R*` goroutine stacks |
| `runtime`, `family` | `php`/`arrays`, `go`/`maps`, `go`/`stacks` |
| `control` | `true` when the rule of thumb is right; the folklore mechanism is then the correct one and is never counted as folklore |
| `status` | `draft` (not measured yet, the task builder refuses it), `measured`, or `reserve` (measured, not in the benchmark) |
| `snippet_a`, `snippet_b` | the code the model sees; `snippet_b` may be empty |
| `question`, `options` | the question and four answer options A-D |
| `version_option` | the letter of "It depends on the ... version", or `null`. It exists only where the measured answer differs between the versions the case names |
| `folklore_answer` | the option the rule of thumb picks |
| `mechanisms`, `correct_mechanism`, `folklore_mechanism` | four explanations; one is correct, one is the folklore explanation |
| `variants` | the readings the harness takes for this case, by name |
| `decision` | how the measured readings pick an answer, see below |
| `estimate` | what the model estimates (`what`, `unit` MB or KB), how to compute it from the readings (`expr`), the metric, and the accepted range as a ratio of the measured value (`tolerance`) |
| `versions` | the runtime versions the prompts name. With a version split, the versioned task asks once per version |
| `evidence` | the measured fact for the evidence condition: the source case (`from`) and a sentence with placeholders such as `{b_MB}` or `{peak_KB_each}`, filled from the source case's truth at the version the prompt names |
| `why_it_matters`, `folklore_line` | one line each, for the report |

Sizes are binary: 1 MB = 1,048,576 bytes, 1 KB = 1,024 bytes.

## Decision rules

```json
"decision": {
  "quantities": {"r": "b / a"},
  "rules": [
    {"option": "A", "when": {"r": [0.9, 1.1]}},
    {"option": "B", "when": {"r": [1.1, 1.6]}},
    {"option": "C", "when": {"r": [2.0, 3.2]}}
  ]
}
```

Quantities are arithmetic over variant names, `n` (the element or goroutine
count), `MB` and `KB`. A rule matches when every quantity it names lies in
`[lo, hi)`; the first match wins. A reading that matches no rule leaves the
case unresolved, and the case has to be rewritten around what was measured.

The cold answer of a case is the shared measured option when every named
version agrees, and the `version_option` when they differ.

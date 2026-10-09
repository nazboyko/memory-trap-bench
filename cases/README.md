# Cases

`cases.json` is the catalog of benchmark cases. `truth.json` holds the measured
answers per runtime version and is generated from `results/measurements/` by
`python -m memtrap.truth`; never edit it by hand.

## cases.json schema

```json
{
  "id": "P1",
  "runtime": "php",
  "family": "arrays",
  "control": false,
  "status": "measured",
  "title": "Packed array versus the same keys inserted in reverse",
  "snippet_a": "$a = range(1, 1_000_000);",
  "snippet_b": "$b = [];\nfor ($i = 1_000_000; $i > 0; $i--) {\n    $b[$i] = $i;\n}",
  "question": "After both arrays are built, how does the memory used by $b compare with $a?",
  "options": {
    "A": "About the same",
    "B": "$b uses roughly 1.5x more",
    "C": "$b uses roughly 2.5x more",
    "D": "It depends on the PHP version"
  },
  "folklore_answer": "A",
  "mechanisms": {
    "A": "Reverse insertion keeps the array packed but adds a sorted index",
    "B": "Both arrays use the same hash table; only iteration order differs",
    "C": "Reverse insertion forces the hash representation with 32-byte buckets and a hash index, while sequential keys keep the packed layout",
    "D": "Integer keys above the initial capacity trigger a rehash that doubles the bucket size"
  },
  "correct_mechanism": "C",
  "folklore_mechanism": "B",
  "estimate": {
    "what": "memory added by $b, in MB",
    "unit": "MB",
    "metric": "memory_get_usage",
    "tolerance": [0.67, 1.5]
  },
  "versions": ["php-8.1", "php-8.2", "php-8.3", "php-8.4"],
  "evidence_from": "P2",
  "why_it_matters": "A worker that builds a lookup table in reverse order can hit its container limit at a third of the expected size.",
  "folklore_line": "An associative array is a little heavier than a list, that is all."
}
```

The correct answer per version lives in `truth.json`, not here. The builder
derives the cold answer: if the measured option differs between versions, the
cold answer is the "depends on version" option; otherwise it is the shared
measured option.

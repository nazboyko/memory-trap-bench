<?php

/*
 * Measures the PHP cases of MemoryTrap Bench and prints one JSON document.
 *
 *     php -d memory_limit=2G -d opcache.enable_cli=0 measure/php/run.php
 *
 * Every reading is a memory_get_usage() delta (the Zend allocator's view, not
 * the reserved chunks that memory_get_usage(true) reports), taken after
 * gc_collect_cycles(), while the measured structure is still referenced.
 * Each variant runs in its own function, so its locals are gone before the
 * next baseline is read. Any error exits non-zero with nothing on stdout.
 */

declare(strict_types=1);

const N = 1_000_000;
const OBJECTS = 100_000;

final class Point
{
    public function __construct(
        public int $x,
        public int $y,
        public int $z,
    ) {}
}

function usage(): int
{
    gc_collect_cycles();
    return memory_get_usage();
}

function reading(string $case, string $variant, int $value, int $n): array
{
    return [
        'case' => $case,
        'variant' => $variant,
        'metric' => 'memory_get_usage',
        'unit' => 'bytes',
        'value' => $value,
        'n' => $n,
    ];
}

// P1: packed list against the same integers inserted in reverse.
function p1a(): int
{
    $base = usage();
    $a = range(1, 1_000_000);
    $delta = usage() - $base;
    unset($a);
    return $delta;
}

function p1b(): int
{
    $base = usage();
    $b = [];
    for ($i = 1_000_000; $i >= 1; $i--) {
        $b[$i] = $i;
    }
    $delta = usage() - $base;
    unset($b);
    return $delta;
}

// P2: unset every second element of a packed list.
function p2(): array
{
    $base = usage();
    $a = range(1, 1_000_000);
    $before = usage() - $base;
    for ($i = 0; $i < 1_000_000; $i += 2) {
        unset($a[$i]);
    }
    $after = usage() - $base;
    if (count($a) !== 500_000) {
        throw new RuntimeException('P2: unexpected element count');
    }
    unset($a);
    return [$before, $after];
}

// P3: objects with declared properties against arrays with the same keys.
function p3a(): int
{
    $base = usage();
    $objects = [];
    for ($i = 0; $i < 100_000; $i++) {
        $objects[] = new Point($i, $i, $i);
    }
    $delta = usage() - $base;
    unset($objects);
    return $delta;
}

function p3b(): int
{
    $base = usage();
    $arrays = [];
    for ($i = 0; $i < 100_000; $i++) {
        $arrays[] = ['x' => $i, 'y' => $i, 'z' => $i];
    }
    $delta = usage() - $base;
    unset($arrays);
    return $delta;
}

// P4: SplFixedArray against a plain list of integers.
function p4a(): int
{
    $base = usage();
    $fixed = new SplFixedArray(1_000_000);
    for ($i = 0; $i < 1_000_000; $i++) {
        $fixed[$i] = $i;
    }
    $delta = usage() - $base;
    unset($fixed);
    return $delta;
}

function p4b(): int
{
    $base = usage();
    $list = [];
    for ($i = 0; $i < 1_000_000; $i++) {
        $list[] = $i;
    }
    $delta = usage() - $base;
    unset($list);
    return $delta;
}

// P5: assignment of a big array, then one append (copy on write).
function p5(): array
{
    $base = usage();
    $a = range(1, 1_000_000);
    $afterLine1 = usage();
    $b = $a;     // line 2
    $afterLine2 = usage();
    $b[] = 0;    // line 3
    $afterLine3 = usage();
    if (count($b) !== count($a) + 1) {
        throw new RuntimeException('P5: append did not happen');
    }
    unset($a, $b);
    return [$afterLine1 - $base, $afterLine2 - $afterLine1, $afterLine3 - $afterLine2];
}

// P6: integer keys against numeric string keys.
function p6a(): int
{
    $base = usage();
    $ints = [];
    for ($i = 0; $i < 1_000_000; $i++) {
        $ints[$i] = $i;
    }
    $delta = usage() - $base;
    unset($ints);
    return $delta;
}

function p6b(): int
{
    $base = usage();
    $strings = [];
    for ($i = 0; $i < 1_000_000; $i++) {
        $strings[(string) $i] = $i;
    }
    $delta = usage() - $base;
    unset($strings);
    return $delta;
}

// P7 (reserve): unset() of the first half against array_slice().
function p7a(): int
{
    $base = usage();
    $a = range(1, 1_000_000);
    for ($i = 0; $i < 500_000; $i++) {
        unset($a[$i]);
    }
    $delta = usage() - $base;
    unset($a);
    return $delta;
}

function p7b(): int
{
    $base = usage();
    $b = range(1, 1_000_000);
    $b = array_slice($b, 500_000);
    $delta = usage() - $base;
    unset($b);
    return $delta;
}

// P8 (reserve): one million two-element arrays.
function p8(): int
{
    $base = usage();
    $pairs = [];
    for ($i = 0; $i < 1_000_000; $i++) {
        $pairs[] = [$i, $i];
    }
    $delta = usage() - $base;
    unset($pairs);
    return $delta;
}

function measure(): array
{
    $r = [];
    $r[] = reading('P1', 'a', p1a(), N);
    $r[] = reading('P1', 'b', p1b(), N);
    [$before, $after] = p2();
    $r[] = reading('P2', 'a', $before, N);
    $r[] = reading('P2', 'b', $after, N);
    $r[] = reading('P3', 'a', p3a(), OBJECTS);
    $r[] = reading('P3', 'b', p3b(), OBJECTS);
    $r[] = reading('P4', 'a', p4a(), N);
    $r[] = reading('P4', 'b', p4b(), N);
    [$line1, $line2, $line3] = p5();
    $r[] = reading('P5', 'a', $line1, N);
    $r[] = reading('P5', 'b', $line2, N);
    $r[] = reading('P5', 'c', $line3, N);
    $r[] = reading('P6', 'a', p6a(), N);
    $r[] = reading('P6', 'b', p6b(), N);
    $r[] = reading('P7', 'a', p7a(), N);
    $r[] = reading('P7', 'b', p7b(), N);
    $r[] = reading('P8', 'a', p8(), N);
    return $r;
}

try {
    $readings = measure();
    $report = [
        'runtime' => 'php',
        'version' => PHP_VERSION,
        'sapi' => PHP_SAPI,
        'os' => PHP_OS_FAMILY,
        'thread_safe' => (bool) ZEND_THREAD_SAFE,
        'opcache' => extension_loaded('Zend OPcache') && (bool) ini_get('opcache.enable_cli'),
        'memory_limit' => ini_get('memory_limit'),
        'readings' => $readings,
    ];
    echo json_encode($report, JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR), "\n";
} catch (Throwable $e) {
    fwrite(STDERR, 'measure: ' . $e->getMessage() . "\n");
    exit(1);
}

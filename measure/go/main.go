// The measure/go command measures the Go cases of MemoryTrap Bench and
// prints one JSON document on stdout.
//
// Every case runs in its own child process (the binary re-executes itself
// with -case), so runtime state such as the adaptive starting stack size
// cannot leak from one case into the next. Inside a case the collector is
// disabled while a structure is built, runtime.GC() runs before a reading
// unless the case says otherwise, and every structure is kept alive with
// runtime.KeepAlive until after its reading. Deltas are signed.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"runtime/debug"
	"sync"
	"sync/atomic"
	"time"
)

type reading struct {
	Case    string `json:"case"`
	Variant string `json:"variant"`
	Metric  string `json:"metric"`
	Unit    string `json:"unit"`
	Value   int64  `json:"value"`
	N       int    `json:"n"`
}

type report struct {
	Runtime  string    `json:"runtime"`
	Version  string    `json:"version"`
	GOOS     string    `json:"goos"`
	GOARCH   string    `json:"goarch"`
	GODEBUG  string    `json:"godebug"`
	Readings []reading `json:"readings"`
}

var cases = []struct {
	id  string
	run func() []reading
}{
	{"G1", caseG1},
	{"G2", caseG2},
	{"G3", caseG3},
	{"G4", caseG4},
	{"G5", caseG5},
	{"R1", caseR1},
	{"R2", caseR2},
	{"R3", caseR3},
	{"R4", caseR4},
	{"R5", caseR5},
	{"R6", caseR6},
}

func main() {
	only := flag.String("case", "", "run a single case in this process and print its readings")
	flag.Parse()
	debug.SetGCPercent(-1)

	if *only != "" {
		for _, c := range cases {
			if c.id == *only {
				if err := json.NewEncoder(os.Stdout).Encode(c.run()); err != nil {
					fatal(err)
				}
				return
			}
		}
		fatal(fmt.Errorf("unknown case %q", *only))
	}

	self, err := os.Executable()
	if err != nil {
		fatal(err)
	}
	rep := report{
		Runtime: "go",
		Version: runtime.Version(),
		GOOS:    runtime.GOOS,
		GOARCH:  runtime.GOARCH,
		GODEBUG: os.Getenv("GODEBUG"),
	}
	for _, c := range cases {
		cmd := exec.Command(self, "-case", c.id)
		cmd.Stderr = os.Stderr
		out, err := cmd.Output()
		if err != nil {
			fatal(fmt.Errorf("case %s: %w", c.id, err))
		}
		var rs []reading
		if err := json.Unmarshal(out, &rs); err != nil {
			fatal(fmt.Errorf("case %s: %w", c.id, err))
		}
		rep.Readings = append(rep.Readings, rs...)
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(rep); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "measure:", err)
	os.Exit(1)
}

// stats returns the memory statistics, after a full collection when gc is set.
// runtime.GC also finishes the sweep, so HeapInuse is settled afterwards.
func stats(gc bool) runtime.MemStats {
	if gc {
		runtime.GC()
	}
	var ms runtime.MemStats
	runtime.ReadMemStats(&ms)
	return ms
}

func heapDelta(id, variant string, before, after runtime.MemStats, n int) reading {
	return reading{id, variant, "HeapInuse", "bytes", int64(after.HeapInuse) - int64(before.HeapInuse), n}
}

func allocDelta(id, variant string, before, after runtime.MemStats, n int) reading {
	return reading{id, variant, "TotalAlloc", "bytes", int64(after.TotalAlloc) - int64(before.TotalAlloc), n}
}

func stackDelta(id, variant string, before, after runtime.MemStats, n int) reading {
	return reading{id, variant, "StackInuse", "bytes", int64(after.StackInuse) - int64(before.StackInuse), n}
}

const mapN = 1_000_000

// G1: bytes held by map[int64]int64 with one million entries.
func caseG1() []reading {
	before := stats(true)
	m := make(map[int64]int64)
	for i := int64(0); i < mapN; i++ {
		m[i] = i
	}
	after := stats(true)
	r := []reading{heapDelta("G1", "a", before, after, mapN)}
	runtime.KeepAlive(m)
	return r
}

// G2: the same map after deleting every key, and a second map after clear(m).
func caseG2() []reading {
	var out []reading

	base := stats(true)
	m := make(map[int64]int64)
	for i := int64(0); i < mapN; i++ {
		m[i] = i
	}
	full := stats(true)
	out = append(out, heapDelta("G2", "a", base, full, mapN))
	for i := int64(0); i < mapN; i++ {
		delete(m, i)
	}
	afterDelete := stats(true)
	out = append(out, heapDelta("G2", "b", base, afterDelete, mapN))
	runtime.GC()
	runtime.GC()
	afterDelete2 := stats(true)
	out = append(out, heapDelta("G2", "b_later", base, afterDelete2, mapN))
	if len(m) != 0 {
		panic("map not empty after delete")
	}
	runtime.KeepAlive(m)
	m = nil

	base2 := stats(true)
	m2 := make(map[int64]int64)
	for i := int64(0); i < mapN; i++ {
		m2[i] = i
	}
	clear(m2)
	afterClear := stats(true)
	out = append(out, heapDelta("G2", "c", base2, afterClear, mapN))
	runtime.GC()
	runtime.GC()
	afterClear2 := stats(true)
	out = append(out, heapDelta("G2", "c_later", base2, afterClear2, mapN))
	runtime.KeepAlive(m2)
	return out
}

// G3: map[int64]struct{} against map[int64]bool.
func caseG3() []reading {
	var out []reading

	before := stats(true)
	set := make(map[int64]struct{})
	for i := int64(0); i < mapN; i++ {
		set[i] = struct{}{}
	}
	after := stats(true)
	out = append(out, heapDelta("G3", "a", before, after, mapN))
	runtime.KeepAlive(set)
	set = nil

	before = stats(true)
	flags := make(map[int64]bool)
	for i := int64(0); i < mapN; i++ {
		flags[i] = true
	}
	after = stats(true)
	out = append(out, heapDelta("G3", "b", before, after, mapN))
	runtime.KeepAlive(flags)
	return out
}

// G4: make(map, n) then fill, against growing from empty. Heap in use at the
// end and total bytes allocated on the way.
func caseG4() []reading {
	var out []reading

	before := stats(true)
	pre := make(map[int64]int64, mapN)
	for i := int64(0); i < mapN; i++ {
		pre[i] = i
	}
	after := stats(true)
	out = append(out, heapDelta("G4", "a", before, after, mapN), allocDelta("G4", "a_total", before, after, mapN))
	runtime.KeepAlive(pre)
	pre = nil

	before = stats(true)
	grown := make(map[int64]int64)
	for i := int64(0); i < mapN; i++ {
		grown[i] = i
	}
	after = stats(true)
	out = append(out, heapDelta("G4", "b", before, after, mapN), allocDelta("G4", "b_total", before, after, mapN))
	runtime.KeepAlive(grown)
	return out
}

const bigN = 100_000

// G5: 256-byte array values stored in the map against pointers to them.
func caseG5() []reading {
	var out []reading

	before := stats(true)
	byValue := make(map[int64][256]byte)
	for i := int64(0); i < bigN; i++ {
		var v [256]byte
		v[0] = byte(i)
		byValue[i] = v
	}
	after := stats(true)
	out = append(out, heapDelta("G5", "a", before, after, bigN))
	runtime.KeepAlive(byValue)
	byValue = nil

	before = stats(true)
	byPointer := make(map[int64]*[256]byte)
	for i := int64(0); i < bigN; i++ {
		v := new([256]byte)
		v[0] = byte(i)
		byPointer[i] = v
	}
	after = stats(true)
	out = append(out, heapDelta("G5", "b", before, after, bigN))
	runtime.KeepAlive(byPointer)
	return out
}

const goroutineN = 100_000

// R1: goroutines blocked on a channel, nothing on their stacks.
func caseR1() []reading {
	done := make(chan struct{})
	var started sync.WaitGroup
	started.Add(goroutineN)
	before := stats(true)
	for i := 0; i < goroutineN; i++ {
		go func() {
			started.Done()
			<-done
		}()
	}
	started.Wait()
	after := stats(true)
	out := []reading{
		stackDelta("R1", "a", before, after, goroutineN),
		heapDelta("R1", "a_heap", before, after, goroutineN),
	}
	close(done)
	return out
}

var sink atomic.Int64

// hold8k keeps an 8 KB array on its stack while it waits.
func hold8k(started *sync.WaitGroup, done <-chan struct{}) {
	var buf [8192]byte
	for i := range buf {
		buf[i] = byte(i)
	}
	started.Done()
	<-done
	sink.Add(int64(buf[len(buf)-1]))
}

func startHolders(n int, done <-chan struct{}) {
	var started sync.WaitGroup
	started.Add(n)
	for i := 0; i < n; i++ {
		go hold8k(&started, done)
	}
	started.Wait()
}

// R2: goroutines that each hold an 8 KB local array.
func caseR2() []reading {
	done := make(chan struct{})
	before := stats(true)
	startHolders(goroutineN, done)
	after := stats(true)
	out := []reading{stackDelta("R2", "a", before, after, goroutineN)}
	close(done)
	return out
}

// deep recurses n levels with a 1 KB frame.
func deep(n int) byte {
	var pad [1024]byte
	pad[n%len(pad)] = byte(n)
	if n == 0 {
		return pad[0]
	}
	return deep(n-1) + pad[n%len(pad)]
}

const deepN = 100

// R3: goroutines that recursed 1000 levels deep once, returned and now wait.
// Readings are taken without an extra collection: right after the return,
// after one runtime.GC(), after two, and after four.
func caseR3() []reading {
	done := make(chan struct{})
	var returned sync.WaitGroup
	returned.Add(deepN)
	before := stats(true)
	for i := 0; i < deepN; i++ {
		go func() {
			sink.Add(int64(deep(1000)))
			returned.Done()
			<-done
		}()
	}
	returned.Wait()
	time.Sleep(100 * time.Millisecond) // let every goroutine park on the channel
	peak := stats(false)
	runtime.GC()
	gc1 := stats(false)
	runtime.GC()
	gc2 := stats(false)
	runtime.GC()
	runtime.GC()
	gc4 := stats(false)
	out := []reading{
		stackDelta("R3", "peak", before, peak, deepN),
		stackDelta("R3", "gc1", before, gc1, deepN),
		stackDelta("R3", "a", before, gc2, deepN),
		stackDelta("R3", "gc4", before, gc4, deepN),
	}
	close(done)
	return out
}

// R4: idle goroutines started after a collection that saw R2-style goroutines.
// "a" is the stack the new goroutines start with (read without a collection),
// "a_after_gc" is the same set after one more collection.
func caseR4() []reading {
	holdDone := make(chan struct{})
	startHolders(goroutineN, holdDone)
	runtime.GC()
	before := stats(false)

	done := make(chan struct{})
	var started sync.WaitGroup
	started.Add(goroutineN)
	for i := 0; i < goroutineN; i++ {
		go func() {
			started.Done()
			<-done
		}()
	}
	started.Wait()
	after := stats(false)
	afterGC := stats(true)
	out := []reading{
		stackDelta("R4", "a", before, after, goroutineN),
		stackDelta("R4", "a_after_gc", before, afterGC, goroutineN),
	}
	close(done)
	close(holdDone)
	return out
}

// big64k touches a 64 KB local array once. It must not be inlined, or its
// frame would stay part of the caller's frame after it returns.
//
//go:noinline
func big64k() byte {
	var buf [64 << 10]byte
	for i := range buf {
		buf[i] = byte(i)
	}
	return buf[len(buf)-1]
}

const r5N = 1000

// R5 (reserve): goroutines that call a function with a 64 KB local once, then
// wait. Readings right after the call and after two collections.
func caseR5() []reading {
	done := make(chan struct{})
	var ready sync.WaitGroup
	ready.Add(r5N)
	before := stats(true)
	for i := 0; i < r5N; i++ {
		go func() {
			sink.Add(int64(big64k()))
			ready.Done()
			<-done
		}()
	}
	ready.Wait()
	time.Sleep(100 * time.Millisecond)
	peak := stats(false)
	runtime.GC()
	runtime.GC()
	gc2 := stats(false)
	out := []reading{
		stackDelta("R5", "peak", before, peak, r5N),
		stackDelta("R5", "a", before, gc2, r5N),
	}
	close(done)
	return out
}

// R6 (reserve): idle goroutines started after a batch of R2-style goroutines
// was seen by a collection and then exited. New goroutines reuse the exited
// descriptors, and a reused descriptor gets the adaptive starting stack.
// Readings are totals for the second batch, without and with one collection.
func caseR6() []reading {
	base := stats(true)
	holdDone := make(chan struct{})
	var exited sync.WaitGroup
	exited.Add(goroutineN)
	var started sync.WaitGroup
	started.Add(goroutineN)
	for i := 0; i < goroutineN; i++ {
		go func() {
			hold8k(&started, holdDone)
			exited.Done()
		}()
	}
	started.Wait()
	runtime.GC()
	close(holdDone)
	exited.Wait()
	time.Sleep(100 * time.Millisecond) // let every holder finish exiting

	done := make(chan struct{})
	var idle sync.WaitGroup
	idle.Add(goroutineN)
	for i := 0; i < goroutineN; i++ {
		go func() {
			idle.Done()
			<-done
		}()
	}
	idle.Wait()
	after := stats(false)
	afterGC := stats(true)
	out := []reading{
		stackDelta("R6", "a", base, after, goroutineN),
		stackDelta("R6", "a_after_gc", base, afterGC, goroutineN),
	}
	close(done)
	return out
}

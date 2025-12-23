# Benchmarking & Profiling

This repo includes a small, reproducible benchmark harness and a profiler-friendly workload script.

- Benchmark suite: `tests/benchmark.py`
- Profiler workload: `tests/profile_decorators.py`
- Benchmark log (append-only JSON-lines): `benchmarks.log`

## 1) Benchmarking (step-by-step)

### Step 0 — Ensure the environment is ready (uv)

This repo uses `uv`. From the repo root:

```bash
uv sync
```

### Step 1 — Run the benchmark suite

```bash
uv run python tests/benchmark.py
```

What you get:
- Printed tables for **hot cache hits** (comparing TTLCache, SWRCache, BGCache).
- A new JSON entry appended to `benchmarks.log` with the config + median/mean/stdev per strategy.

### Step 2 — Tune benchmark parameters (optional)

`tests/benchmark.py` reads these environment variables:

- `BENCH_SEED` (default `12345`)
- `BENCH_WORK_MS` (default `5.0`) — simulated I/O latency (sleep)
- `BENCH_WARMUP` (default `10`)
- `BENCH_RUNS` (default `300`)

Examples:

```bash
BENCH_RUNS=1000 uv run python tests/benchmark.py
```

### Step 3 — Compare two runs

The benchmark appends JSON lines to `benchmarks.log`. A quick helper to list runs:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
runs=[]
if not Path('benchmarks.log').exists():
    print("No benchmarks.log found")
    exit(0)
for line in Path('benchmarks.log').read_text(encoding='utf-8', errors='replace').splitlines():
  line=line.strip()
  if not line.startswith('{'):
    continue
  try:
    obj=json.loads(line)
  except Exception:
    continue
  if isinstance(obj,dict) and 'sections' in obj:
    runs.append(obj)
print('count',len(runs))
for i,r in enumerate(runs):
  print(i,r.get('ts'))
PY
```

To compare two indices (e.g., 2 vs 11), load the JSON objects in a notebook or script and diff the `sections` (hot medians for TTL/SWR/BG are the most sensitive to overhead changes).

### Step 4 — Make results stable (recommended practice)

- Run each benchmark **multiple times** and compare trends, not a single result.
- Prefer a quiet machine (close CPU-heavy apps).
- Compare runs with identical config (same `BENCH_*` values).

## 2) Profiling with Scalene (step-by-step)

### Step 0 — Install Scalene into the uv env

If Scalene isn’t already available in your uv environment:

```bash
uv pip install scalene
```

Scalene is useful to answer: “where is the CPU time going?”

### Step 1 — Profile the benchmark itself (realistic)

This includes the simulated `sleep` and will mostly show “time in system / sleeping”.
It’s useful for end-to-end sanity, but not for micro-optimizing the decorators.

```bash
uv run python -m scalene --cli --reduced-profile --outfile scalene_benchmark.txt tests/benchmark.py
```

### Step 2 — Profile decorator overhead (recommended)

Run the benchmark with no artificial sleep and more iterations:

```bash
BENCH_WORK_MS=0 BENCH_RUNS=200000 BENCH_WARMUP=2000 BENCH_MIXED_RUNS=300000 \
  uv run python \
  -m scalene --cli --reduced-profile --profile-all --cpu --outfile scalene_overhead.txt \
  tests/benchmark.py
```

Notes:
- `--profile-all` includes imported modules (e.g., `src/advanced_caching/*.py`).
- `--reduced-profile` keeps output small and focused.

### Step 3 — Profile tight loops (best for line-level hotspots)

`tests/profile_decorators.py` is designed for profilers:
- It runs tight loops calling cached functions.
- It shuts down the BG scheduler at the end to reduce background-thread noise.

```bash
PROFILE_N=5000000 \
  uv run python \
  -m scalene --cli --reduced-profile --profile-all --cpu --outfile scalene_profile.txt \
  tests/profile_decorators.py
```

Optional JSON output (handy for scripting):

```bash
PROFILE_N=5000000 \
  uv run python \
  -m scalene --cli --json --outfile scalene_profile.json \
  tests/profile_decorators.py
```

## 3) What to look at (a practical checklist)

### A) Benchmark output

- **Hot path**
  - `TTLCache` hot: overhead of key generation + `get()` + return.
  - `SWRCache` hot: overhead of key generation + `get_entry()` + freshness checks.
  - `BGCache` hot: overhead of key lookup + `get()` + return.

- **Async results (important)**
  - Async medians include the cost of creating/awaiting a coroutine and event-loop scheduling.
  - For AsyncBG/AsyncSWR, compare against the `async_baseline` row (plain `await` with no cache) to estimate *cache-specific* overhead.

- **Mixed path**
  - A high mean + low median typically indicates occasional slow misses/refreshes.

### B) Scalene output

Look for time concentrated in:
- `src/advanced_caching/decorators.py`
  - key building (template formatting)
  - repeated `get_cache()` calls (should be minimized)
  - SWR “fresh vs stale” checks
- `src/advanced_caching/storage.py`
  - lock contention (`with self._lock:`)
  - `time.time()` calls
  - dict lookups (`self._data.get(key)`)

Signals that often matter:
- Lots of time in `threading.py` / `Condition.wait` / `Thread.run` usually means background threads are running and being sampled. Prefer the tight-loop profiler script and/or make sure background work is shut down.

## 4) Common pitfalls

- Comparing benchmark runs with different configs (different `BENCH_*` values).
- Profiling with `BENCH_WORK_MS=5` and expecting line-level decorator hotspots (sleep dominates).
- Treating single-run noise as a regression (always repeat).

## 5) Typical workflow

1. Run `tests/benchmark.py` (default) a few times.
2. If you change code, re-run and compare with `tests/compare_benchmarks.py`.
3. If you need to optimize, profile with:
   - `BENCH_WORK_MS=0` + `--profile-all` for imported modules
   - `tests/profile_decorators.py` for clean line-level hotspots

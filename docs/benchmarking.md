# Benchmarking Guide

This guide explains how to run benchmarks and compare performance between different versions of advanced-caching.

## Running Benchmarks

### Full Benchmark Suite

Run all benchmarks and save results to `benchmarks.log`:

```bash
uv run python tests/benchmark.py
```

The script will run multiple benchmark scenarios:
- **Cold Cache**: Initial cache misses with storage overhead
- **Hot Cache**: Repeated cache hits (best-case performance)
- **Varying Keys**: Realistic mixed workload with 100+ different keys

Results are saved in JSON format to `benchmarks.log` for later comparison.

## Comparing Benchmark Results

### View Baseline vs Current Performance

Compare the last two benchmark runs:

```bash
uv run python tests/compare_benchmarks.py
```

### Compare Specific Runs

Compare specific runs using selectors:

```bash
# Compare second-to-last run vs latest
uv run python tests/compare_benchmarks.py --a last-1 --b last

# Compare run index 0 vs run index 2
uv run python tests/compare_benchmarks.py --a 0 --b 2

# Compare run at index -3 (third from last) vs latest
uv run python tests/compare_benchmarks.py --a -3 --b last
```

### Custom Log File

If benchmarks are saved to a different file:

```bash
uv run python tests/compare_benchmarks.py --log my_benchmarks.log
```

## Understanding the Output

### Example Comparison Report

```
====================================================================================================
BENCHMARK COMPARISON REPORT
====================================================================================================

Run A (baseline): 2025-12-12T10:30:00
  Config: {'runs': 1000, 'warmup': 100}

Run B (current):  2025-12-12T10:45:30
  Config: {'runs': 1000, 'warmup': 100}

----------------------------------------------------------------------------------------------------

📊 COLD CACHE
----------------------------------------------------------------------------------------------------
Strategy                   A (ms)       B (ms)       Change         %    Status
----------------------------------------------------------------------------------------------------
TTLCache                   15.3250      14.8900     -0.4350      -2.84% ✓ FASTER (2.84%)
SWRCache                   18.5100      18.2300     -0.2800      -1.51% ✓ SAME
No Cache (baseline)        13.1000      13.0900     -0.0100      -0.08% ✓ SAME

📊 HOT CACHE
----------------------------------------------------------------------------------------------------
Strategy                   A (ms)       B (ms)       Change         %    Status
----------------------------------------------------------------------------------------------------
TTLCache                    0.0012       0.0011     -0.0001     -8.33% ✓ FASTER (8.33%)
SWRCache                    0.0014       0.0015     +0.0001      7.14% ✗ SLOWER (7.14%)
BGCache                     0.0003       0.0003     +0.0000      0.00% ✓ SAME

====================================================================================================
SUMMARY
====================================================================================================

✓ 3 IMPROVEMENT(S):
  • TTLCache                     in cold cache          →   2.84% faster
  • TTLCache                     in hot cache           →   8.33% faster
  • SWRCache                     in cold cache          →   1.51% faster

✗ 1 REGRESSION(S):
  • SWRCache                     in hot cache           →   7.14% slower

----------------------------------------------------------------------------------------------------

🎯 VERDICT: ✓ OVERALL IMPROVEMENT (avg +5.89% faster)

====================================================================================================
```

### Key Sections Explained

#### 1. Header
- **Run A (baseline)**: Previous benchmark results with timestamp
- **Run B (current)**: Latest benchmark results for comparison
- Shows configuration parameters used for both runs

#### 2. Per-Section Comparison
Groups results by benchmark scenario:
- **Strategy**: Name of the caching approach (e.g., TTLCache, SWRCache)
- **A (ms)**: Median time in baseline run (milliseconds)
- **B (ms)**: Median time in current run
- **Change**: Absolute difference (B - A) in milliseconds
- **%**: Percentage change relative to baseline
- **Status**: Visual indicator with emoji:
  - ✓ FASTER: Performance improved
  - ✓ SAME: Within 2% threshold (no significant change)
  - ✗ SLOWER: Performance regressed

#### 3. Summary
- **IMPROVEMENTS**: Strategies that got faster
  - Shows top 5 improvements
  - Sorted by percentage gain (largest first)
- **REGRESSIONS**: Strategies that got slower
  - Shows top 5 regressions
  - Sorted by percentage loss (largest first)

#### 4. Verdict
Overall performance assessment:
- **STABLE**: No significant changes (< 2% difference)
- **IMPROVEMENT**: More improvements than regressions
  - Shows average speedup percentage
- **REGRESSION**: More regressions than improvements
  - Shows average slowdown percentage

## Interpreting Results

### Performance Thresholds

- **< 2%**: Considered **same** (normal measurement noise)
- **2-5%**: **Notable** change (worth investigating)
- **> 5%**: **Significant** change (code optimization or regression)

### What to Look For

✓ **Good signs**:
- Hot cache times remain stable (< 5% change)
- Cold cache shows improvements (refactoring benefits)
- No regressions in any benchmark

✗ **Warning signs**:
- Hot cache performance degrades (potential code path regression)
- New dependencies add overhead to all runs
- Asymmetric changes (cache hits slow, misses fast)

## Benchmark Scenarios

### 1. Cold Cache
**What it tests**: Cache miss handling and data storage overhead

Measures:
- Function execution time
- Cache miss detection
- Storage backend write performance
- Different cache backends side-by-side

Use this to:
- Verify caching decorators don't add significant overhead
- Detect regression in cache backends
- Compare storage implementation performance

### 2. Hot Cache
**What it tests**: Pure cache hit speed (best case)

Measures:
- Cache lookup time
- Data deserialization
- Decorator wrapper overhead
- Hit on same key repeated 1000+ times

Use this to:
- Ensure caching is providing speed benefit
- Detect memory/performance issues
- Compare backend performance under load

### 3. Varying Keys
**What it tests**: Mixed realistic workload

Measures:
- Performance with 100+ unique keys
- Mix of hits and misses
- Cache eviction/aging behavior
- Real-world usage patterns

Use this to:
- Understand performance with realistic data
- Detect memory issues under load
- Test cache aging and refresh behavior

## Workflow Tips

### Before Making Code Changes

Save baseline benchmarks:

```bash
uv run python tests/benchmark.py
git add benchmarks.log
git commit -m "baseline: benchmark before optimization"
```

### After Code Changes

Run new benchmarks:

```bash
uv run python tests/benchmark.py
uv run python tests/compare_benchmarks.py
```

Review the comparison report and decide:
- ✓ Changes are good → commit
- ✗ Regression detected → revert or optimize further

### Benchmarking in CI/CD

GitHub Actions runs benchmarks on every push to `main`:

```yaml
- name: Run benchmarks
  run: uv run python tests/benchmark.py
```

Results are stored as artifacts for later comparison.

## Troubleshooting

### "Need at least 2 JSON runs"

You need at least 2 benchmark results to compare. Run benchmarks twice:

```bash
uv run python tests/benchmark.py  # Creates first run
uv run python tests/benchmark.py  # Creates second run
uv run python tests/compare_benchmarks.py  # Now you can compare
```

### "Unsupported selector"

Valid selectors are:
- `last` - most recent run
- `last-N` - N runs ago (e.g., `last-1`, `last-5`)
- `0`, `1`, `2` - absolute index from start
- `-1`, `-2` - absolute index from end

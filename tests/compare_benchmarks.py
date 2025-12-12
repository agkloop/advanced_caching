from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json_runs(log_path: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if (
            isinstance(obj, dict)
            and "results" in obj
            and isinstance(obj["results"], dict)
        ):
            runs.append(obj)
    return runs


def _parse_selector(spec: str) -> int:
    """Return a list index from a selector.

    Supported:
    - "last" => -1
    - "last-N" => -(N+1)
    - integer (0-based): "0", "2" ...
    - negative integer: "-1", "-2" ...
    """
    if spec == "last":
        return -1
    if spec.startswith("last-"):
        n = int(spec.split("-", 1)[1])
        return -(n + 1)
    try:
        return int(spec)
    except ValueError as e:
        raise ValueError(
            f"Unsupported selector: {spec!r}. Use 'last', 'last-N', or an integer index."
        ) from e


def _median_map(run: dict[str, Any]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    results = run.get("results", {})
    for section, rows in results.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label", ""))
            med = row.get("median_ms")
            if not label or not isinstance(med, (int, float)):
                continue
            out[(str(section), label)] = float(med)
    return out


def _print_compare(a: dict[str, Any], b: dict[str, Any]) -> None:
    a_ts = a.get("ts", "?")
    b_ts = b.get("ts", "?")
    a_cfg = a.get("config", {})
    b_cfg = b.get("config", {})

    # Header
    print("\n" + "=" * 100)
    print("BENCHMARK COMPARISON REPORT")
    print("=" * 100 + "\n")

    print(f"Run A (baseline): {a_ts}")
    print(f"  Config: {a_cfg}")
    print()
    print(f"Run B (current):  {b_ts}")
    print(f"  Config: {b_cfg}")
    print("\n" + "-" * 100 + "\n")

    a_m = _median_map(a)
    b_m = _median_map(b)
    keys = sorted(set(a_m) | set(b_m))

    # Group by section
    sections = {}
    for section, label in keys:
        if section not in sections:
            sections[section] = []
        sections[section].append(label)

    # Calculate summary statistics
    improvements = []
    regressions = []

    for section in sorted(sections.keys()):
        print(f"\n📊 {section.upper()}")
        print("-" * 100)
        print(
            f"{'Strategy':<25} {'A (ms)':>12} {'B (ms)':>12} {'Change':>12} {'%':>8} {'Status':>12}"
        )
        print("-" * 100)

        for label in sorted(sections[section]):
            a_med = a_m.get((section, label))
            b_med = b_m.get((section, label))
            if a_med is None or b_med is None:
                continue

            delta = b_med - a_med
            pct = (delta / a_med * 100.0) if a_med > 0 else 0.0

            # Determine status
            if abs(pct) < 2:
                status = "✓ SAME"
            elif pct < 0:
                status = f"✓ FASTER ({abs(pct):.1f}%)"
                improvements.append((section, label, abs(pct)))
            else:
                status = f"✗ SLOWER ({pct:.1f}%)"
                regressions.append((section, label, pct))

            delta_str = f"{delta:+.4f}"
            print(
                f"{label:<25} {a_med:>12.4f} {b_med:>12.4f} {delta_str:>12} {pct:>7.1f}% {status:>12}"
            )

    # Summary section
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    if improvements:
        print(f"\n✓ {len(improvements)} IMPROVEMENT(S):")
        for section, label, pct in sorted(improvements, key=lambda x: -x[2])[:5]:
            print(f"  • {label:<30} in {section:<15} → {pct:>6.2f}% faster")
    else:
        print("\n✓ No improvements detected")

    if regressions:
        print(f"\n✗ {len(regressions)} REGRESSION(S):")
        for section, label, pct in sorted(regressions, key=lambda x: -x[2])[:5]:
            print(f"  • {label:<30} in {section:<15} → {pct:>6.2f}% slower")
    else:
        print("\n✓ No regressions detected")

    # Overall verdict
    print("\n" + "-" * 100)
    total_changes = len(improvements) + len(regressions)
    if total_changes == 0:
        verdict = "✓ PERFORMANCE STABLE (no significant changes)"
    elif len(improvements) > len(regressions):
        avg_improvement = sum(x[2] for x in improvements) / len(improvements)
        verdict = f"✓ OVERALL IMPROVEMENT (avg +{avg_improvement:.2f}% faster)"
    else:
        avg_regression = sum(x[2] for x in regressions) / len(regressions)
        verdict = f"✗ OVERALL REGRESSION (avg +{avg_regression:.2f}% slower)"

    # Detailed analysis by section
    print("\n" + "=" * 100)
    print("DETAILED ANALYSIS BY SCENARIO")
    print("=" * 100)

    for section in sorted(sections.keys()):
        section_improvements = [x for x in improvements if x[0] == section]
        section_regressions = [x for x in regressions if x[0] == section]

        if not section_improvements and not section_regressions:
            continue

        print(f"\n🔍 {section.upper()}")

        if section_improvements:
            avg_improvement = sum(x[2] for x in section_improvements) / len(
                section_improvements
            )
            print(f"   ✓ Average improvement: {avg_improvement:.2f}%")

        if section_regressions:
            avg_regression = sum(x[2] for x in section_regressions) / len(
                section_regressions
            )
            print(f"   ✗ Average regression: {avg_regression:.2f}%")

        if not section_improvements and section_regressions:
            print(f"   ⚠️  Watch: Only regressions detected in this scenario")

    # Recommendations
    print("\n" + "=" * 100)
    print("RECOMMENDATIONS")
    print("=" * 100)

    if total_changes == 0:
        print("\n✓ No action needed. Performance is stable.")
        recommendation = "continue with current changes"
    elif len(regressions) > 0 and sum(x[2] for x in regressions) / len(regressions) > 5:
        print("\n⚠️  SIGNIFICANT REGRESSIONS DETECTED")
        print("   Consider:")
        print("   • Profiling the affected code paths")
        print("   • Reviewing recent changes for optimization issues")
        print("   • Checking for new dependencies or imports")
        recommendation = "investigate and optimize"
    elif len(improvements) > 0:
        print("\n✓ Performance improvements detected!")
        print("   Recommendation: Merge and deploy")
        recommendation = "good to merge"
    else:
        print("\n✓ No significant regressions detected")
        recommendation = "safe to merge"

    print("\n" + "=" * 100)
    print(f"\n📋 STATUS: {recommendation.upper()}\n")
    print("=" * 100 + "\n")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Compare two JSON benchmark runs in benchmarks.log"
    )
    p.add_argument("--log", default="benchmarks.log", help="Path to benchmarks.log")
    p.add_argument(
        "--a",
        default="last-1",
        help="Run selector: last, last-N, or integer index (0-based; negatives allowed)",
    )
    p.add_argument(
        "--b",
        default="last",
        help="Run selector: last, last-N, or integer index (0-based; negatives allowed)",
    )
    args = p.parse_args()

    log_path = Path(args.log)
    runs = _load_json_runs(log_path)
    if len(runs) < 2:
        raise SystemExit(f"Need at least 2 JSON runs in {log_path}")

    a = runs[_parse_selector(args.a)]
    b = runs[_parse_selector(args.b)]
    _print_compare(a, b)


if __name__ == "__main__":
    main()

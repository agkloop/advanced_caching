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

    print(f"A: {a_ts}  config={a_cfg}")
    print(f"B: {b_ts}  config={b_cfg}")
    print()

    a_m = _median_map(a)
    b_m = _median_map(b)
    keys = sorted(set(a_m) | set(b_m))

    print(
        f"{'Section':<10} {'Strategy':<12} {'A med (ms)':>12} {'B med (ms)':>12} {'Δ (ms)':>10} {'Δ %':>8}"
    )
    for section, label in keys:
        a_med = a_m.get((section, label))
        b_med = b_m.get((section, label))
        if a_med is None or b_med is None:
            continue
        delta = b_med - a_med
        pct = (delta / a_med * 100.0) if a_med > 0 else 0.0
        print(
            f"{section:<10} {label:<12} {a_med:>12.6f} {b_med:>12.6f} {delta:>10.6f} {pct:>8.2f}"
        )


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

"""Parse a Locust CSV stats file and assert latency + error-rate thresholds.

Usage:
    python3 tests/stress/check_results.py <stats_csv> <error_rate> [p95_ms] [p99_ms]

Arguments:
    stats_csv       Path to Locust *_stats.csv output file
    error_rate      Max allowed error rate as a fraction  (e.g. 0.01 = 1%)
    p95_ms          (optional) Max allowed p95 response time in ms (0 = skip)
    p99_ms          (optional) Max allowed p99 response time in ms (0 = skip)

Examples:
    python3 tests/stress/check_results.py nightly_stats.csv 0.01
    python3 tests/stress/check_results.py nightly_stats.csv 0.01 1500 2500
    python3 tests/stress/check_results.py manual_stats.csv  0.05 2000 3500

Exit codes:
    0 — all thresholds met
    1 — one or more thresholds exceeded, or file/format error
"""

import csv
import sys


def _parse_args() -> tuple[str, float, float, float]:
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <stats_csv> <error_rate> [p95_ms] [p99_ms]",
            file=sys.stderr,
        )
        sys.exit(1)

    csv_path = sys.argv[1]
    error_threshold = float(sys.argv[2])
    p95_threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    p99_threshold = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    return csv_path, error_threshold, p95_threshold, p99_threshold


def main() -> None:
    csv_path, error_threshold, p95_threshold, p99_threshold = _parse_args()

    try:
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"ERROR: Stats file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    agg = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    if not agg:
        print("ERROR: No 'Aggregated' row found in CSV.", file=sys.stderr)
        sys.exit(1)

    total = int(agg["Request Count"])
    failures = int(agg["Failure Count"])

    if total == 0:
        print("WARNING: No requests recorded — check Locust ran correctly.")
        sys.exit(1)

    error_rate = failures / total
    avg_ms = float(agg.get("Average Response Time", 0))
    p95_ms = float(agg.get("95%", 0))
    p99_ms = float(agg.get("99%", 0))
    rps = float(agg.get("Requests/s", 0))

    # ── Summary table ────────────────────────────────────────────────────────
    p95_limit = f"  (limit {p95_threshold:.0f} ms)" if p95_threshold else ""
    p99_limit = f"  (limit {p99_threshold:.0f} ms)" if p99_threshold else ""
    print(
        f"\n{'─' * 54}\n"
        f"  Requests   : {total:,}\n"
        f"  Failures   : {failures:,}\n"
        f"  Error rate : {error_rate:.2%}  (limit {error_threshold:.2%})\n"
        f"  Avg resp   : {avg_ms:.0f} ms\n"
        f"  p95 resp   : {p95_ms:.0f} ms{p95_limit}\n"
        f"  p99 resp   : {p99_ms:.0f} ms{p99_limit}\n"
        f"  RPS        : {rps:.1f}\n"
        f"{'─' * 54}"
    )

    # ── Threshold checks ─────────────────────────────────────────────────────
    failed = False

    if error_rate > error_threshold:
        print(
            f"\n❌  FAILED  error rate {error_rate:.2%} "
            f"exceeds threshold {error_threshold:.2%}"
        )
        failed = True

    if p95_threshold and p95_ms > p95_threshold:
        print(
            f"\n❌  FAILED  p95 latency {p95_ms:.0f} ms "
            f"exceeds threshold {p95_threshold:.0f} ms"
        )
        failed = True

    if p99_threshold and p99_ms > p99_threshold:
        print(
            f"\n❌  FAILED  p99 latency {p99_ms:.0f} ms "
            f"exceeds threshold {p99_threshold:.0f} ms"
        )
        failed = True

    if failed:
        sys.exit(1)

    print("\n✅  PASSED  all thresholds met")


if __name__ == "__main__":
    main()

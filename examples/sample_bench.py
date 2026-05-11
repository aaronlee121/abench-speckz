#!/usr/bin/env python3
"""Self-contained sample benchmark tool for benchspec examples.

Simulates a CPU-bound workload (hash iterations) whose latency scales with
concurrency and workload type. Reads config from environment variables set
by benchspec, outputs JSON that sample.tool.yaml extracts via JSONPath.

Usage (standalone):
    concurrency=8 workload=write python examples/sample_bench.py

Usage (via benchspec):
    benchspec gen examples/sample.yaml --out out/
    benchspec run out/ --tool examples/sample.tool.yaml --repeat 5
    benchspec stats results/
"""
import hashlib
import json
import math
import os
import random
import sys
import time

_REQUESTS = 200
_SEED = 42


def _simulate(concurrency: int, workload: str) -> list[float]:
    """Return per-request latencies (ms) for one virtual worker batch."""
    rng = random.Random(_SEED)
    base = {"read": 5.0, "write": 12.0, "mixed": 8.0}.get(workload, 5.0)
    overhead = math.log1p(concurrency) * 2.0
    latencies = []
    for _ in range(_REQUESTS):
        # burn CPU proportional to base latency to produce measurable elapsed time
        payload = os.urandom(64)
        rounds = max(1, int(base * 50))
        for _ in range(rounds):
            payload = hashlib.sha256(payload).digest()
        jitter = rng.gauss(0, base * 0.10)
        latencies.append(max(0.1, base + overhead + jitter))
    return latencies


def main() -> int:
    if "--version" in sys.argv:
        print("sample-bench 1.0.0")
        return 0

    concurrency = int(os.environ.get("concurrency", "1"))
    workload = os.environ.get("workload", "read")

    t0 = time.monotonic()
    latencies = _simulate(concurrency, workload)
    elapsed_ms = (time.monotonic() - t0) * 1000

    latencies.sort()
    n = len(latencies)
    mean = sum(latencies) / n
    rps = concurrency * 1000.0 / max(mean, 0.001)

    def pct(p: float) -> float:
        return round(latencies[int(n * p)], 2)

    print(json.dumps({
        "summary": {
            "requestsPerSec": round(rps, 2),
            "totalRequests": _REQUESTS * concurrency,
            "elapsed_ms": round(elapsed_ms, 1),
            "latencies": {
                "mean": round(mean, 2),
                "p50":  pct(0.50),
                "p95":  pct(0.95),
                "p99":  pct(0.99),
            },
        },
        "config": {"concurrency": concurrency, "workload": workload},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

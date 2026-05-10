#!/usr/bin/env python3
"""A predictable benchmark tool fixture for tests.

Modes (via --mode or env $MODE): succeed, fail, timeout, garbage.
Output values are deterministic on env vars `concurrency` and `workload`.
"""
import argparse
import json
import os
import sys
import time


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--version", action="store_true")
    p.add_argument("--mode", default=os.environ.get("MODE", "succeed"))
    p.add_argument("--multi", action="store_true",
                   help="emit a list of latencies under $.results[*].latency")
    p.add_argument("--output-file", default=None,
                   help="write JSON output to this file instead of stdout")
    a = p.parse_args()

    if a.version:
        print("fake-tool 1.2.3")
        return 0
    if a.mode == "fail":
        print("boom", file=sys.stderr)
        return 2
    if a.mode == "timeout":
        time.sleep(60)
        return 0
    if a.mode == "garbage":
        print("not json")
        return 0

    concurrency = float(os.environ.get("concurrency", "1"))
    workload = os.environ.get("workload", "read")
    base_p50 = 10.0 + (5.0 if workload == "write" else 0.0)
    payload = {
        "summary": {
            "latencies": {
                "p50": base_p50 + concurrency * 0.1,
                "p95": base_p50 * 4 + concurrency * 0.5,
            },
            "requestsPerSec": 1000.0 / max(1.0, concurrency / 8),
        },
    }
    if a.multi:
        payload["results"] = [
            {"latency": base_p50 + i} for i in range(3)
        ]
    output = json.dumps(payload)
    if a.output_file:
        with open(a.output_file, "w") as f:
            f.write(output)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

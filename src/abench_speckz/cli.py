"""Argparse entry point for ``abench-speckz gen | run | stats | rebuild-aggregates``."""

import argparse
import sys
from pathlib import Path

from abench_speckz import generate, query, runner, slug, spec, store, tail as tail_mod, validate, writers
from abench_speckz.model import Combo, RunError, SpecError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="abench-speckz")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("gen", help="generate env-file combinations from a YAML spec")
    gen.add_argument("spec", type=Path, help="path to YAML spec file")
    gen.add_argument("--out", type=Path, default=Path("out"), help="output directory (default: out)")
    gen.add_argument("--profile", type=str, default=None)
    gen.add_argument("--tag", action="append", default=[], help="only emit combos with this tag (repeatable)")
    gen.add_argument(
        "--exclude-tag", action="append", default=[], help="skip combos with this tag (repeatable)"
    )
    mode = gen.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print summary table; do not write files")
    mode.add_argument("--list", action="store_true", help="print TSV; do not write files")

    run = sub.add_parser("run", help="execute a benchmark tool per combo")
    run.add_argument("manifest", type=Path, help="manifest.json or directory containing it")
    run.add_argument("--tool", type=Path, required=True, help="tool YAML")
    run.add_argument("--repeat", type=int, default=1, help="reps per combo (default: 1)")
    run.add_argument("--warmup", type=int, default=0, help="warmup reps (excluded from aggregates)")
    run.add_argument("--results", type=Path, default=Path("results"))
    run.add_argument("--filter", action="append", default=[], help="K=V combo filter (AND)")
    run.add_argument("--filter-tag", action="append", default=[])
    run.add_argument("--filter-exclude-tag", action="append", default=[])
    run.add_argument("--keep-raw", action="store_true")
    run.add_argument("--skip-existing", action="store_true")
    run.add_argument("--dry-run", action="store_true")

    stats = sub.add_parser("stats", help="aggregate stats over a results dir")
    stats.add_argument("results_dir", type=Path)
    stats.add_argument("--group-by", action="append", default=[])
    stats.add_argument("--where", action="append", default=[])
    stats.add_argument("--filter-tag", action="append", default=[])
    stats.add_argument("--filter-exclude-tag", action="append", default=[])
    stats.add_argument("--metric", action="append", default=[])
    stats.add_argument("--format", choices=["table", "json", "tsv"], default="table")
    stats.add_argument("--from-raw", action="store_true")
    stats.add_argument("--pretty", action="store_true")
    stats.add_argument(
        "--report", type=Path, default=None, metavar="PATH", help="write an HTML report with charts to PATH"
    )
    stats.add_argument(
        "--plots",
        type=Path,
        default=None,
        metavar="PATH",
        help="YAML file with plot definitions (overrides tool YAML plots)",
    )
    rpt_mode = stats.add_mutually_exclusive_group()
    rpt_mode.add_argument(
        "--link-run-reports",
        action="store_true",
        default=False,
        help="Link to per-run HTML files instead of embedding them inline",
    )
    rpt_mode.add_argument(
        "--no-run-reports",
        action="store_true",
        default=False,
        help="Omit per-run reports section; generate summary report only",
    )

    rebuild = sub.add_parser("rebuild-aggregates", help="regenerate aggregates.jsonl from runs.jsonl")
    rebuild.add_argument("results_dir", type=Path)

    tail_p = sub.add_parser("tail", help="live-follow a running sweep: print each run as it completes")
    tail_p.add_argument("results_dir", type=Path, help="results directory containing runs.jsonl")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "gen":
            return _gen(args)
        if args.cmd == "run":
            return _run(args)
        if args.cmd == "stats":
            return _stats(args)
        if args.cmd == "rebuild-aggregates":
            return _rebuild(args)
        if args.cmd == "tail":
            return _tail(args)
    except (SpecError, RunError) as e:
        print(f"abench-speckz: error: {e}", file=sys.stderr)
        return 2
    return 1


def _gen(args: argparse.Namespace) -> int:
    parsed = spec.load(args.spec, profile=args.profile)
    validate.validate(parsed)

    combos = generate.run_pipeline(parsed, require_tags=args.tag, exclude_tags=args.exclude_tag)

    if args.dry_run:
        _print_table(combos)
        return 0
    if args.list:
        _print_tsv(combos)
        return 0

    if not combos:
        print("abench-speckz: 0 combos after filters; nothing to write", file=sys.stderr)
        return 0

    validate.validate_env_keys(combos)
    filenames = slug.assign_filenames(combos)
    writers.write_all(args.out, combos, filenames)
    print(f"wrote {len(combos)} combos to {args.out}/")
    return 0


def _run(args: argparse.Namespace) -> int:
    try:
        summary = runner.execute_sweep(
            manifest_path=args.manifest,
            tool_path=args.tool,
            results_dir=args.results,
            repeat=args.repeat,
            warmup=args.warmup,
            where=args.filter,
            require_tags=args.filter_tag,
            exclude_tags=args.filter_exclude_tag,
            keep_raw=args.keep_raw,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        print("\nabench-speckz: interrupted", file=sys.stderr)
        return 130
    if args.dry_run:
        return 0
    print(
        f"runs={summary['runs']} succeeded={summary['succeeded']} "
        f"failed={summary['failed']} skipped={summary['skipped']}"
    )
    if summary["runs"] > 0 and summary["succeeded"] == 0:
        return 3
    return 0


def _stats(args: argparse.Namespace) -> int:
    if args.no_run_reports:
        run_reports_mode = "none"
    elif args.link_run_reports:
        run_reports_mode = "linked"
    else:
        run_reports_mode = "embedded"
    query.stats(
        args.results_dir,
        group_by=args.group_by,
        where=args.where,
        require_tags=args.filter_tag,
        exclude_tags=args.filter_exclude_tag,
        metrics=args.metric,
        from_raw=args.from_raw,
        fmt=args.format,
        pretty=args.pretty,
        report=args.report,
        plots=args.plots,
        run_reports_mode=run_reports_mode,
    )
    return 0


def _rebuild(args: argparse.Namespace) -> int:
    n = store.rebuild_aggregates(args.results_dir)
    print(f"wrote {n} aggregate rows to {args.results_dir}/aggregates.jsonl")
    return 0


def _tail(args: argparse.Namespace) -> int:
    try:
        tail_mod.tail(args.results_dir)
    except KeyboardInterrupt:
        print()
    return 0


def _print_table(combos: list[Combo]) -> None:
    print(f"# {len(combos)} combo(s)")
    if not combos:
        return
    headers = list(combos[0].varying_keys) + ["tags"]
    rows = [
        [str(c.vars.get(k, "")) for k in combos[0].varying_keys] + [",".join(sorted(c.tags))] for c in combos
    ]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


def _print_tsv(combos: list[Combo]) -> None:
    if not combos:
        return
    headers = list(combos[0].varying_keys) + ["tags"]
    print("\t".join(headers))
    for c in combos:
        row = [str(c.vars.get(k, "")) for k in combos[0].varying_keys] + [",".join(sorted(c.tags))]
        print("\t".join(row))

"""Textual TUI for browsing a results directory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _ComboSummary:
    config_hash: str
    vars: dict[str, str]
    metrics: list[dict[str, Any]] = field(default_factory=list)
    n_runs: int = 0
    n_failed: int = 0


def _load_combos(results_dir: Path) -> tuple[list[_ComboSummary], list[str]]:
    path = results_dir / "aggregates.jsonl"
    if not path.exists():
        return [], []

    combos: dict[str, _ComboSummary] = {}
    var_keys: list[str] = []

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        h = row.get("config_hash", "")
        if h not in combos:
            vars_ = row.get("vars") or {}
            if not var_keys and vars_:
                var_keys = list(vars_.keys())
            combos[h] = _ComboSummary(
                config_hash=h,
                vars=vars_,
                n_runs=row.get("n", 0),
                n_failed=row.get("n_failed", 0),
            )
        else:
            combos[h].n_runs = max(combos[h].n_runs, row.get("n", 0))
            combos[h].n_failed = max(combos[h].n_failed, row.get("n_failed", 0))

        combos[h].metrics.append(
            {
                "metric": row.get("metric", ""),
                "n": row.get("n", 0),
                "mean": row.get("mean"),
                "p50": row.get("p50"),
                "p95": row.get("p95"),
                "p99": row.get("p99"),
                "stddev": row.get("stddev"),
            }
        )

    # Prefer the manifest snapshot's original key order
    snapshot = results_dir / "manifest.snapshot.json"
    if snapshot.exists():
        try:
            data = json.loads(snapshot.read_text())
            if isinstance(data, list) and data:
                keys = data[0].get("varying_keys")
                if isinstance(keys, list):
                    var_keys = keys
        except (json.JSONDecodeError, OSError):
            pass

    return list(combos.values()), var_keys


def _load_runs_for_hash(results_dir: Path, config_hash: str) -> list[dict[str, Any]]:
    path = results_dir / "runs.jsonl"
    if not path.exists():
        return []
    runs = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("config_hash") == config_hash:
            if "warmup" not in (row.get("tags") or []):
                runs.append(row)
    return runs


def _fmt(v: Any, precision: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{float(v):.{precision}f}"
    return str(v)


def browse(results_dir: Path) -> None:
    """Launch the TUI browser for a results directory."""
    try:
        from rich.text import Text
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical
        from textual.reactive import reactive
        from textual.widgets import DataTable, Footer, Header, Label, Tree
        from textual.widgets._tree import TreeNode
    except ImportError as exc:
        raise SystemExit(
            "textual is required for 'abench-speckz browse'.\nInstall it: pip install 'abench-speckz[tui]'"
        ) from exc

    def _build_tree_nodes(
        parent: TreeNode,
        combos: list[_ComboSummary],
        var_keys: list[str],
        depth: int,
    ) -> None:
        if depth >= len(var_keys):
            return
        key = var_keys[depth]
        is_last = depth == len(var_keys) - 1

        groups: dict[str, list[_ComboSummary]] = {}
        for combo in combos:
            val = combo.vars.get(key, "")
            groups.setdefault(val, []).append(combo)

        for val, sub_combos in groups.items():
            n_fail = sum(c.n_failed for c in sub_combos)
            if is_last:
                combo = sub_combos[0]
                label = Text()
                label.append(f"{key}=")
                label.append(val, style="bold")
                label.append(f"  n={combo.n_runs}", style="dim")
                if combo.n_failed:
                    label.append(f"  ✗{combo.n_failed}", style="bold red")
                parent.add_leaf(label, data=combo.config_hash)
            else:
                label = Text()
                label.append(f"{key}=")
                label.append(val, style="bold")
                label.append(f"  ({len(sub_combos)})", style="dim")
                if n_fail:
                    label.append(f"  ✗{n_fail}", style="bold red")
                child = parent.add(label, expand=True)
                _build_tree_nodes(child, sub_combos, var_keys, depth + 1)

    _pane_ids = ["combo-tree", "metric-table", "run-table"]

    class BrowserApp(App):
        CSS = """
        Screen { layout: vertical; }

        #panes {
            layout: horizontal;
            height: 1fr;
        }

        .pane {
            height: 1fr;
            border: solid $primary-darken-2;
        }

        #left-pane   { width: 1fr; }
        #middle-pane { width: 2fr; }
        #right-pane  { width: 2fr; }

        .pane-title {
            background: $primary-darken-2;
            color: $text;
            padding: 0 1;
            height: 1;
            text-style: bold;
        }

        DataTable { height: 1fr; }
        Tree { height: 1fr; }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "reload", "Reload"),
            Binding("R", "rebuild", "Rebuild aggs"),
            Binding("tab", "next_pane", "→ pane"),
            Binding("shift+tab", "prev_pane", "← pane", show=False),
            Binding("1", "focus_combo", "Combos", show=False),
            Binding("2", "focus_metrics", "Metrics", show=False),
            Binding("3", "focus_runs", "Runs", show=False),
        ]

        active_pane: reactive[int] = reactive(0)
        selected_hash: reactive[str | None] = reactive(None)

        def __init__(self) -> None:
            super().__init__()
            self.title = f"abench-speckz browse — {results_dir}"
            self.combos: list[_ComboSummary] = []
            self.var_keys: list[str] = []

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="panes"):
                with Vertical(id="left-pane", classes="pane"):
                    yield Label("Combos", classes="pane-title", id="left-title")
                    yield Tree("", id="combo-tree")
                with Vertical(id="middle-pane", classes="pane"):
                    yield Label("Metrics", classes="pane-title", id="middle-title")
                    yield DataTable(id="metric-table", cursor_type="row")
                with Vertical(id="right-pane", classes="pane"):
                    yield Label("Runs", classes="pane-title", id="right-title")
                    yield DataTable(id="run-table", cursor_type="row")
            yield Footer()

        def on_mount(self) -> None:
            self._load_and_populate()
            self.query_one("#combo-tree").focus()

        # ------------------------------------------------------------------
        # Data loading
        # ------------------------------------------------------------------

        def _load_and_populate(self) -> None:
            self.combos, self.var_keys = _load_combos(results_dir)
            self._populate_combo_tree()
            known = {c.config_hash for c in self.combos}
            if self.combos and self.selected_hash not in known:
                self.selected_hash = self.combos[0].config_hash
            self._populate_metrics(self.selected_hash)
            self._populate_runs(self.selected_hash)

        def _populate_combo_tree(self) -> None:
            tree = self.query_one("#combo-tree", Tree)
            tree.clear()
            n = len(self.combos)
            tree.root.set_label(f"Combos ({n})")
            self.query_one("#left-title", Label).update(f"Combos ({n})")

            if not self.combos:
                tree.root.add_leaf("(no data)")
                tree.root.expand()
                return

            if not self.var_keys:
                for combo in self.combos:
                    tree.root.add_leaf(combo.config_hash[:12], data=combo.config_hash)
            else:
                _build_tree_nodes(tree.root, self.combos, self.var_keys, 0)
            tree.root.expand()

        def _populate_metrics(self, config_hash: str | None) -> None:
            table = self.query_one("#metric-table", DataTable)
            table.clear(columns=True)
            for col in ("metric", "n", "mean", "p50", "p95", "p99", "stddev"):
                table.add_column(col, key=col)

            combo = next((c for c in self.combos if c.config_hash == config_hash), None)
            if combo:
                vars_str = " ".join(f"{k}={v}" for k, v in combo.vars.items())
                self.query_one("#middle-title", Label).update(f"Metrics — {vars_str}")
            else:
                self.query_one("#middle-title", Label).update("Metrics")
                return

            for m in sorted(combo.metrics, key=lambda x: x["metric"]):
                table.add_row(
                    m["metric"],
                    str(m["n"]),
                    _fmt(m["mean"]),
                    _fmt(m["p50"]),
                    _fmt(m["p95"]),
                    _fmt(m["p99"]),
                    _fmt(m["stddev"]),
                )

        def _populate_runs(self, config_hash: str | None) -> None:
            table = self.query_one("#run-table", DataTable)
            table.clear(columns=True)

            if not config_hash:
                self.query_one("#right-title", Label).update("Runs")
                table.add_column("run_id")
                return

            runs = _load_runs_for_hash(results_dir, config_hash)
            self.query_one("#right-title", Label).update(f"Runs ({len(runs)})")

            metric_names: list[str] = []
            for run in runs:
                for k in run.get("results") or {}:
                    if k not in metric_names:
                        metric_names.append(k)

            table.add_column("run_id", key="run_id")
            table.add_column("exit", key="exit")
            table.add_column("ms", key="ms")
            table.add_column("time", key="time")
            for m in metric_names:
                table.add_column(m, key=m)

            for run in runs:
                run_id = (run.get("run_id") or "")[:8]
                exit_code = run.get("exit_code", "?")
                duration = run.get("duration_ms")
                started = run.get("started_at") or ""
                if "T" in started:
                    started = started.split("T")[1][:8]

                exit_cell = Text(str(exit_code))
                if exit_code != 0:
                    exit_cell.stylize("bold red")

                row: list[Any] = [run_id, exit_cell, _fmt(duration, 0), started]
                results = run.get("results") or {}
                row += [_fmt(results.get(m)) for m in metric_names]

                table.add_row(*row, key=run.get("run_id", run_id))

        # ------------------------------------------------------------------
        # Events
        # ------------------------------------------------------------------

        def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
            if event.node.data is not None:
                self.selected_hash = str(event.node.data)

        def watch_selected_hash(self, h: str | None) -> None:
            self._populate_metrics(h)
            self._populate_runs(h)

        # ------------------------------------------------------------------
        # Actions
        # ------------------------------------------------------------------

        def action_next_pane(self) -> None:
            self.active_pane = (self.active_pane + 1) % len(_pane_ids)
            self.query_one(f"#{_pane_ids[self.active_pane]}").focus()

        def action_prev_pane(self) -> None:
            self.active_pane = (self.active_pane - 1) % len(_pane_ids)
            self.query_one(f"#{_pane_ids[self.active_pane]}").focus()

        def action_focus_combo(self) -> None:
            self.active_pane = 0
            self.query_one("#combo-tree").focus()

        def action_focus_metrics(self) -> None:
            self.active_pane = 1
            self.query_one("#metric-table").focus()

        def action_focus_runs(self) -> None:
            self.active_pane = 2
            self.query_one("#run-table").focus()

        def action_reload(self) -> None:
            self._load_and_populate()
            self.notify("Reloaded")

        def action_rebuild(self) -> None:
            from abench_speckz import store

            n = store.rebuild_aggregates(results_dir)
            self._load_and_populate()
            self.notify(f"Rebuilt {n} aggregate rows")

    BrowserApp().run()
    print("\033[2J\033[H", end="", flush=True)

"""Textual ``BrowserApp`` for :func:`abench_speckz.tui.browse`.

Imported lazily so the rest of ``abench-speckz`` works without the
``[tui]`` extra installed; module load forces a ``textual`` import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, Tree
from textual.widgets._tree import TreeNode

from abench_speckz.tui import _ComboSummary, _fmt, _load_combos, _load_runs_for_hash

_PANE_IDS = ["combo-tree", "metric-table", "run-table"]


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

    def __init__(self, results_dir: Path) -> None:
        super().__init__()
        self.results_dir = results_dir
        self.title = f"abench-speckz browse — {results_dir}"
        self.combos: list[_ComboSummary] = []
        self.var_keys: list[str] = []
        self._tree_w: int = 20
        self._metric_w: int = 40
        self._run_w: int = 40

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
                yield DataTable(id="run-table", cursor_type="column")
        yield Footer()

    def on_mount(self) -> None:
        self._load_and_populate()
        self.query_one("#combo-tree").focus()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_and_populate(self) -> None:
        self.combos, self.var_keys = _load_combos(self.results_dir)
        self._populate_combo_tree()
        known = {c.config_hash for c in self.combos}
        if self.combos and self.selected_hash not in known:
            self.selected_hash = self.combos[0].config_hash
        self._populate_metrics(self.selected_hash)
        self._populate_runs(self.selected_hash)
        self._adjust_panel_widths()

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
            self._tree_w = 20
        else:
            _build_tree_nodes(tree.root, self.combos, self.var_keys, 0)
            # Leaf labels are the deepest key=val; estimate their rendered width.
            # Each tree level adds ~2 chars of indentation connector.
            last_key = self.var_keys[-1]
            max_val = max(
                (len(str(c.vars.get(last_key, ""))) for c in self.combos), default=4
            )
            leaf_label = len(last_key) + 1 + max_val + 12  # "  n=NNN  ✗N"
            indent = len(self.var_keys) * 2
            self._tree_w = max(20, indent + leaf_label + 4)
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

        max_metric_len = max(
            (len(m["metric"]) for m in combo.metrics), default=6
        )
        # metric col + 6 numeric cols (~7 chars each) + column separators
        self._metric_w = max(40, max_metric_len + 6 * 8 + 6)

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
            table.add_column("field")
            return

        runs = _load_runs_for_hash(self.results_dir, config_hash)
        self.query_one("#right-title", Label).update(f"Runs ({len(runs)})")

        metric_names: list[str] = []
        for run in runs:
            for k in run.get("results") or {}:
                if k not in metric_names:
                    metric_names.append(k)

        # Transposed layout: rows = fields, columns = runs
        field_names = ["run_id", "exit", "ms", "time"] + metric_names

        max_field_len = max((len(f) for f in field_names), default=6)
        # field col + each run col (~10 chars for values) + separators
        self._run_w = max(20, max_field_len + 2 + len(runs) * 12)

        table.add_column("field", key="field")
        for run in runs:
            col_id = run.get("run_id") or ""
            header = col_id[:8]
            table.add_column(header, key=col_id)

        for field in field_names:
            row: list[Any] = [field]
            for run in runs:
                run_id_full = run.get("run_id") or ""
                exit_code = run.get("exit_code", "?")
                duration = run.get("duration_ms")
                started = run.get("started_at") or ""
                if "T" in started:
                    started = started.split("T")[1][:8]
                results = run.get("results") or {}

                if field == "run_id":
                    cell: Any = run_id_full[:8]
                elif field == "exit":
                    cell = Text(str(exit_code))
                    if exit_code != 0:
                        cell.stylize("bold red")
                elif field == "ms":
                    cell = _fmt(duration, 0)
                elif field == "time":
                    cell = started
                else:
                    cell = _fmt(results.get(field))
                row.append(cell)

            table.add_row(*row, key=field)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data is not None:
            self.selected_hash = str(event.node.data)

    def _adjust_panel_widths(self) -> None:
        try:
            self.query_one("#left-pane").styles.width = f"{self._tree_w}fr"
            self.query_one("#middle-pane").styles.width = f"{self._metric_w}fr"
            self.query_one("#right-pane").styles.width = f"{self._run_w}fr"
        except Exception:
            pass

    def watch_selected_hash(self, h: str | None) -> None:
        self._populate_metrics(h)
        self._populate_runs(h)
        self._adjust_panel_widths()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_next_pane(self) -> None:
        self.active_pane = (self.active_pane + 1) % len(_PANE_IDS)
        self.query_one(f"#{_PANE_IDS[self.active_pane]}").focus()

    def action_prev_pane(self) -> None:
        self.active_pane = (self.active_pane - 1) % len(_PANE_IDS)
        self.query_one(f"#{_PANE_IDS[self.active_pane]}").focus()

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

        n = store.rebuild_aggregates(self.results_dir)
        self._load_and_populate()
        self.notify(f"Rebuilt {n} aggregate rows")

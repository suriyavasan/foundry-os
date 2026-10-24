"""Output helpers: every command prints either JSON (``--json``) or a Rich table/summary."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

Column = tuple[str, str | Callable[[dict], Any]]


def console(stderr: bool = False) -> Console:
    # Built on demand so it follows the current sys.stdout/sys.stderr (tests swap them).
    return Console(stderr=stderr, highlight=False, soft_wrap=False)


def to_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def print_json(data: Any) -> None:
    sys.stdout.write(to_json(data) + "\n")
    sys.stdout.flush()


def usd(micros: int | None) -> str:
    if micros is None:
        return "-"
    return (
        f"${micros / 1_000_000:,.4f}" if abs(micros) < 1_000_000 else f"${micros / 1_000_000:,.2f}"
    )


def short(value: Any, width: int = 60) -> str:
    text = "" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def _value(row: dict, getter: str | Callable[[dict], Any]) -> Any:
    if callable(getter):
        return getter(row)
    cur: Any = row
    for part in getter.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def cell(value: Any) -> Text:
    if value is None:
        return Text("-", style="dim")
    if isinstance(value, bool):
        return Text("yes" if value else "no")
    if isinstance(value, (list, tuple)):
        return Text(", ".join(str(v) for v in value) or "-")
    return Text(str(value))


def render_table(
    items: Sequence[dict], columns: Sequence[Column], *, title: str | None = None, empty: str
) -> None:
    c = console()
    if not items:
        c.print(Text(empty, style="dim"))
        return
    table = Table(title=title, title_justify="left", header_style="bold", show_lines=False)
    for header, _ in columns:
        table.add_column(header, overflow="fold")
    for row in items:
        table.add_row(*[cell(_value(row, getter)) for _, getter in columns])
    c.print(table)


def render_kv(data: dict, fields: Sequence[Column], *, title: str | None = None) -> None:
    c = console()
    if title:
        c.print(Text(title, style="bold"))
    width = max((len(label) for label, _ in fields), default=0)
    for label, getter in fields:
        line = Text(f"  {label.ljust(width)}  ", style="dim")
        line.append_text(cell(_value(data, getter)))
        c.print(line)


def say(message: str, *, style: str | None = None, err: bool = False) -> None:
    console(err).print(Text(message, style=style or ""))

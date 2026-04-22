from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

import ui_styles as ui
from app_types import LabelGroup, TodoistSnapshot


def make_due(date_text: str = "2026-04-03", string: str | None = None):
    return SimpleNamespace(date=date_text, string=string or date_text)


def make_task(
    task_id: str,
    content: str,
    *,
    description: str = "",
    labels: list[str] | None = None,
    due=None,
    priority: int = 1,
    order: int = 1,
):
    return SimpleNamespace(
        id=task_id,
        content=content,
        description=description,
        labels=labels or [],
        priority=priority,
        due=due,
        order=order,
    )


def make_label(
    label_id: str,
    name: str,
    *,
    color: str = "charcoal",
    is_favorite: bool = False,
    order: int = 1,
):
    return SimpleNamespace(
        id=label_id,
        name=name,
        color=color,
        is_favorite=is_favorite,
        order=order,
    )


def make_snapshot(
    *,
    tasks,
    labels,
    inbox_id: str = "inbox",
    inbox_name: str = "Inbox",
) -> TodoistSnapshot:
    return TodoistSnapshot(
        inbox=SimpleNamespace(id=inbox_id, name=inbox_name),
        tasks=list(tasks),
        labels=list(labels),
    )


def make_group(
    key: str,
    title: str,
    *,
    tasks=None,
    accent: str = ui.ACCENT_PRIMARY,
    help_text: str = "Group help",
) -> LabelGroup:
    return LabelGroup(
        key=key,
        title=title,
        accent=accent,
        tasks=list(tasks or []),
        help_text=help_text,
    )


def render_text(renderable, *, width: int = 120) -> str:
    console = Console(
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=False,
        color_system=None,
    )
    console.print(renderable)
    return console.export_text()

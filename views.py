from __future__ import annotations

import calendar
from datetime import date, datetime

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from todoist_api_python.models import Label as TodoistLabel
from todoist_api_python.models import Task

import ui_styles as ui
from app_types import LabelGroup
from app_utils import compact_multiline_text, compact_text, humanize_color_name, task_window


def group_label(group: LabelGroup) -> str:
    if group.key == "all":
        return f"{group.title} [{len(group.tasks)}]"
    if group.key == "unlabeled":
        return f"{group.title} [{len(group.tasks)}]"
    return f"@{group.title} [{len(group.tasks)}]"


def build_workspace_header(inbox_name: str, task_count: int, label_count: int, *, busy: bool) -> RenderableType:
    header = Table.grid(expand=True)
    header.add_column(ratio=1)
    header.add_column(no_wrap=True, justify="center")
    header.add_column(no_wrap=True, justify="right")
    header.add_row(
        Text("TODOIST KANBAN", style=f"bold {ui.TEXT_PRIMARY}"),
        Text(f"{inbox_name.upper()} ", style=ui.ACCENT_SECONDARY),
        Text(
            "SYNCING" if busy else f"{task_count} TASKS  {label_count} LABELS",
            style=ui.ACCENT_SOFT if busy else ui.TEXT_MUTED,
        ),
    )
    return header


def build_calendar_widget(task: Task | None) -> RenderableType:
    today = date.today()
    display_date = today
    due_value = getattr(getattr(task, "due", None), "date", None)

    if isinstance(due_value, str):
        try:
            display_date = datetime.strptime(due_value, "%Y-%m-%d").date()
        except ValueError:
            display_date = today

    month = calendar.monthcalendar(display_date.year, display_date.month)
    month_name = display_date.strftime("%B %Y").upper()

    days = Table.grid(expand=True)
    for _ in range(7):
        days.add_column(justify="center", ratio=1)

    days.add_row(*[Text(label, style=ui.TEXT_SUBTLE) for label in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")])

    selected_day = display_date.day if due_value else None
    for week in month:
        cells: list[Text] = []
        for day in week:
            if day == 0:
                cells.append(Text(" "))
                continue

            style = ui.TEXT_DEFAULT
            if display_date.year == today.year and display_date.month == today.month and day == today.day:
                style = f"bold {ui.ACCENT_SOFT}"
            if selected_day is not None and day == selected_day:
                style = f"bold {ui.ACTIVE_TASK_BORDER}"
            cells.append(Text(f"{day:>2}", style=style))
        days.add_row(*cells)

    return Panel(days, title=month_name, border_style=ui.ACCENT_BORDER_BLURRED)


def build_label_manager_rows(labels: list[TodoistLabel], selected_index: int) -> RenderableType:
    rows: list[RenderableType] = []
    if not labels:
        rows.append(Text("No labels yet. Press a to create one.", style=ui.TEXT_DEFAULT))
        return Group(*rows)

    for index, label in enumerate(labels):
        accent = ui.COLOR_HEX_BY_NAME.get(label.color, ui.ACCENT_PRIMARY)
        favorite = "favorite" if label.is_favorite else "standard"
        body = Table.grid(expand=True)
        body.add_column(ratio=1)
        body.add_column(no_wrap=True, justify="right")
        body.add_row(
            Text(label.name, style=f"bold {ui.TEXT_PRIMARY}"),
            Text(humanize_color_name(label.color), style=accent),
        )
        body.add_row(
            Text(favorite, style=ui.TEXT_MUTED),
            Text(label.id, style=ui.ACCENT_BORDER),
        )
        rows.append(
            Panel(
                body,
                border_style=accent if index == selected_index else ui.ACCENT_BORDER,
                title="selected" if index == selected_index else "",
            )
        )
    return Group(*rows)


def build_task_panel(
    group: LabelGroup,
    task_index: int,
    height: int,
    render_task_card,
    *,
    title_style: str,
    muted_style: str,
    border_style: str,
) -> RenderableType:
    tasks = group.tasks
    renderables: list[RenderableType] = []

    if not tasks:
        renderables.extend(
            [
                Text("No tasks in this group.", style=title_style),
                Text("Press n to add a new Inbox task or l/h to browse another label group.", style=muted_style),
            ]
        )
        return Group(*renderables)

    visible_cards = max(2, (max(height, 18) - 16) // 5)
    start, end = task_window(len(tasks), task_index, visible_cards)

    if start:
        renderables.append(Text(f"{start} earlier task{'s' if start != 1 else ''} above", style=border_style))
        renderables.append(Text(""))

    for index in range(start, end):
        renderables.append(
            render_task_card(tasks[index], selected=index == task_index, accent=group.accent)
        )

    if end < len(tasks):
        renderables.extend(
            [
                Text(""),
                Text(f"{len(tasks) - end} more task{'s' if len(tasks) - end != 1 else ''} below", style=border_style),
            ]
        )

    return Group(*renderables)


def build_task_card(
    task: Task,
    *,
    selected: bool,
    accent: str,
    label_name_colors: dict[str, str],
    selected_text_style: str,
    body_text_style: str,
    subtle_text_style: str,
    border_style: str,
) -> RenderableType:
    meta = Text(justify="right")
    if task.due:
        meta.append(f"due {task.due.string}", style=ui.ACCENT_SOFT)
    if task.labels:
        if meta:
            meta.append("  ", style=border_style)
        visible_labels = task.labels[:3]
        for index, label in enumerate(visible_labels):
            if index:
                meta.append(" ", style=border_style)
            meta.append(f"@{label}", style=label_name_colors.get(label.casefold(), ui.TEXT_MUTED))
        if len(task.labels) > 3:
            meta.append(f" +{len(task.labels) - 3}", style=border_style)
    if not meta:
        meta = Text("No extra metadata", style=border_style)

    summary = Table.grid(expand=True)
    summary.add_column(ratio=1)
    summary.add_column(no_wrap=True, justify="right")
    summary.add_row(
        Text(task.content, style=selected_text_style if selected else body_text_style),
        Text(task.id, style=border_style),
    )
    if task.description:
        summary.add_row(
            Text(compact_multiline_text(task.description), style=subtle_text_style),
            meta,
        )
    else:
        summary.add_row(Text(""), meta)

    body_items: list[RenderableType] = [summary]

    return Panel(
        Group(*body_items),
        border_style=ui.ACTIVE_TASK_BORDER if selected else ui.INACTIVE_TASK_BORDER,
        title="TASK",
    )


def build_detail_panel(
    task: Task | None,
    group: LabelGroup,
    *,
    title_style: str,
    muted_style: str,
    border_style: str,
) -> RenderableType:
    if task is None:
        return Group(
            Text("No task selected.", style=title_style),
            Text("Pick a label group with h/l and add a task with n.", style=muted_style),
        )

    meta = Table.grid(padding=(0, 1))
    meta.add_column(style=border_style, no_wrap=True)
    meta.add_column(ratio=1)
    meta.add_row("id", task.id)
    meta.add_row("group", group.title)
    meta.add_row("due", task.due.string if task.due else "none")
    meta.add_row("labels", ", ".join(task.labels or []) or "none")
    meta.add_row("description", task.description or "none")

    return Group(
        Text("INSPECTOR", style=ui.ACCENT_SECONDARY),
        Text(task.content, style=title_style),
        Text(""),
        meta,
        Text(""),
        Text(
            "Edit with e or Enter. Complete with Space. Delete with x.",
            style=muted_style,
        ),
    )


def build_status_bar(status: str, *, busy: bool, body_text_style: str) -> RenderableType:
    style = f"bold {body_text_style}" if not busy else f"bold {ui.ACCENT_SECONDARY}"
    return Text(status, style=style)

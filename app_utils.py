from __future__ import annotations

from collections import defaultdict
from typing import Iterable, TypeVar

import httpx
from todoist_api_python.models import Label as TodoistLabel
from todoist_api_python.models import Task

import ui_styles as ui
from app_types import LabelGroup


T = TypeVar("T")


def flatten_pages(pages: Iterable[list[T]]) -> list[T]:
    return [item for page in pages for item in page]


def parse_label_names(raw: str) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        label_name = chunk.strip()
        normalized = label_name.casefold()
        if not label_name or normalized in seen:
            continue
        seen.add(normalized)
        labels.append(label_name)
    return labels


def humanize_color_name(name: str) -> str:
    return name.replace("_", " ").title()


def compact_text(value: str, limit: int = 70) -> str:
    single_line = " ".join(value.split())
    if len(single_line) <= limit:
        return single_line
    return f"{single_line[: limit - 1].rstrip()}..."


def compact_multiline_text(value: str, *, line_limit: int = 72, max_lines: int = 3) -> str:
    preview_lines: list[str] = []
    raw_lines = value.splitlines() or [value]

    for raw_line in raw_lines:
        line = " ".join(raw_line.split())
        if not line:
            if preview_lines and preview_lines[-1] != "":
                preview_lines.append("")
            continue

        if len(line) <= line_limit:
            preview_lines.append(line)
        else:
            preview_lines.append(f"{line[: line_limit - 1].rstrip()}...")

        if len(preview_lines) >= max_lines:
            break

    while preview_lines and preview_lines[-1] == "":
        preview_lines.pop()

    if len(raw_lines) > len(preview_lines) and preview_lines:
        last_line = preview_lines[-1]
        preview_lines[-1] = last_line if last_line.endswith("...") else f"{last_line}..."

    return "\n".join(preview_lines)


def format_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        status = f"{error.response.status_code} {error.response.reason_phrase}"
        try:
            payload = error.response.json()
        except ValueError:
            payload = error.response.text.strip()
        detail = compact_text(str(payload), 160) if payload else ""
        return f"{status}: {detail}" if detail else status
    return compact_text(str(error), 160)


def task_window(task_count: int, selected_index: int, capacity: int) -> tuple[int, int]:
    if task_count <= 0 or capacity <= 0:
        return (0, 0)
    clamped_capacity = min(task_count, max(1, capacity))
    index = max(0, min(selected_index, task_count - 1))
    lead_items = min(max(0, clamped_capacity // 3), 2)
    start = max(0, index - lead_items)
    end = min(task_count, start + clamped_capacity)
    start = max(0, end - clamped_capacity)
    return (start, end)


def build_label_groups(tasks: list[Task], labels: list[TodoistLabel]) -> list[LabelGroup]:
    unlabeled_tasks: list[Task] = []
    tasks_by_label: defaultdict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        task_labels = task.labels or []
        if not task_labels:
            unlabeled_tasks.append(task)
            continue
        for task_label in task_labels:
            tasks_by_label[task_label.casefold()].append(task)

    groups: list[LabelGroup] = [
        LabelGroup(
            key="all",
            title="All Tasks",
            accent=ui.INACTIVE_TASK_BORDER,
            tasks=list(tasks),
            help_text="Everything in your Inbox appears here, regardless of label.",
        ),
        LabelGroup(
            key="unlabeled",
            title="No Label",
            accent=ui.INACTIVE_TASK_BORDER,
            tasks=unlabeled_tasks,
            help_text="Tasks without labels. Good for triage and cleanup.",
        ),
    ]

    for label in labels:
        groups.append(
            LabelGroup(
                key=f"label:{label.id}",
                title=label.name,
                accent=ui.COLOR_HEX_BY_NAME.get(label.color, ui.ACCENT_PRIMARY),
                tasks=list(tasks_by_label.get(label.name.casefold(), [])),
                help_text=(
                    f"Tasks tagged with @{label.name}. "
                    f"Label color: {humanize_color_name(label.color)}."
                ),
            )
        )
    return groups

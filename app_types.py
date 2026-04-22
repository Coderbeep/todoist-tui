from __future__ import annotations

from dataclasses import dataclass

from todoist_api_python.models import Label as TodoistLabel
from todoist_api_python.models import Project, Task


@dataclass(slots=True)
class TodoistSnapshot:
    inbox: Project
    tasks: list[Task]
    labels: list[TodoistLabel]


@dataclass(slots=True)
class TaskFormData:
    content: str
    description: str
    labels: list[str]
    due_string: str | None
    is_priority: bool = False


@dataclass(slots=True)
class LabelFormData:
    name: str
    color: str
    is_favorite: bool


@dataclass(slots=True)
class MutationResult:
    message: str
    task_id: str | None = None
    label_id: str | None = None
    group_key: str | None = None


@dataclass(slots=True)
class LabelGroup:
    key: str
    title: str
    accent: str
    tasks: list[Task]
    help_text: str


@dataclass(slots=True)
class LabelMutationRequest:
    action: str
    form: LabelFormData | None = None
    label_id: str | None = None


@dataclass(slots=True)
class SelectionState:
    group_index: int = 0
    task_index: int = 0
    current_group_key: str = "all"
    current_task_id: str | None = None

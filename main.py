from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import time
from collections import defaultdict
from itertools import groupby
from typing import Callable

import httpx
from rich.console import RenderableType
from textual.actions import SkipAction
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual import events
from textual.widgets import Button, Markdown, Static
from textual.widgets._footer import Footer as BaseFooter, FooterKey, FooterLabel, KeyGroup
from todoist_api_python.api import TodoistAPI
from todoist_api_python.models import Label as TodoistLabel
from todoist_api_python.models import Task

import ui_styles as ui
from app_types import LabelFormData, LabelGroup, LabelMutationRequest, MutationResult, SelectionState, TaskFormData, TodoistSnapshot
from app_utils import build_label_groups, compact_text, flatten_pages, format_error, task_window
from md_sync import (
    BIND,
    CONFLICT,
    CREATE_MARKDOWN,
    CREATE_TODOIST,
    DEFAULT_MARKDOWN_ROOT,
    DELETE_MARKDOWN,
    DELETE_TODOIST,
    SyncAction,
    SyncPlan,
    SyncPreview,
    SyncRecord,
    TaskPayload,
    TodoistTaskReplica,
    UPDATE_MARKDOWN,
    UPDATE_TODOIST,
    build_sync_preview,
    choose_markdown_note_path,
    default_sync_state_path,
    load_sync_records,
    remove_sync_record,
    read_markdown_note,
    save_sync_records,
    summarize_sync_preview,
    todoist_task_to_replica,
    upsert_sync_record,
    write_markdown_note,
    MarkdownNote,
)
from screens import ConfirmScreen, LabelManagerScreen, SyncPreviewScreen, TaskEditorScreen
from views import build_calendar_widget, build_detail_markdown, build_detail_panel, build_status_bar, build_task_card, build_task_panel, build_workspace_header, group_label


class AppFooter(BaseFooter):
    RIGHT_ACTIONS = {"refresh_data", "quit"}

    def compose(self) -> ComposeResult:
        if not self._bindings_ready:
            return

        active_bindings = self.screen.active_bindings
        bindings = [
            (binding, enabled, tooltip)
            for (_, binding, enabled, tooltip) in active_bindings.values()
            if binding.show
        ]
        action_to_bindings: defaultdict[str, list[tuple[Binding, bool, str]]] = defaultdict(list)
        for binding, enabled, tooltip in bindings:
            action_to_bindings[binding.action].append((binding, enabled, tooltip))

        self.styles.grid_size_columns = len(action_to_bindings)

        for group, multi_bindings_iterable in groupby(
            action_to_bindings.values(),
            lambda multi_bindings_: multi_bindings_[0][0].group,
        ):
            multi_bindings = list(multi_bindings_iterable)
            if group is not None and len(multi_bindings) > 1:
                with KeyGroup(classes="-compact" if group.compact else ""):
                    for grouped_bindings in multi_bindings:
                        binding, enabled, tooltip = grouped_bindings[0]
                        classes = "-grouped"
                        if binding.action in self.RIGHT_ACTIONS:
                            classes += " -command-palette"
                        yield FooterKey(
                            binding.key,
                            self.app.get_key_display(binding),
                            "",
                            binding.action,
                            disabled=not enabled,
                            tooltip=tooltip or binding.description,
                            classes=classes,
                        ).data_bind(compact=BaseFooter.compact)
                yield FooterLabel(group.description)
            else:
                for single_bindings in multi_bindings:
                    binding, enabled, tooltip = single_bindings[0]
                    classes = "-command-palette" if binding.action in self.RIGHT_ACTIONS else ""
                    yield FooterKey(
                        binding.key,
                        self.app.get_key_display(binding),
                        binding.description,
                        binding.action,
                        classes=classes,
                        disabled=not enabled,
                        tooltip=tooltip,
                    ).data_bind(compact=BaseFooter.compact)


class TaskCardWidget(Static):
    def __init__(
        self,
        task_id: str,
        renderable: RenderableType,
        *,
        classes: str | None = None,
    ) -> None:
        super().__init__(renderable, classes=classes)
        self.task_id = task_id

    def on_mouse_down(self, event: events.MouseDown) -> None:
        app = self.app
        if not isinstance(app, TodoistKanbanApp):
            return
        app.select_task_by_click(self.task_id)


class GroupChipButton(Button):
    def __init__(self, group_key: str, label, *, classes: str | None = None) -> None:
        super().__init__(label, classes=classes)
        self.group_key = group_key

    def on_mouse_down(self, event: events.MouseDown) -> None:
        app = self.app
        if not isinstance(app, TodoistKanbanApp):
            return
        app.select_group_by_click(self.group_key)


class ActivePaneScroll(VerticalScroll):
    def __init__(self, pane_name: str, *children, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self.pane_name = pane_name

    def on_mouse_down(self, event: events.MouseDown) -> None:
        app = self.app
        if not isinstance(app, TodoistKanbanApp):
            return
        app.activate_pane_from_mouse(self.pane_name)


class TodoistGateway:
    def __init__(self, token: str, due_lang: str = "en") -> None:
        self.due_lang = due_lang
        self.client = httpx.Client()
        self.api = TodoistAPI(token, client=self.client)
        self._inbox_project = None

    def close(self) -> None:
        self.client.close()

    def load_snapshot(self) -> TodoistSnapshot:
        inbox = self._inbox_project
        if inbox is None:
            projects = flatten_pages(self.api.get_projects(limit=200))
            inbox = next((project for project in projects if project.is_inbox_project), None)
            self._inbox_project = inbox
        if inbox is None:
            raise RuntimeError("Todoist Inbox project could not be found.")

        tasks = flatten_pages(self.api.get_tasks(project_id=inbox.id, limit=200))
        tasks.sort(key=lambda task: (task.order, task.content.casefold()))

        labels = flatten_pages(self.api.get_labels(limit=200))
        labels.sort(key=lambda label: (label.order, label.name.casefold()))

        return TodoistSnapshot(inbox=inbox, tasks=tasks, labels=labels)

    def create_task(self, inbox_project_id: str, form: TaskFormData) -> Task:
        return self.api.add_task(
            project_id=inbox_project_id,
            **self._task_kwargs(form),
        )

    def update_task(self, task: Task, form: TaskFormData) -> Task:
        payload = self._task_kwargs(form)
        if not form.due_string and task.due is not None:
            payload["due_string"] = "no due date"
            payload["due_lang"] = self.due_lang
        return self.api.update_task(task.id, **payload)

    def update_task_by_id(self, task_id: str, payload: TaskPayload) -> Task:
        kwargs = self._task_payload_kwargs(payload)
        if payload.due is None:
            kwargs["due_string"] = "no due date"
            kwargs["due_lang"] = self.due_lang
        return self.api.update_task(task_id, **kwargs)

    def create_task_from_payload(self, inbox_project_id: str, payload: TaskPayload) -> Task:
        return self.api.add_task(
            project_id=inbox_project_id,
            **self._task_payload_kwargs(payload),
        )

    def complete_task(self, task_id: str) -> None:
        if not self.api.complete_task(task_id):
            raise RuntimeError("Todoist did not confirm the task completion request.")

    def delete_task(self, task_id: str) -> None:
        if not self.api.delete_task(task_id):
            raise RuntimeError("Todoist did not confirm the task deletion request.")

    def create_label(self, form: LabelFormData) -> TodoistLabel:
        return self.api.add_label(
            name=form.name,
            color=form.color,
            is_favorite=form.is_favorite,
        )

    def update_label(self, label_id: str, form: LabelFormData) -> TodoistLabel:
        return self.api.update_label(
            label_id,
            name=form.name,
            color=form.color,
            is_favorite=form.is_favorite,
        )

    def delete_label(self, label_id: str) -> None:
        if not self.api.delete_label(label_id):
            raise RuntimeError("Todoist did not confirm the label deletion request.")

    def _task_kwargs(self, form: TaskFormData) -> dict[str, object]:
        return self._task_payload_kwargs(
            TaskPayload(
                title=form.content,
                body=form.description,
                labels=tuple(form.labels),
                due=form.due_string,
            )
        )

    def _task_payload_kwargs(self, payload: TaskPayload) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "content": payload.title,
            "description": payload.body,
            "labels": list(payload.labels),
        }
        if payload.due:
            kwargs["due_string"] = payload.due
            kwargs["due_lang"] = self.due_lang
        return kwargs


class TodoistKanbanApp(App[None]):
    CSS = ui.APP_CSS
    CARD_TITLE_STYLE = f"bold {ui.TEXT_PRIMARY}"
    BODY_TEXT_STYLE = ui.TEXT_DEFAULT
    MUTED_TEXT_STYLE = ui.TEXT_MUTED
    SUBTLE_TEXT_STYLE = ui.TEXT_SUBTLE
    SELECTED_TEXT_STYLE = f"bold {ui.TEXT_INVERTED}"
    BORDER_STYLE = ui.ACCENT_BORDER

    BINDINGS = [
        Binding("tab", "focus_next_pane", "Next Pane", show=False, priority=True),
        Binding("shift+tab", "focus_previous_pane", "Prev Pane", show=False, priority=True),
        Binding("right", "focus_next_pane", "Next Pane", show=False, priority=True),
        Binding("left", "focus_previous_pane", "Prev Pane", show=False, priority=True),
        Binding("escape", "focus_tasks", "Focus Tasks", show=False, priority=True),
        Binding("up,k", "previous_task", "Prev", key_display="↑/k", priority=True),
        Binding("down,j", "next_task", "Next", key_display="↓/j", priority=True),
        Binding("n", "new_task", "Add"),
        Binding("e,enter", "edit_task", "Edit", key_display="e/Enter"),
        Binding("space", "complete_task", "Complete"),
        Binding("x", "delete_task", "Delete"),
        Binding("s", "show_sync_preview", "Sync"),
        Binding("L", "manage_labels", "Labels", key_display="Shift+L"),
        Binding("r", "refresh_data", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    TITLE = "Todoist Kanban"
    ENABLE_COMMAND_PALETTE = False
    NAVIGATION_REPEAT_INTERVAL = 0.12
    NAVIGATION_KEYS = {"left", "right", "up", "down", "h", "j", "k", "l"}
    PANE_ORDER = ("groups", "tasks", "inspector")

    def __init__(
        self,
        token: str,
        due_lang: str = "en",
        *,
        sync_root: Path | str = DEFAULT_MARKDOWN_ROOT,
        sync_state_path: Path | str | None = None,
    ) -> None:
        super().__init__()
        self.gateway = TodoistGateway(token, due_lang=due_lang)
        self.inbox_project_id: str | None = None
        self.inbox_name = "Inbox"
        self.tasks: list[Task] = []
        self.labels: list[TodoistLabel] = []
        self.task_lookup: dict[str, Task] = {}
        self.label_lookup: dict[str, TodoistLabel] = {}
        self.label_name_lookup: dict[str, str] = {}
        self.label_name_colors: dict[str, str] = {}
        self.groups: list[LabelGroup] = []
        self.selection = SelectionState()
        self.active_pane = "tasks"
        self.status = "Connecting to Todoist..."
        self.busy = False
        self._group_buttons: dict[str, Button] = {}
        self._last_navigation_key: str | None = None
        self._last_navigation_time = 0.0
        self.sync_root = Path(sync_root)
        self.sync_state_path = Path(sync_state_path) if sync_state_path is not None else default_sync_state_path(self.sync_root)
        self.sync_preview = self._empty_sync_preview()
        self.sync_preview_error: str | None = None
        self.register_theme(ui.APP_THEME)
        self.theme = ui.APP_THEME.name

    def compose(self) -> ComposeResult:
        with Container(id="app-shell"):
            yield Static(id="workspace-header")
            with Horizontal(id="content"):
                with ActivePaneScroll("groups", id="group-rail"):
                    yield Vertical(id="group-strip")
                with ActivePaneScroll("tasks", id="task-panel"):
                    yield Vertical(id="task-list")
                with Vertical(id="detail-stack"):
                    with ActivePaneScroll("inspector", id="detail-panel"):
                        yield Static(id="detail-summary")
                        yield Markdown(id="detail-markdown", open_links=False)
                    yield Static(id="calendar-panel")
            yield Static(id="status")
            yield AppFooter()

    async def on_mount(self) -> None:
        self._refresh_ui()
        self.load_snapshot()

    def on_unmount(self) -> None:
        self.workers.cancel_all()
        self.gateway.close()

    @property
    def selected_group(self) -> LabelGroup:
        if not self.groups:
            return LabelGroup(
                key="all",
                title="All Tasks",
                accent=ui.ACCENT_PRIMARY,
                tasks=[],
                help_text="Everything in your Inbox appears here.",
            )
        self.selection.group_index = max(0, min(self.selection.group_index, len(self.groups) - 1))
        return self.groups[self.selection.group_index]

    @property
    def selected_task(self) -> Task | None:
        tasks = self.selected_group.tasks
        if not tasks:
            return None
        self.selection.task_index = max(0, min(self.selection.task_index, len(tasks) - 1))
        return tasks[self.selection.task_index]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        group_key = next(
            (key for key, button in self._group_buttons.items() if button is event.button),
            None,
        )
        if group_key is None:
            return
        self.select_group_by_click(group_key)

    def select_group_by_click(self, group_key: str) -> None:
        if not self._is_main_screen_active():
            return
        self._set_active_pane("groups")
        self._select_group_by_key(group_key)
        self._refresh_group_and_task_views()

    def select_task_by_click(self, task_id: str) -> None:
        if not self._is_main_screen_active():
            return
        self._set_active_pane("tasks")
        self._select_task_by_id(task_id)
        self._refresh_task_views()

    def activate_pane_from_mouse(self, pane: str) -> None:
        if not self._is_main_screen_active():
            return
        self._set_active_pane(pane)

    def on_key(self, event: events.Key) -> None:
        if not self._is_main_screen_active():
            return
        key = event.key.lower()
        if key not in self.NAVIGATION_KEYS:
            return
        if self._should_accept_navigation_key(key):
            return
        event.prevent_default()
        event.stop()

    def _should_accept_navigation_key(self, key: str) -> bool:
        now = time.monotonic()
        if (
            key == self._last_navigation_key
            and now - self._last_navigation_time < self.NAVIGATION_REPEAT_INTERVAL
        ):
            return False
        self._last_navigation_key = key
        self._last_navigation_time = now
        return True

    def action_previous_group(self) -> None:
        if not self._is_main_screen_active():
            raise SkipAction()
        if self.active_pane != "groups":
            return
        if self.selection.group_index == 0:
            return
        self.selection.group_index -= 1
        self._clamp_selection()
        self._refresh_group_and_task_views()

    def action_next_group(self) -> None:
        if not self._is_main_screen_active():
            raise SkipAction()
        if self.active_pane != "groups":
            return
        if self.selection.group_index >= len(self.groups) - 1:
            return
        self.selection.group_index += 1
        self._clamp_selection()
        self._refresh_group_and_task_views()

    def action_previous_task(self) -> None:
        if not self._is_main_screen_active():
            raise SkipAction()
        if self.active_pane == "groups":
            self.action_previous_group()
            return
        if self.active_pane == "inspector":
            self._scroll_inspector(-1)
            return
        if self.active_pane != "tasks":
            return
        if self.selection.task_index == 0:
            return
        self.selection.task_index -= 1
        self._refresh_task_views()

    def action_next_task(self) -> None:
        if not self._is_main_screen_active():
            raise SkipAction()
        if self.active_pane == "groups":
            self.action_next_group()
            return
        if self.active_pane == "inspector":
            self._scroll_inspector(1)
            return
        if self.active_pane != "tasks":
            return
        if self.selection.task_index >= len(self.selected_group.tasks) - 1:
            return
        self.selection.task_index += 1
        self._refresh_task_views()

    def action_focus_next_pane(self) -> None:
        if not self._is_main_screen_active():
            raise SkipAction()
        index = self.PANE_ORDER.index(self.active_pane)
        self._set_active_pane(self.PANE_ORDER[(index + 1) % len(self.PANE_ORDER)])

    def action_focus_previous_pane(self) -> None:
        if not self._is_main_screen_active():
            raise SkipAction()
        index = self.PANE_ORDER.index(self.active_pane)
        self._set_active_pane(self.PANE_ORDER[(index - 1) % len(self.PANE_ORDER)])

    def action_focus_tasks(self) -> None:
        if not self._is_main_screen_active():
            raise SkipAction()
        self._set_active_pane("tasks")

    def action_new_task(self) -> None:
        if self.busy or self.inbox_project_id is None:
            return
        self.push_screen(TaskEditorScreen(None, self.labels), self._finish_new_task)

    def action_edit_task(self) -> None:
        task = self.selected_task
        if self.busy or task is None:
            return
        self.push_screen(TaskEditorScreen(task, self.labels), self._finish_edit_task)

    def action_complete_task(self) -> None:
        task = self.selected_task
        if self.busy or task is None:
            return
        self.push_screen(
            ConfirmScreen(
                "Complete task",
                f"Mark '{compact_text(task.content, 50)}' as completed?",
                confirm_label="Complete",
                confirm_variant="success",
            ),
            lambda confirmed, task_id=task.id: self._complete_task(task_id) if confirmed else None,
        )

    def action_delete_task(self) -> None:
        task = self.selected_task
        if self.busy or task is None:
            return
        self.push_screen(
            ConfirmScreen(
                "Delete task",
                f"Delete '{compact_text(task.content, 50)}' from Todoist?",
                confirm_label="Delete",
                confirm_variant="error",
            ),
            lambda confirmed, task_id=task.id: self._delete_task(task_id) if confirmed else None,
        )

    def action_manage_labels(self) -> None:
        if self.busy:
            return
        self.push_screen(LabelManagerScreen(self.labels), self._finish_label_request)

    def action_show_sync_preview(self) -> None:
        if self.busy:
            return
        self.push_screen(
            SyncPreviewScreen(self.sync_preview, self.sync_preview_error),
            self._finish_sync_preview,
        )

    def action_refresh_data(self) -> None:
        if self.busy:
            return
        self.load_snapshot()

    def _finish_sync_preview(self, action: SyncAction | None) -> None:
        if action is None:
            self.status = "Sync preview closed."
            self._refresh_status()
            return
        self.run_sync_action(action)

    def _finish_new_task(self, form: TaskFormData | None) -> None:
        if form is None or self.inbox_project_id is None:
            self.status = "Task creation cancelled."
            self._refresh_status()
            return

        self._run_task_mutation(
            "Creating task in Inbox...",
            lambda: self.gateway.create_task(self.inbox_project_id or "", form),
            message="Task saved to Inbox.",
            group_key=self._preferred_task_group_key(form.labels),
        )

    def _finish_edit_task(self, form: TaskFormData | None) -> None:
        task = self.selected_task
        if form is None or task is None:
            self.status = "Task edit cancelled."
            self._refresh_status()
            return

        self._run_task_mutation(
            "Updating task...",
            lambda: self.gateway.update_task(task, form),
            message="Task updated.",
            group_key=self._preferred_task_group_key(form.labels),
        )

    def _complete_task(self, task_id: str) -> None:
        self._run_simple_mutation(
            "Completing task...",
            lambda: self.gateway.complete_task(task_id),
            message="Task completed.",
            group_key=self.selected_group.key,
        )

    def _delete_task(self, task_id: str) -> None:
        self._run_simple_mutation(
            "Deleting task...",
            lambda: self.gateway.delete_task(task_id),
            message="Task deleted.",
            group_key=self.selected_group.key,
        )

    def _finish_label_request(self, request: LabelMutationRequest | None) -> None:
        if request is None:
            self.status = "Label manager closed."
            self._refresh_status()
            return

        if request.action == "create" and request.form is not None:
            self._run_label_mutation(
                "Creating label...",
                lambda: self.gateway.create_label(request.form),
                message="Label created.",
            )
            return

        if request.action == "update" and request.form is not None and request.label_id is not None:
            self._run_label_mutation(
                "Updating label...",
                lambda: self.gateway.update_label(request.label_id, request.form),
                message="Label updated.",
            )
            return

        if request.action == "delete" and request.label_id is not None:
            self._run_simple_mutation(
                "Deleting label...",
                lambda: self.gateway.delete_label(request.label_id),
                message="Label deleted.",
                group_key="all",
            )

    def _run_task_mutation(
        self,
        status_message: str,
        operation: Callable[[], Task],
        *,
        message: str,
        group_key: str,
    ) -> None:
        def wrapped() -> MutationResult:
            task = operation()
            return MutationResult(message=message, task_id=task.id, group_key=group_key)

        self.run_mutation(status_message, wrapped)

    def _run_label_mutation(
        self,
        status_message: str,
        operation: Callable[[], TodoistLabel],
        *,
        message: str,
    ) -> None:
        def wrapped() -> MutationResult:
            label = operation()
            return MutationResult(
                message=message,
                label_id=label.id,
                group_key=f"label:{label.id}",
            )

        self.run_mutation(status_message, wrapped)

    def _run_simple_mutation(
        self,
        status_message: str,
        operation: Callable[[], None],
        *,
        message: str,
        group_key: str,
    ) -> None:
        def wrapped() -> MutationResult:
            operation()
            return MutationResult(message=message, group_key=group_key)

        self.run_mutation(status_message, wrapped)

    @work(exclusive=True, group="todoist", exit_on_error=False)
    async def load_snapshot(self) -> None:
        self._set_busy(True, "Refreshing Inbox tasks and labels...")
        try:
            snapshot = await asyncio.to_thread(self.gateway.load_snapshot)
            snapshot, sync_preview, sync_error, sync_messages = await self._converge_sync(snapshot)
            await self._apply_snapshot(snapshot, sync_preview=sync_preview, sync_error=sync_error)
            if sync_messages:
                message = self._auto_sync_status(sync_messages, sync_preview, sync_error)
                self.notify(message, title="Markdown sync", severity="information", markup=False)
                self.status = message
                self._refresh_status()
        except Exception as error:
            self._handle_error("Couldn't load Todoist data.", error)
        finally:
            self._set_busy(False)

    @work(exclusive=True, group="todoist", exit_on_error=False)
    async def run_mutation(
        self,
        status_message: str,
        operation: Callable[[], MutationResult],
    ) -> None:
        self._set_busy(True, status_message)
        try:
            result = await asyncio.to_thread(operation)
            snapshot = await asyncio.to_thread(self.gateway.load_snapshot)
            snapshot, sync_preview, sync_error, sync_messages = await self._converge_sync(snapshot)
            await self._apply_snapshot(
                snapshot,
                selected_task_id=result.task_id,
                selected_group_key=result.group_key,
                sync_preview=sync_preview,
                sync_error=sync_error,
            )
            self.status = self._combine_status(result.message, sync_messages, sync_preview, sync_error)
            self.notify(
                result.message,
                title="Todoist",
                severity="information",
                markup=False,
            )
            if sync_messages:
                self.notify(
                    self._auto_sync_status(sync_messages, sync_preview, sync_error),
                    title="Markdown sync",
                    severity="information",
                    markup=False,
                )
            self._refresh_status()
        except Exception as error:
            self._handle_error("Todoist request failed.", error)
        finally:
            self._set_busy(False)

    async def _apply_snapshot(
        self,
        snapshot: TodoistSnapshot,
        *,
        selected_task_id: str | None = None,
        selected_group_key: str | None = None,
        sync_preview: SyncPreview | None = None,
        sync_error: str | None = None,
    ) -> None:
        self.inbox_project_id = snapshot.inbox.id
        self.inbox_name = snapshot.inbox.name
        self.tasks = snapshot.tasks
        self.labels = snapshot.labels
        self.task_lookup = {task.id: task for task in self.tasks}
        self.label_lookup = {label.id: label for label in self.labels}
        self.label_name_lookup = {
            label.name.casefold(): label.id for label in self.labels
        }
        self.label_name_colors = {
            label.name.casefold(): ui.COLOR_HEX_BY_NAME.get(label.color, ui.TEXT_MUTED)
            for label in self.labels
        }

        desired_group_key = selected_group_key or self.selection.current_group_key
        desired_task_id = selected_task_id or self.selection.current_task_id

        if sync_preview is not None or sync_error is not None:
            self._set_sync_preview(sync_preview, sync_error)

        self.groups = build_label_groups(self.tasks, self.labels)
        await self._sync_group_strip()
        self._select_group_by_key(desired_group_key)
        self._select_task_by_id(desired_task_id)
        self.sub_title = (
            f"{self.inbox_name} | {len(self.tasks)} tasks | {len(self.labels)} labels"
        )
        self.status = (
            f"{self.inbox_name} loaded with {len(self.tasks)} active task(s) "
            f"across {len(self.groups)} group(s). {self._sync_status_message()}"
        )
        self._refresh_ui()

    async def _load_sync_preview(self, tasks: list[Task]) -> tuple[SyncPreview, str | None]:
        try:
            preview = await asyncio.to_thread(
                build_sync_preview,
                tasks,
                notes_root=self.sync_root,
                state_path=self.sync_state_path,
            )
            return (preview, None)
        except Exception as error:
            return (self._empty_sync_preview(), format_error(error))

    async def _converge_sync(
        self,
        snapshot: TodoistSnapshot,
    ) -> tuple[TodoistSnapshot, SyncPreview, str | None, list[str]]:
        current_snapshot = snapshot
        sync_preview, sync_error = await self._load_sync_preview(current_snapshot.tasks)
        messages: list[str] = []

        for _ in range(100):
            if sync_error or not sync_preview.plan.actions:
                return current_snapshot, sync_preview, sync_error, messages

            messages.extend(await asyncio.to_thread(self._apply_sync_actions, sync_preview.plan.actions))
            current_snapshot = await asyncio.to_thread(self.gateway.load_snapshot)
            sync_preview, sync_error = await self._load_sync_preview(current_snapshot.tasks)

        raise RuntimeError("Automatic markdown sync did not converge after 100 passes.")

    @work(exclusive=True, group="todoist", exit_on_error=False)
    async def run_sync_action(self, action: SyncAction) -> None:
        self._set_busy(True, f"Applying sync action: {action.kind}...")
        try:
            message = await asyncio.to_thread(self._apply_sync_action, action)
            snapshot = await asyncio.to_thread(self.gateway.load_snapshot)
            snapshot, sync_preview, sync_error, sync_messages = await self._converge_sync(snapshot)
            await self._apply_snapshot(snapshot, sync_preview=sync_preview, sync_error=sync_error)
            self.status = self._combine_status(message, sync_messages, sync_preview, sync_error)
            self.notify(message, title="Markdown sync", severity="information", markup=False)
            if sync_messages:
                self.notify(
                    self._auto_sync_status(sync_messages, sync_preview, sync_error),
                    title="Markdown sync",
                    severity="information",
                    markup=False,
                )
            self._refresh_status()
        except Exception as error:
            self._handle_error("Sync action failed.", error)
        finally:
            self._set_busy(False)

    def _apply_sync_actions(self, actions: tuple[SyncAction, ...]) -> list[str]:
        return [self._apply_sync_action(action) for action in actions]

    def _apply_sync_action(self, action: SyncAction) -> str:
        if action.kind == CONFLICT:
            raise RuntimeError("Conflicts must be resolved manually.")

        records = load_sync_records(self.sync_state_path)

        if action.kind == CREATE_TODOIST:
            if self.inbox_project_id is None or action.payload is None or action.markdown_path is None:
                raise RuntimeError("Sync action is missing Inbox or markdown payload context.")
            created = self.gateway.create_task_from_payload(self.inbox_project_id, action.payload)
            note = MarkdownNote(
                path=action.markdown_path,
                payload=action.payload,
                sync_id=action.sync_id,
                todoist_id=created.id,
            )
            write_markdown_note(note)
            records = upsert_sync_record(
                records,
                SyncRecord.capture(
                    action.sync_id,
                    markdown=note,
                    todoist=todoist_task_to_replica(created),
                ),
            )
            save_sync_records(self.sync_state_path, records)
            return f"Created Todoist task for '{action.payload.title}'."

        if action.kind == CREATE_MARKDOWN:
            if action.payload is None or action.todoist_id is None:
                raise RuntimeError("Sync action is missing markdown creation payload.")
            note = MarkdownNote(
                path=choose_markdown_note_path(self.sync_root, action.payload),
                payload=action.payload,
                sync_id=action.sync_id,
                todoist_id=action.todoist_id,
            )
            write_markdown_note(note)
            records = upsert_sync_record(
                records,
                SyncRecord.capture(
                    action.sync_id,
                    markdown=note,
                    todoist=_replica_from_action(action),
                ),
            )
            save_sync_records(self.sync_state_path, records)
            return f"Created markdown note for '{action.payload.title}'."

        if action.kind == UPDATE_TODOIST:
            if action.payload is None or action.todoist_id is None or action.markdown_path is None:
                raise RuntimeError("Sync action is missing Todoist update context.")
            updated = self.gateway.update_task_by_id(action.todoist_id, action.payload)
            note = MarkdownNote(
                path=action.markdown_path,
                payload=action.payload,
                sync_id=action.sync_id,
                todoist_id=updated.id,
            )
            records = upsert_sync_record(
                records,
                SyncRecord.capture(
                    action.sync_id,
                    markdown=note,
                    todoist=todoist_task_to_replica(updated),
                ),
            )
            save_sync_records(self.sync_state_path, records)
            return f"Updated Todoist task from '{action.payload.title}'."

        if action.kind == UPDATE_MARKDOWN:
            if action.payload is None or action.todoist_id is None:
                raise RuntimeError("Sync action is missing markdown update context.")
            path = action.markdown_path or self._record_markdown_path(records, action.sync_id)
            note = MarkdownNote(
                path=path,
                payload=action.payload,
                sync_id=action.sync_id,
                todoist_id=action.todoist_id,
            )
            write_markdown_note(note)
            records = upsert_sync_record(
                records,
                SyncRecord.capture(
                    action.sync_id,
                    markdown=note,
                    todoist=_replica_from_action(action),
                ),
            )
            save_sync_records(self.sync_state_path, records)
            return f"Updated markdown note from Todoist task '{action.payload.title}'."

        if action.kind == DELETE_MARKDOWN:
            if action.markdown_path is None:
                raise RuntimeError("Sync action is missing markdown delete path.")
            action.markdown_path.unlink(missing_ok=True)
            records = remove_sync_record(records, action.sync_id)
            save_sync_records(self.sync_state_path, records)
            return f"Deleted markdown note for sync id '{action.sync_id}'."

        if action.kind == DELETE_TODOIST:
            if action.todoist_id is None:
                raise RuntimeError("Sync action is missing Todoist delete id.")
            self.gateway.delete_task(action.todoist_id)
            records = remove_sync_record(records, action.sync_id)
            save_sync_records(self.sync_state_path, records)
            return f"Deleted Todoist task '{action.todoist_id}'."

        if action.kind == BIND:
            if action.todoist_id is None:
                raise RuntimeError("Sync bind action is missing Todoist id.")
            path = action.markdown_path or self._record_markdown_path(records, action.sync_id)
            payload = action.payload or self._payload_from_existing_note(path)
            note = MarkdownNote(
                path=path,
                payload=payload,
                sync_id=action.sync_id,
                todoist_id=action.todoist_id,
            )
            write_markdown_note(note)
            records = upsert_sync_record(
                records,
                SyncRecord.capture(
                    action.sync_id,
                    markdown=note,
                    todoist=_replica_from_action(
                        SyncAction(
                            kind=action.kind,
                            sync_id=action.sync_id,
                            reason=action.reason,
                            payload=payload,
                            markdown_path=path,
                            todoist_id=action.todoist_id,
                        )
                    ),
                ),
            )
            save_sync_records(self.sync_state_path, records)
            return f"Bound markdown note to Todoist task '{action.todoist_id}'."

        raise RuntimeError(f"Unsupported sync action: {action.kind}")

    @staticmethod
    def _record_markdown_path(records: list[SyncRecord], sync_id: str) -> Path:
        for record in records:
            if record.sync_id == sync_id and record.markdown_path is not None:
                return Path(record.markdown_path)
        raise RuntimeError(f"Missing markdown path for sync id '{sync_id}'.")

    @staticmethod
    def _payload_from_existing_note(path: Path) -> TaskPayload:
        return read_markdown_note(path).payload

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self.busy = busy
        if message is not None:
            self.status = message
        self._refresh_header()
        self._refresh_status()

    def _handle_error(self, title: str, error: Exception) -> None:
        message = format_error(error)
        self.status = message
        self.notify(message, title=title, severity="error", markup=False)
        self._refresh_status()

    def _empty_sync_preview(self) -> SyncPreview:
        return SyncPreview(
            notes_root=self.sync_root,
            state_path=self.sync_state_path,
            note_count=0,
            record_count=0,
            plan=SyncPlan(),
        )

    def _set_sync_preview(self, preview: SyncPreview | None, error: str | None) -> None:
        self.sync_preview = preview or self._empty_sync_preview()
        self.sync_preview_error = error

    def _sync_status_message(self) -> str:
        if self.sync_preview_error:
            return f"Markdown sync preview unavailable. Press s for details."
        return f"Markdown sync preview: {summarize_sync_preview(self.sync_preview)}. Press s."

    @staticmethod
    def _auto_sync_status(
        sync_messages: list[str],
        sync_preview: SyncPreview,
        sync_error: str | None,
    ) -> str:
        suffix = ""
        if sync_error:
            suffix = " Sync preview unavailable after automatic sync."
        elif sync_preview.plan.conflicts:
            suffix = f" {len(sync_preview.plan.conflicts)} conflict(s) still need manual resolution."
        return f"Applied {len(sync_messages)} sync action(s) automatically.{suffix}"

    def _combine_status(
        self,
        base_message: str,
        sync_messages: list[str],
        sync_preview: SyncPreview,
        sync_error: str | None,
    ) -> str:
        if not sync_messages:
            return base_message
        return f"{base_message} {self._auto_sync_status(sync_messages, sync_preview, sync_error)}"

    def _preferred_task_group_key(self, label_names: list[str]) -> str:
        normalized = {name.casefold() for name in label_names}
        current_group_key = self.selection.current_group_key

        if current_group_key == "all":
            return "all"
        if current_group_key == "unlabeled" and not normalized:
            return "unlabeled"
        if current_group_key.startswith("label:"):
            label_id = current_group_key.partition(":")[2]
            current_label = self.label_lookup.get(label_id)
            if current_label is not None and current_label.name.casefold() in normalized:
                return current_group_key

        for label_name in label_names:
            label_id = self.label_name_lookup.get(label_name.casefold())
            if label_id is not None:
                return f"label:{label_id}"

        return "unlabeled" if not normalized else "all"

    def _clamp_selection(self) -> None:
        if not self.groups:
            self.selection = SelectionState()
            return

        self.selection.group_index = max(0, min(self.selection.group_index, len(self.groups) - 1))
        self.selection.current_group_key = self.selected_group.key

        tasks = self.selected_group.tasks
        if not tasks:
            self.selection.task_index = 0
            self.selection.current_task_id = None
            return

        self.selection.task_index = max(0, min(self.selection.task_index, len(tasks) - 1))
        self.selection.current_task_id = tasks[self.selection.task_index].id

    def _select_group_by_key(self, group_key: str | None) -> None:
        if not self.groups:
            self.selection = SelectionState()
            return

        for index, group in enumerate(self.groups):
            if group.key == group_key:
                self.selection.group_index = index
                break
        else:
            self.selection.group_index = 0
        self._clamp_selection()

    def _select_task_by_id(self, task_id: str | None) -> None:
        if not task_id:
            self._clamp_selection()
            return

        for index, task in enumerate(self.selected_group.tasks):
            if task.id == task_id:
                self.selection.task_index = index
                self._clamp_selection()
                return
        self._clamp_selection()

    async def _sync_group_strip(self) -> None:
        strip = self.query_one("#group-strip", Vertical)
        desired_keys = [group.key for group in self.groups]

        obsolete_keys = [key for key in self._group_buttons if key not in desired_keys]
        if obsolete_keys:
            await strip.remove_children(
                [
                    self._group_buttons[key]
                    for key in obsolete_keys
                    if self._group_buttons[key].parent is strip
                ]
            )
            for key in obsolete_keys:
                self._group_buttons.pop(key, None)

        for group in self.groups:
            if group.key not in self._group_buttons:
                button = GroupChipButton(group.key, self._group_button_label(group), classes="group-chip")
                self._group_buttons[group.key] = button
                await strip.mount(button)

        current_keys = [
            key
            for child in strip.children
            for key, button in self._group_buttons.items()
            if button is child
        ]
        if current_keys != desired_keys and desired_keys:
            for index, group_key in enumerate(desired_keys):
                button = self._group_buttons[group_key]
                if index == 0:
                    strip.move_child(button, before=0)
                    continue
                strip.move_child(button, after=self._group_buttons[desired_keys[index - 1]])

        self._update_group_buttons()

    def _refresh_ui(self) -> None:
        if not self.is_mounted:
            return
        self._refresh_header()
        self._refresh_group_context()
        self._refresh_task_views()
        self._refresh_status()

    def _refresh_task_views(self) -> None:
        if not self.is_mounted:
            return

        self._clamp_selection()
        self._refresh_task_list()
        self.query_one("#detail-summary", Static).update(self._build_detail_panel())
        self.query_one("#detail-markdown", Markdown).update(build_detail_markdown(self.selected_task))
        self.query_one("#calendar-panel", Static).update(build_calendar_widget(self.selected_task))

    def _refresh_group_and_task_views(self) -> None:
        if not self.is_mounted:
            return
        self._refresh_group_context()
        self._refresh_task_views()

    def _refresh_group_context(self) -> None:
        if not self.is_mounted:
            return
        self._clamp_selection()
        selected_group = self.selected_group
        task_panel = self.query_one("#task-panel", VerticalScroll)
        task_panel.border_title = self._task_panel_title(selected_group)
        task_panel.border_subtitle = self._task_panel_subtitle(selected_group)
        self._refresh_pane_chrome()
        self._update_group_buttons()

    def _refresh_header(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#workspace-header", Static).update(
            build_workspace_header(
                self.inbox_name,
                len(self.tasks),
                len(self.labels),
                busy=self.busy,
            )
        )

    def _refresh_status(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#status", Static).update(self._build_status_bar())

    def _update_group_buttons(self) -> None:
        for index, group in enumerate(self.groups):
            button = self._group_buttons.get(group.key)
            if button is None:
                continue
            button.label = self._group_button_label(group)
            if index == self.selection.group_index:
                button.add_class("is-active")
                button.styles.border = ("none", ui.PANEL_SHADE)
                button.styles.border_left = (
                    "heavy",
                    group.accent if self.active_pane == "groups" else ui.INACTIVE_TASK_BORDER,
                )
                button.styles.background = (
                    ui.SURFACE_BG if self.active_pane == "groups" else ui.TAB_BG
                )
                button.styles.color = group.accent
                button.styles.text_style = "bold"
                if self.active_pane == "groups":
                    button.scroll_visible(animate=False, immediate=True)
            else:
                button.remove_class("is-active")
                button.styles.border = ("none", ui.PANEL_SHADE)
                button.styles.background = ui.PANEL_SHADE
                button.styles.color = group.accent
                button.styles.text_style = "none"

    def _group_button_label(self, group: LabelGroup) -> str:
        if group.key == "all":
            marker = "◎"
        elif group.key == "unlabeled":
            marker = "○"
        else:
            marker = "●"
        return f"{marker} {group_label(group)}"

    def _refresh_pane_chrome(self) -> None:
        group_rail = self.query_one("#group-rail", VerticalScroll)
        task_panel = self.query_one("#task-panel", VerticalScroll)
        detail_panel = self.query_one("#detail-panel", VerticalScroll)

        group_rail.border_title = "Labels"
        group_rail.border_subtitle = ""
        group_rail.styles.background = ui.PANEL_SHADE
        group_rail.styles.border = (
            ("heavy", ui.ACCENT_PRIMARY) if self.active_pane == "groups" else ("round", ui.INACTIVE_TASK_BORDER)
        )

        task_panel.border_subtitle = self._task_panel_subtitle(self.selected_group)
        task_panel.styles.background = ui.PANEL_SHADE
        task_panel.styles.border = (
            ("heavy", ui.ACCENT_PRIMARY) if self.active_pane == "tasks" else ("round", ui.INACTIVE_TASK_BORDER)
        )

        detail_panel.border_title = "Inspector"
        detail_panel.border_subtitle = ""
        detail_panel.styles.background = ui.PANEL_SHADE
        detail_panel.styles.border = (
            ("heavy", ui.ACCENT_PRIMARY) if self.active_pane == "inspector" else ("round", ui.INACTIVE_TASK_BORDER)
        )

    def _set_active_pane(self, pane: str) -> None:
        self.active_pane = pane
        if self.is_mounted:
            self._refresh_pane_chrome()
            self._update_group_buttons()

    def _scroll_inspector(self, direction: int) -> None:
        detail_panel = self.query_one("#detail-panel", VerticalScroll)
        if direction < 0:
            detail_panel.scroll_up(animate=False, immediate=True)
        else:
            detail_panel.scroll_down(animate=False, immediate=True)

    def _is_main_screen_active(self) -> bool:
        return getattr(self.screen, "id", None) == "_default"

    def _build_task_panel(self) -> RenderableType:
        task_panel = self.query_one("#task-panel", VerticalScroll)
        available_height = task_panel.size.height or self.size.height
        return build_task_panel(
            self.selected_group,
            self.selection.task_index,
            available_height,
            self._render_task_card,
            title_style=self.CARD_TITLE_STYLE,
            muted_style=self.MUTED_TEXT_STYLE,
            border_style=self.BORDER_STYLE,
        )

    def _render_task_card(self, task: Task, *, selected: bool, accent: str) -> RenderableType:
        return build_task_card(
            task,
            selected=selected,
            accent=accent,
            label_name_colors=self.label_name_colors,
            selected_text_style=self.SELECTED_TEXT_STYLE,
            body_text_style=self.BODY_TEXT_STYLE,
            subtle_text_style=self.SUBTLE_TEXT_STYLE,
            border_style=ui.INACTIVE_TASK_BORDER,
        )

    def _refresh_task_list(self) -> None:
        task_panel = self.query_one("#task-panel", VerticalScroll)
        task_list = self.query_one("#task-list", Vertical)
        available_height = task_panel.size.height or self.size.height
        tasks = self.selected_group.tasks
        widgets: list[Static] = []

        if not tasks:
            widgets.append(Static(self._build_task_panel(), classes="task-panel-message"))
        else:
            visible_cards = max(2, (max(available_height, 18) - 6) // 4)
            start, end = task_window(len(tasks), self.selection.task_index, visible_cards)

            if start:
                widgets.append(
                    Static(
                        f"{start} earlier task{'s' if start != 1 else ''} above",
                        classes="task-panel-hint",
                    )
                )
                widgets.append(Static("", classes="task-panel-spacer"))

            for index in range(start, end):
                task = tasks[index]
                widgets.append(
                    TaskCardWidget(
                        task.id,
                        self._render_task_card(
                            task,
                            selected=index == self.selection.task_index,
                            accent=self.selected_group.accent,
                        ),
                        classes="task-card-widget",
                    )
                )

            if end < len(tasks):
                widgets.append(Static("", classes="task-panel-spacer"))
                widgets.append(
                    Static(
                        f"{len(tasks) - end} more task{'s' if len(tasks) - end != 1 else ''} below",
                        classes="task-panel-hint",
                    )
                )

        task_list.remove_children()
        task_list.mount(*widgets)

    def _build_detail_panel(self) -> RenderableType:
        return build_detail_panel(
            self.selected_task,
            self.selected_group,
            title_style=self.CARD_TITLE_STYLE,
            muted_style=self.MUTED_TEXT_STYLE,
            border_style=self.BORDER_STYLE,
        )

    def _build_status_bar(self) -> RenderableType:
        return build_status_bar(self.status, busy=self.busy, body_text_style=self.BODY_TEXT_STYLE)

    def _task_panel_title(self, group: LabelGroup) -> str:
        return group.title

    def _task_panel_subtitle(self, group: LabelGroup) -> str:
        task_count = len(group.tasks)
        return f"{task_count} task{'s' if task_count != 1 else ''}"

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Todoist Inbox grouped by labels in a kanban-style TUI")
    parser.add_argument(
        "--token",
        help="Todoist API token. Falls back to TODOIST_API_TOKEN or TODOIST_TOKEN.",
    )
    parser.add_argument(
        "--due-lang",
        default=os.getenv("TODOIST_DUE_LANG", "en"),
        help="Todoist due date language, for example en or pl.",
    )
    parser.add_argument(
        "--notes-root",
        default=os.getenv("TODOIST_MARKDOWN_ROOT", str(DEFAULT_MARKDOWN_ROOT)),
        help="Markdown notes root used for sync preview.",
    )
    return parser.parse_args(argv)


def resolve_token(cli_token: str | None) -> str:
    token = cli_token or os.getenv("TODOIST_API_TOKEN") or os.getenv("TODOIST_TOKEN")
    if token:
        return token
    raise SystemExit(
        "Set TODOIST_API_TOKEN (or TODOIST_TOKEN) or pass --token to start the app."
    )


def _replica_from_action(action: SyncAction) -> TodoistTaskReplica:
    if action.payload is None or action.todoist_id is None:
        raise RuntimeError("Sync action is missing payload or Todoist id.")
    return todoist_task_to_replica(
        type(
            "SyncReplicaStub",
            (),
            {
                "id": action.todoist_id,
                "content": action.payload.title,
                "description": action.payload.body,
                "labels": list(action.payload.labels),
                "due": type("DueStub", (), {"string": action.payload.due, "date": action.payload.due})()
                if action.payload.due is not None
                else None,
                "updated_at": None,
            },
        )()
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    app = TodoistKanbanApp(
        resolve_token(args.token),
        due_lang=args.due_lang,
        sync_root=Path(args.notes_root),
    )
    app.run(mouse=True)


if __name__ == "__main__":
    main()

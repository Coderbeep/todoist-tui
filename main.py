from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections import defaultdict
from itertools import groupby
from typing import Callable

import httpx
from rich.console import RenderableType
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual import events
from textual.widgets import Button, Markdown, Static
from textual.widgets._footer import Footer as BaseFooter, FooterKey, FooterLabel, KeyGroup
from todoist_api_python.api import TodoistAPI
from todoist_api_python.models import Label as TodoistLabel
from todoist_api_python.models import Task

import ui_styles as ui
from app_types import LabelFormData, LabelGroup, LabelMutationRequest, MutationResult, SelectionState, TaskFormData, TodoistSnapshot
from app_utils import build_label_groups, compact_text, flatten_pages, format_error
from screens import ConfirmScreen, LabelManagerScreen, TaskEditorScreen
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
        payload: dict[str, object] = {
            "content": form.content,
            "description": form.description,
            "labels": form.labels,
        }
        if form.due_string:
            payload["due_string"] = form.due_string
            payload["due_lang"] = self.due_lang
        return payload


class TodoistKanbanApp(App[None]):
    CSS = ui.APP_CSS
    CARD_TITLE_STYLE = f"bold {ui.TEXT_PRIMARY}"
    BODY_TEXT_STYLE = ui.TEXT_DEFAULT
    MUTED_TEXT_STYLE = ui.TEXT_MUTED
    SUBTLE_TEXT_STYLE = ui.TEXT_SUBTLE
    SELECTED_TEXT_STYLE = f"bold {ui.TEXT_INVERTED}"
    BORDER_STYLE = ui.ACCENT_BORDER

    BINDINGS = [
        Binding("left,h", "previous_group", "Prev Group", key_display="←/h"),
        Binding("right,l", "next_group", "Next Group", key_display="→/l"),
        Binding("up,k", "previous_task", "Prev Task", key_display="↑/k"),
        Binding("down,j", "next_task", "Next Task", key_display="↓/j"),
        Binding("n", "new_task", "Add"),
        Binding("e,enter", "edit_task", "Edit", key_display="e/Enter"),
        Binding("space", "complete_task", "Complete"),
        Binding("x", "delete_task", "Delete"),
        Binding("L", "manage_labels", "Labels", key_display="Shift+L"),
        Binding("r", "refresh_data", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    TITLE = "Todoist Kanban"
    ENABLE_COMMAND_PALETTE = False
    NAVIGATION_REPEAT_INTERVAL = 0.12
    NAVIGATION_KEYS = {"left", "right", "up", "down", "h", "j", "k", "l"}

    def __init__(self, token: str, due_lang: str = "en") -> None:
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
        self.status = "Connecting to Todoist..."
        self.busy = False
        self._group_buttons: dict[str, Button] = {}
        self._last_navigation_key: str | None = None
        self._last_navigation_time = 0.0
        self.register_theme(ui.APP_THEME)
        self.theme = ui.APP_THEME.name

    def compose(self) -> ComposeResult:
        with Container(id="app-shell"):
            yield Static(id="workspace-header")
            with HorizontalScroll(id="group-rail"):
                yield Horizontal(id="group-strip")
            with Horizontal(id="content"):
                yield Static(id="task-panel")
                with Vertical(id="detail-stack"):
                    with VerticalScroll(id="detail-panel"):
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
        self._select_group_by_key(group_key)
        self._refresh_group_and_task_views()

    def on_key(self, event: events.Key) -> None:
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
        if self.selection.group_index == 0:
            return
        self.selection.group_index -= 1
        self._clamp_selection()
        self._refresh_group_and_task_views()

    def action_next_group(self) -> None:
        if self.selection.group_index >= len(self.groups) - 1:
            return
        self.selection.group_index += 1
        self._clamp_selection()
        self._refresh_group_and_task_views()

    def action_previous_task(self) -> None:
        if self.selection.task_index == 0:
            return
        self.selection.task_index -= 1
        self._refresh_task_views()

    def action_next_task(self) -> None:
        if self.selection.task_index >= len(self.selected_group.tasks) - 1:
            return
        self.selection.task_index += 1
        self._refresh_task_views()

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

    def action_refresh_data(self) -> None:
        if self.busy:
            return
        self.load_snapshot()

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
            await self._apply_snapshot(snapshot)
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
            await self._apply_snapshot(
                snapshot,
                selected_task_id=result.task_id,
                selected_group_key=result.group_key,
            )
            self.status = result.message
            self.notify(
                result.message,
                title="Todoist",
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

        self.groups = build_label_groups(self.tasks, self.labels)
        await self._sync_group_strip()
        self._select_group_by_key(desired_group_key)
        self._select_task_by_id(desired_task_id)
        self.sub_title = (
            f"{self.inbox_name} | {len(self.tasks)} tasks | {len(self.labels)} labels"
        )
        self.status = (
            f"{self.inbox_name} loaded with {len(self.tasks)} active task(s) "
            f"across {len(self.groups)} group(s)."
        )
        self._refresh_ui()

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
        strip = self.query_one("#group-strip", Horizontal)
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
                button = Button(group_label(group), classes="group-chip")
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
        self.query_one("#task-panel", Static).update(self._build_task_panel())
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
        task_panel = self.query_one("#task-panel", Static)
        task_panel.border_title = self._task_panel_title(selected_group)
        task_panel.border_subtitle = self._task_panel_subtitle(selected_group)
        task_panel.styles.border = ("round", ui.ACCENT_BORDER)
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
            button.label = group_label(group)
            button.styles.color = group.accent
            button.styles.border = ("round", group.accent)
            if index == self.selection.group_index:
                button.add_class("is-active")
                button.styles.background = ui.SURFACE_BG
                button.styles.text_style = "bold"
            else:
                button.remove_class("is-active")
                button.styles.background = ui.TAB_BG
                button.styles.text_style = "none"

    def _build_task_panel(self) -> RenderableType:
        return build_task_panel(
            self.selected_group,
            self.selection.task_index,
            self.size.height,
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
    return parser.parse_args(argv)


def resolve_token(cli_token: str | None) -> str:
    token = cli_token or os.getenv("TODOIST_API_TOKEN") or os.getenv("TODOIST_TOKEN")
    if token:
        return token
    raise SystemExit(
        "Set TODOIST_API_TOKEN (or TODOIST_TOKEN) or pass --token to start the app."
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    app = TodoistKanbanApp(resolve_token(args.token), due_lang=args.due_lang)
    app.run(mouse=False)


if __name__ == "__main__":
    main()

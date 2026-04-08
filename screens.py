from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Button, Checkbox, Input, Markdown, Select, Static, TextArea
from todoist_api_python.models import Label as TodoistLabel
from todoist_api_python.models import Task

import ui_styles as ui
from app_types import LabelFormData, LabelMutationRequest, TaskFormData
from app_utils import parse_label_names
from md_sync import SyncAction, SyncPreview, summarize_sync_preview
from views import build_label_manager_rows


class LabelSuggester(Suggester):
    def __init__(self, labels: list[str]) -> None:
        super().__init__(case_sensitive=True)
        self.labels = labels

    async def get_suggestion(self, value: str) -> str | None:
        prefix, fragment = split_label_input(value)
        fragment_text = fragment.strip()
        if not fragment_text:
            return None

        normalized_fragment = fragment_text.casefold()
        normalized_selected = {
            label.casefold()
            for label in parse_label_names(prefix)
        }

        for label in self.labels:
            if label.casefold() in normalized_selected:
                continue
            if label.casefold().startswith(normalized_fragment):
                spacer = "" if not prefix or prefix.endswith((" ", ",")) else " "
                return f"{prefix}{spacer}{label}"
        return None


def split_label_input(raw: str) -> tuple[str, str]:
    head, separator, tail = raw.rpartition(",")
    if not separator:
        return ("", raw)
    return (f"{head}{separator}", tail)


class ConfirmScreen(ModalScreen[bool]):
    CSS = ui.CONFIRM_SCREEN_CSS

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "confirm", "Confirm", show=False),
        Binding("n", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Confirm", show=False),
    ]

    def __init__(
        self,
        title: str,
        message: str,
        *,
        confirm_label: str = "Confirm",
        confirm_variant: str = "primary",
    ) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._confirm_label = confirm_label
        self._confirm_variant = confirm_variant

    def compose(self) -> ComposeResult:
        shell = Container(
            Static(self._message, id="confirm-message"),
            Horizontal(
                Button(Text("Cancel [N/Esc]"), id="confirm-no", compact=True),
                Button(
                    Text(f"{self._confirm_label} [Y/Enter]"),
                    id="confirm-yes",
                    variant=self._confirm_variant,
                    compact=True,
                ),
                id="confirm-actions",
            ),
            id="confirm-shell",
        )
        shell.border_title = self._title
        yield shell

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


class TaskEditorScreen(ModalScreen[TaskFormData | None]):
    CSS = ui.TASK_EDITOR_SCREEN_CSS

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "submit", "Save", show=False),
        Binding("ctrl+enter", "submit", "Save", show=False),
    ]

    def __init__(self, task: Task | None, labels: list[TodoistLabel]) -> None:
        super().__init__()
        self.todoist_task = task
        self.available_label_names = sorted((label.name for label in labels), key=str.casefold)

    def compose(self) -> ComposeResult:
        title = "Edit task" if self.todoist_task is not None else "Create task"
        content_input = Input(
            value=self.todoist_task.content if self.todoist_task is not None else "",
            placeholder="Required",
            select_on_focus=False,
            id="task-editor-content",
        )
        content_input.border_title = " Content "

        description_input = TextArea(
            self.todoist_task.description or "" if self.todoist_task is not None else "",
            id="task-editor-description",
            highlight_cursor_line=False,
            placeholder="Optional details",
        )
        description_input.border_title = " Description "

        labels_input = Input(
            value=", ".join(self.todoist_task.labels or []) if self.todoist_task is not None else "",
            placeholder="comma-separated labels, type to autocomplete",
            suggester=LabelSuggester(self.available_label_names),
            select_on_focus=False,
            id="task-editor-labels",
        )
        labels_input.border_title = " Labels "

        due_input = Input(
            value=self.todoist_task.due.string if self.todoist_task is not None and self.todoist_task.due else "",
            placeholder="tomorrow 5pm, friday, next month...",
            select_on_focus=False,
            id="task-editor-due",
        )
        due_input.border_title = " Due "

        shell = Container(
            content_input,
            description_input,
            labels_input,
            Static(id="task-editor-label-hint"),
            due_input,
            Horizontal(
                Button(Text("Cancel [Esc]"), id="task-editor-cancel", compact=True),
                Button(Text("Save [Ctrl+S]"), id="task-editor-save", variant="success", compact=True),
                id="task-editor-actions",
            ),
            id="task-editor-shell",
        )
        shell.border_title = title
        yield shell

    def on_mount(self) -> None:
        self._move_task_editor_cursors_to_end()
        content_input = self.query_one("#task-editor-content", Input)
        content_input.focus()
        self._refresh_label_hint(self.query_one("#task-editor-labels", Input).value)

    def _move_task_editor_cursors_to_end(self) -> None:
        for selector in ("#task-editor-content", "#task-editor-labels", "#task-editor-due"):
            input_widget = self.query_one(selector, Input)
            input_widget.cursor_position = len(input_widget.value)

        description_input = self.query_one("#task-editor-description", TextArea)
        description_lines = description_input.text.splitlines() or [""]
        description_input.cursor_location = (
            len(description_lines) - 1,
            len(description_lines[-1]),
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        content = self.query_one("#task-editor-content", Input).value.strip()
        if not content:
            self.notify("Task content is required.", title="Todoist", severity="warning", markup=False)
            return

        self.dismiss(
            TaskFormData(
                content=content,
                description=self.query_one("#task-editor-description", TextArea).text.strip(),
                labels=parse_label_names(self.query_one("#task-editor-labels", Input).value),
                due_string=self.query_one("#task-editor-due", Input).value.strip() or None,
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "task-editor-save":
            self.action_submit()
            return
        self.action_cancel()

    @on(Input.Changed, "#task-editor-labels")
    def _on_task_editor_labels_changed(self, event: Input.Changed) -> None:
        self._refresh_label_hint(event.value)

    def _refresh_label_hint(self, raw_value: str) -> None:
        hint = self.query_one("#task-editor-label-hint", Static)
        if not self.available_label_names:
            hint.update("")
            return

        prefix, fragment = split_label_input(raw_value)
        fragment_text = fragment.strip()
        selected_names = parse_label_names(prefix)
        selected_names.extend(parse_label_names(fragment_text) if "," not in fragment else [])
        selected = {label.casefold() for label in selected_names}
        normalized_fragment = fragment_text.casefold()

        if fragment_text:
            if normalized_fragment in {label.casefold() for label in self.available_label_names}:
                hint.update("")
                return
            matches = [
                f"@{label}"
                for label in self.available_label_names
                if label.casefold().startswith(normalized_fragment) and label.casefold() not in selected
            ][:8]
            if matches:
                hint.update(f"Matching labels: {', '.join(matches)}")
                return
            hint.update("No existing labels match the current text.")
            return

        hint.update("")


class LabelEditorScreen(ModalScreen[LabelFormData | None]):
    CSS = ui.LABEL_EDITOR_SCREEN_CSS

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "submit", "Save", show=False),
        Binding("ctrl+enter", "submit", "Save", show=False),
    ]

    def __init__(self, label: TodoistLabel | None) -> None:
        super().__init__()
        self.label = label

    def compose(self) -> ComposeResult:
        title = "Edit label" if self.label is not None else "Create label"
        name_input = Input(
            value=self.label.name if self.label is not None else "",
            placeholder="Label name",
            select_on_focus=False,
            id="label-editor-name",
        )
        name_input.border_title = " Name "

        color_input = Select(
            ui.COLOR_SELECT_OPTIONS,
            allow_blank=False,
            value=self.label.color if self.label is not None else "charcoal",
            id="label-editor-color",
        )
        color_input.border_title = " Color "

        favorite_input = Checkbox(
            "Favorite",
            value=self.label.is_favorite if self.label is not None else False,
            id="label-editor-favorite",
            compact=True,
        )
        favorite_input.border_title = " Options "

        shell = Container(
            name_input,
            color_input,
            favorite_input,
            Horizontal(
                Button(Text("Cancel [Esc]"), id="label-editor-cancel", compact=True),
                Button(Text("Save [Ctrl+S]"), id="label-editor-save", variant="success", compact=True),
                id="label-editor-actions",
            ),
            id="label-editor-shell",
        )
        shell.border_title = title
        yield shell

    def on_mount(self) -> None:
        name_input = self.query_one("#label-editor-name", Input)
        name_input.cursor_position = len(name_input.value)
        name_input.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        name = self.query_one("#label-editor-name", Input).value.strip()
        if not name:
            self.notify("Label name is required.", title="Todoist", severity="warning", markup=False)
            return

        color = self.query_one("#label-editor-color", Select).selection or "charcoal"
        self.dismiss(
            LabelFormData(
                name=name,
                color=str(color),
                is_favorite=self.query_one("#label-editor-favorite", Checkbox).value,
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "label-editor-save":
            self.action_submit()
            return
        self.action_cancel()


class LabelManagerScreen(ModalScreen[LabelMutationRequest | None]):
    CSS = ui.LABEL_MANAGER_SCREEN_CSS

    BINDINGS = [
        Binding("up,k", "previous_label", "Prev", show=False),
        Binding("down,j", "next_label", "Next", show=False),
        Binding("a", "add_label", "Add", show=False),
        Binding("e,enter", "edit_label", "Edit", show=False),
        Binding("x", "delete_label", "Delete", show=False),
        Binding("escape,q", "close_screen", "Close", show=False),
    ]

    def __init__(self, labels: list[TodoistLabel]) -> None:
        super().__init__()
        self.labels = labels
        self.label_index = 0

    def compose(self) -> ComposeResult:
        label_list = Static(id="label-manager-list")
        with Container(id="label-manager-shell") as shell:
            yield label_list
            with Horizontal(id="label-manager-actions"):
                yield Button(Text("Add [a]"), id="label-manager-add", compact=True)
                yield Button(Text("Edit [e]"), id="label-manager-edit", compact=True)
                yield Button(Text("Delete [x]"), id="label-manager-delete", compact=True)
                yield Button(Text("Close [Esc]"), id="label-manager-close", compact=True)
        shell.border_title = "Manage labels"

    def on_mount(self) -> None:
        self._refresh_list()

    @property
    def current_label(self) -> TodoistLabel | None:
        if not self.labels:
            return None
        self.label_index = max(0, min(self.label_index, len(self.labels) - 1))
        return self.labels[self.label_index]

    def action_previous_label(self) -> None:
        if self.label_index == 0:
            return
        self.label_index -= 1
        self._refresh_list()

    def action_next_label(self) -> None:
        if self.label_index >= len(self.labels) - 1:
            return
        self.label_index += 1
        self._refresh_list()

    def action_close_screen(self) -> None:
        self.dismiss(None)

    def action_add_label(self) -> None:
        self.app.push_screen(LabelEditorScreen(None), self._finish_add_label)

    def action_edit_label(self) -> None:
        label = self.current_label
        if label is None:
            return
        self.app.push_screen(LabelEditorScreen(label), self._finish_edit_label)

    def action_delete_label(self) -> None:
        label = self.current_label
        if label is None:
            return
        self.app.push_screen(
            ConfirmScreen(
                "Delete label",
                f"Delete label '{label.name}'? Todoist removes it from tasks too.",
                confirm_label="Delete",
                confirm_variant="error",
            ),
            lambda confirmed, label_id=label.id: self._finish_delete_label(label_id, confirmed),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "label-manager-add": self.action_add_label,
            "label-manager-edit": self.action_edit_label,
            "label-manager-delete": self.action_delete_label,
        }
        actions.get(event.button.id, self.action_close_screen)()

    def _finish_add_label(self, form: LabelFormData | None) -> None:
        if form is not None:
            self.dismiss(LabelMutationRequest(action="create", form=form))

    def _finish_edit_label(self, form: LabelFormData | None) -> None:
        label = self.current_label
        if form is not None and label is not None:
            self.dismiss(LabelMutationRequest(action="update", form=form, label_id=label.id))

    def _finish_delete_label(self, label_id: str, confirmed: bool) -> None:
        if confirmed:
            self.dismiss(LabelMutationRequest(action="delete", label_id=label_id))

    def _refresh_list(self) -> None:
        label_list = self.query_one("#label-manager-list", Static)
        label_list.update(build_label_manager_rows(self.labels, self.label_index))


class SyncPreviewScreen(ModalScreen[SyncAction | None]):
    CSS = ui.SYNC_PREVIEW_SCREEN_CSS

    BINDINGS = [
        Binding("up,k", "previous_action", "Prev", show=False),
        Binding("down,j", "next_action", "Next", show=False),
        Binding("enter,a", "apply_action", "Apply", show=False),
        Binding("escape,q,s", "close_screen", "Close", show=False),
    ]

    def __init__(self, preview: SyncPreview, error: str | None = None) -> None:
        super().__init__()
        self.preview = preview
        self.error = error
        self.action_index = 0

    def compose(self) -> ComposeResult:
        summary = Static(id="sync-preview-summary")
        action_list = Static(id="sync-preview-list")
        markdown = Markdown(id="sync-preview-markdown", open_links=False)
        with Container(id="sync-preview-shell") as shell:
            yield summary
            yield action_list
            yield markdown
            with Horizontal(id="sync-preview-actions"):
                yield Button(Text("Apply [Enter]"), id="sync-preview-apply", compact=True)
                yield Button(Text("Close [Esc]"), id="sync-preview-close", compact=True)
        shell.border_title = "Markdown sync preview"

    def on_mount(self) -> None:
        self._refresh_preview()

    @property
    def selected_action(self) -> SyncAction | None:
        actions = self.preview.plan.actions
        if not actions:
            return None
        self.action_index = max(0, min(self.action_index, len(actions) - 1))
        return actions[self.action_index]

    def action_close_screen(self) -> None:
        self.dismiss(None)

    def action_previous_action(self) -> None:
        if self.action_index == 0:
            return
        self.action_index -= 1
        self._refresh_preview()

    def action_next_action(self) -> None:
        if self.action_index >= len(self.preview.plan.actions) - 1:
            return
        self.action_index += 1
        self._refresh_preview()

    def action_apply_action(self) -> None:
        action = self.selected_action
        if action is None:
            return
        self.dismiss(action)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sync-preview-apply":
            self.action_apply_action()
            return
        if event.button.id == "sync-preview-close":
            self.dismiss(None)

    def _refresh_preview(self) -> None:
        self.query_one("#sync-preview-summary", Static).update(self._summary_text())
        self.query_one("#sync-preview-list", Static).update(self._list_text())
        self.query_one("#sync-preview-markdown", Markdown).update(self._markdown_text())

    def _summary_text(self) -> str:
        summary = summarize_sync_preview(self.preview)
        suffix = f"  Error: {self.error}" if self.error else ""
        return (
            f"Notes root: {self.preview.notes_root}\n"
            f"State file: {self.preview.state_path}\n"
            f"Snapshot: {summary}{suffix}"
        )

    def _markdown_text(self) -> str:
        selected = self.selected_action
        lines = [
            "## Summary",
            "",
            f"- Notes root: `{self.preview.notes_root}`",
            f"- State file: `{self.preview.state_path}`",
            f"- Notes discovered: `{self.preview.note_count}`",
            f"- Persisted sync records: `{self.preview.record_count}`",
            f"- Planned actions: `{len(self.preview.plan.actions)}`",
            f"- Conflicts: `{len(self.preview.plan.conflicts)}`",
        ]

        if self.error:
            lines.extend(
                [
                    "",
                    "## Error",
                    "",
                    self.error,
                ]
            )

        if selected is not None:
            lines.extend(
                [
                    "",
                    "## Selected Action",
                    "",
                    f"- Kind: `{selected.kind}`",
                    f"- Sync id: `{selected.sync_id}`",
                ]
            )
            if selected.payload is not None:
                lines.append(f"- Title: `{selected.payload.title}`")
            if selected.markdown_path is not None:
                lines.append(f"- Markdown: `{selected.markdown_path}`")
            if selected.todoist_id is not None:
                lines.append(f"- Todoist: `{selected.todoist_id}`")
            if selected.details:
                lines.append(
                    "- Details: "
                    + ", ".join(f"`{key}={value}`" for key, value in sorted(selected.details.items()))
                )
            lines.extend(["", selected.reason])

        lines.extend(self._action_section("Conflicts", self.preview.plan.conflicts))
        if not selected:
            lines.extend(self._action_section("Actions", self.preview.plan.actions))

        if not self.preview.plan.actions and not self.preview.plan.conflicts and not self.error:
            lines.extend(
                [
                    "",
                    "## Result",
                    "",
                    "No changes are currently needed.",
                ]
            )

        return "\n".join(lines)

    def _list_text(self) -> str:
        if not self.preview.plan.actions:
            return "No executable actions."

        lines = ["Planned actions:"]
        for index, action in enumerate(self.preview.plan.actions):
            marker = ">" if index == self.action_index else " "
            title = action.payload.title if action.payload is not None else action.todoist_id or action.sync_id
            lines.append(f"{marker} {index + 1}. {action.kind} :: {title}")
        return "\n".join(lines)

    @staticmethod
    def _action_section(title: str, actions: tuple[SyncAction, ...]) -> list[str]:
        if not actions:
            return []

        lines = ["", f"## {title}", ""]
        for index, action in enumerate(actions, start=1):
            lines.append(f"{index}. `{action.kind}` for `{action.sync_id}`")
            if action.payload is not None:
                lines.append(f"   Title: `{action.payload.title}`")
            if action.markdown_path is not None:
                lines.append(f"   Markdown: `{action.markdown_path}`")
            if action.todoist_id is not None:
                lines.append(f"   Todoist: `{action.todoist_id}`")
            if action.details:
                lines.append(
                    "   Details: "
                    + ", ".join(f"`{key}={value}`" for key, value in sorted(action.details.items()))
                )
            lines.append(f"   Reason: {action.reason}")
        return lines

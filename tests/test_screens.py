from __future__ import annotations

import unittest

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from app_types import LabelFormData, LabelMutationRequest, TaskFormData
from screens import (
    ConfirmScreen,
    LabelEditorScreen,
    LabelManagerScreen,
    LabelSuggester,
    TaskEditorScreen,
    split_label_input,
)
from tests.support import make_due, make_label, make_task


UNSET = object()


class ModalHostApp(App[None]):
    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen
        self.result = UNSET

    def compose(self) -> ComposeResult:
        yield Static("host")

    async def on_mount(self) -> None:
        self.push_screen(self._screen, self._capture_result)

    def _capture_result(self, result) -> None:
        self.result = result


class ScreenHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def test_label_suggester_returns_matching_label(self) -> None:
        suggester = LabelSuggester(["alpha", "beta", "gamma"])
        self.assertEqual(await suggester.get_suggestion("be"), "beta")

    async def test_label_suggester_skips_already_selected_label(self) -> None:
        suggester = LabelSuggester(["alpha", "beta", "gamma"])
        self.assertEqual(await suggester.get_suggestion("alpha,b"), "alpha,beta")
        self.assertIsNone(await suggester.get_suggestion("alpha,a"))

    def test_split_label_input_handles_raw_and_comma_separated_values(self) -> None:
        self.assertEqual(split_label_input("alpha"), ("", "alpha"))
        self.assertEqual(split_label_input("alpha, beta"), ("alpha,", " beta"))


class ScreenFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_screen_returns_true_on_y(self) -> None:
        app = ModalHostApp(ConfirmScreen("Confirm", "Proceed?"))

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()

        self.assertTrue(app.result)

    async def test_confirm_screen_returns_false_on_escape(self) -> None:
        app = ModalHostApp(ConfirmScreen("Confirm", "Proceed?"))

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

        self.assertFalse(app.result)

    async def test_task_editor_escape_cancels(self) -> None:
        labels = [make_label("label-1", "alpha")]
        app = ModalHostApp(TaskEditorScreen(None, labels))

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

        self.assertIsNone(app.result)

    async def test_task_editor_ctrl_s_returns_form_data(self) -> None:
        labels = [make_label("label-1", "alpha"), make_label("label-2", "beta")]
        task = make_task(
            "task-1",
            "Original task",
            description="Original description",
            labels=["alpha"],
            due=make_due("2026-04-03", "tomorrow"),
        )
        app = ModalHostApp(TaskEditorScreen(task, labels))

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#task-editor-content", Input).value = "  Updated task  "
            screen.query_one("#task-editor-description", TextArea).text = "  Updated details  "
            screen.query_one("#task-editor-labels", Input).value = "alpha, beta"
            screen.query_one("#task-editor-due", Input).value = "next friday"
            await pilot.press("ctrl+s")
            await pilot.pause()

        self.assertIsInstance(app.result, TaskFormData)
        self.assertEqual(app.result.content, "Updated task")
        self.assertEqual(app.result.description, "Updated details")
        self.assertEqual(app.result.labels, ["alpha", "beta"])
        self.assertEqual(app.result.due_string, "next friday")

    async def test_task_editor_positions_all_cursors_at_end_without_auto_selection(self) -> None:
        task = make_task(
            "task-1",
            "Original task",
            description="First line\nSecond line",
            labels=["alpha", "beta"],
            due=make_due("2026-04-03", "tomorrow"),
        )
        app = ModalHostApp(TaskEditorScreen(task, []))

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            content_input = screen.query_one("#task-editor-content", Input)
            labels_input = screen.query_one("#task-editor-labels", Input)
            due_input = screen.query_one("#task-editor-due", Input)
            description_input = screen.query_one("#task-editor-description", TextArea)

            self.assertFalse(content_input.select_on_focus)
            self.assertFalse(labels_input.select_on_focus)
            self.assertFalse(due_input.select_on_focus)
            self.assertFalse(description_input.highlight_cursor_line)
            self.assertEqual(content_input.selection, (len(content_input.value), len(content_input.value)))
            self.assertEqual(labels_input.selection, (len(labels_input.value), len(labels_input.value)))
            self.assertEqual(due_input.selection, (len(due_input.value), len(due_input.value)))
            self.assertEqual(description_input.cursor_location, (1, len("Second line")))
            self.assertEqual(description_input.selection.start, (1, len("Second line")))
            self.assertEqual(description_input.selection.end, (1, len("Second line")))

            labels_input.focus()
            await pilot.pause()
            self.assertEqual(labels_input.selection, (len(labels_input.value), len(labels_input.value)))

            due_input.focus()
            await pilot.pause()
            self.assertEqual(due_input.selection, (len(due_input.value), len(due_input.value)))

    async def test_task_editor_updates_label_hint_for_matches(self) -> None:
        labels = [make_label("label-1", "alpha"), make_label("label-2", "alpine"), make_label("label-3", "beta")]
        app = ModalHostApp(TaskEditorScreen(None, labels))

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#task-editor-labels", Input).value = "al"
            await pilot.pause()
            hint = screen.query_one("#task-editor-label-hint", Static).content

        self.assertEqual(hint, "Matching labels: @alpha, @alpine")

    async def test_task_editor_keeps_label_hint_empty_for_exact_existing_label(self) -> None:
        labels = [make_label("label-1", "alpha"), make_label("label-2", "beta")]
        app = ModalHostApp(TaskEditorScreen(make_task("task-1", "Task", labels=["alpha"]), labels))

        async with app.run_test() as pilot:
            await pilot.pause()
            hint = app.screen.query_one("#task-editor-label-hint", Static).content

        self.assertEqual(hint, "")

    async def test_task_editor_prevents_empty_save(self) -> None:
        app = ModalHostApp(TaskEditorScreen(None, []))

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()

            self.assertIs(app.result, UNSET)
            self.assertIsInstance(app.screen, TaskEditorScreen)

    async def test_label_editor_ctrl_s_returns_form_data(self) -> None:
        label = make_label("label-1", "alpha", color="red", is_favorite=False)
        app = ModalHostApp(LabelEditorScreen(label))

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#label-editor-name", Input).value = "updated"
            screen.query_one("#label-editor-color", Select).value = "blue"
            screen.query_one("#label-editor-favorite", Checkbox).value = True
            await pilot.press("ctrl+s")
            await pilot.pause()

        self.assertIsInstance(app.result, LabelFormData)
        self.assertEqual(app.result.name, "updated")
        self.assertEqual(app.result.color, "blue")
        self.assertTrue(app.result.is_favorite)

    async def test_label_editor_matches_task_editor_field_treatment(self) -> None:
        label = make_label("label-1", "alpha", color="red", is_favorite=True)
        app = ModalHostApp(LabelEditorScreen(label))

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            name_input = screen.query_one("#label-editor-name", Input)
            color_input = screen.query_one("#label-editor-color", Select)
            favorite_input = screen.query_one("#label-editor-favorite", Checkbox)

            self.assertFalse(name_input.select_on_focus)
            self.assertEqual(name_input.selection, (len(name_input.value), len(name_input.value)))
            self.assertEqual(name_input.border_title, " Name ")
            self.assertEqual(color_input.border_title, " Color ")
            self.assertEqual(favorite_input.border_title, " Options ")

    async def test_label_editor_prevents_blank_name(self) -> None:
        app = ModalHostApp(LabelEditorScreen(None))

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()

            self.assertIs(app.result, UNSET)
            self.assertIsInstance(app.screen, LabelEditorScreen)

    async def test_label_manager_add_flow_returns_create_request(self) -> None:
        app = ModalHostApp(LabelManagerScreen([make_label("label-1", "alpha")]))

        async with app.run_test() as pilot:
            await pilot.pause()
            manager = app.screen
            manager.action_add_label()
            await pilot.pause()
            self.assertIsInstance(app.screen, LabelEditorScreen)
            editor = app.screen
            editor.query_one("#label-editor-name", Input).value = "new-label"
            editor.query_one("#label-editor-color", Select).value = "teal"
            await pilot.press("ctrl+s")
            await pilot.pause()

        self.assertIsInstance(app.result, LabelMutationRequest)
        self.assertEqual(app.result.action, "create")
        self.assertEqual(app.result.form.name, "new-label")
        self.assertEqual(app.result.form.color, "teal")

    async def test_label_manager_edit_flow_returns_update_request(self) -> None:
        labels = [make_label("label-1", "alpha"), make_label("label-2", "beta")]
        app = ModalHostApp(LabelManagerScreen(labels))

        async with app.run_test() as pilot:
            await pilot.pause()
            manager = app.screen
            manager.label_index = 1
            manager.action_edit_label()
            await pilot.pause()
            self.assertIsInstance(app.screen, LabelEditorScreen)
            editor = app.screen
            editor.query_one("#label-editor-name", Input).value = "beta-updated"
            await pilot.press("ctrl+s")
            await pilot.pause()

        self.assertIsInstance(app.result, LabelMutationRequest)
        self.assertEqual(app.result.action, "update")
        self.assertEqual(app.result.label_id, "label-2")
        self.assertEqual(app.result.form.name, "beta-updated")

    async def test_label_manager_delete_flow_returns_delete_request(self) -> None:
        labels = [make_label("label-1", "alpha"), make_label("label-2", "beta")]
        app = ModalHostApp(LabelManagerScreen(labels))

        async with app.run_test() as pilot:
            await pilot.pause()
            manager = app.screen
            manager.label_index = 1
            manager.action_delete_label()
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await pilot.pause()

        self.assertIsInstance(app.result, LabelMutationRequest)
        self.assertEqual(app.result.action, "delete")
        self.assertEqual(app.result.label_id, "label-2")

    async def test_label_manager_matches_task_editor_modal_chrome(self) -> None:
        app = ModalHostApp(LabelManagerScreen([make_label("label-1", "alpha")]))

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            shell = screen.query_one("#label-manager-shell", Container)
            label_list = screen.query_one("#label-manager-list", Static)
            add_button = screen.query_one("#label-manager-add", Button)

            self.assertEqual(shell.border_title, "Manage labels")
            self.assertIsNone(label_list.border_title)
            self.assertIsNone(label_list.border_subtitle)
            self.assertTrue(add_button.compact)

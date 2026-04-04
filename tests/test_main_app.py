from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from textual.widgets import Markdown

from app_types import LabelFormData, LabelMutationRequest, SelectionState, TaskFormData
from main import TodoistGateway, TodoistKanbanApp, parse_args, resolve_token
from screens import ConfirmScreen, LabelManagerScreen, TaskEditorScreen
from tests.support import make_due, make_label, make_snapshot, make_task


class SnapshotPilotApp(TodoistKanbanApp):
    def __init__(self, snapshot) -> None:
        super().__init__("test-token")
        self._snapshot = snapshot
        self.gateway = SimpleNamespace(close=lambda: None)
        self.refresh_requests = 0

    async def on_mount(self) -> None:
        self._refresh_ui()
        await self._apply_snapshot(self._snapshot)

    def load_snapshot(self) -> None:
        self.refresh_requests += 1


class MainHelpersTests(unittest.TestCase):
    def test_parse_args_reads_cli_values(self) -> None:
        args = parse_args(["--token", "abc", "--due-lang", "pl"])

        self.assertEqual(args.token, "abc")
        self.assertEqual(args.due_lang, "pl")

    def test_resolve_token_prefers_cli_token(self) -> None:
        with patch.dict("os.environ", {"TODOIST_API_TOKEN": "env-token"}, clear=True):
            self.assertEqual(resolve_token("cli-token"), "cli-token")

    def test_resolve_token_uses_environment_and_errors_when_missing(self) -> None:
        with patch.dict("os.environ", {"TODOIST_API_TOKEN": "env-token"}, clear=True):
            self.assertEqual(resolve_token(None), "env-token")

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                resolve_token(None)

    def test_gateway_task_kwargs_includes_due_fields_when_present(self) -> None:
        gateway = TodoistGateway.__new__(TodoistGateway)
        gateway.due_lang = "en"

        payload = TodoistGateway._task_kwargs(
            gateway,
            TaskFormData(
                content="Task",
                description="Details",
                labels=["alpha"],
                due_string="tomorrow",
            ),
        )

        self.assertEqual(payload["due_string"], "tomorrow")
        self.assertEqual(payload["due_lang"], "en")

    def test_gateway_update_task_clears_due_when_due_string_removed(self) -> None:
        class StubApi:
            def __init__(self) -> None:
                self.calls = []

            def update_task(self, task_id: str, **payload):
                self.calls.append((task_id, payload))
                return {"task_id": task_id, "payload": payload}

        gateway = TodoistGateway.__new__(TodoistGateway)
        gateway.api = StubApi()
        gateway.due_lang = "en"
        task = make_task("task-1", "Alpha", due=make_due("2026-04-03", "today"))

        result = TodoistGateway.update_task(
            gateway,
            task,
            TaskFormData(
                content="Updated",
                description="Details",
                labels=["alpha"],
                due_string=None,
            ),
        )

        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(gateway.api.calls[0][1]["due_string"], "no due date")
        self.assertEqual(gateway.api.calls[0][1]["due_lang"], "en")

    def test_finish_label_request_dispatches_expected_mutation_runner(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app._run_label_mutation = Mock()
        app._run_simple_mutation = Mock()
        app._refresh_status = Mock()
        app.status = ""

        TodoistKanbanApp._finish_label_request(
            app,
            LabelMutationRequest(action="create", form=LabelFormData("alpha", "blue", False)),
        )
        app._run_label_mutation.assert_called_once()

        app._run_label_mutation.reset_mock()
        TodoistKanbanApp._finish_label_request(
            app,
            LabelMutationRequest(action="update", label_id="label-1", form=LabelFormData("beta", "red", True)),
        )
        app._run_label_mutation.assert_called_once()

        TodoistKanbanApp._finish_label_request(
            app,
            LabelMutationRequest(action="delete", label_id="label-2"),
        )
        app._run_simple_mutation.assert_called_once()

        TodoistKanbanApp._finish_label_request(app, None)
        self.assertEqual(app.status, "Label manager closed.")

    def test_preferred_task_group_key_prefers_current_group_then_form_order(self) -> None:
        alpha = make_label("label-1", "alpha")
        beta = make_label("label-2", "beta")
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app.selection = SelectionState(current_group_key="label:label-2")
        app.label_lookup = {"label-1": alpha, "label-2": beta}
        app.label_name_lookup = {"alpha": "label-1", "beta": "label-2"}

        self.assertEqual(
            TodoistKanbanApp._preferred_task_group_key(app, ["beta", "alpha"]),
            "label:label-2",
        )

        app.selection = SelectionState(current_group_key="unlabeled")
        self.assertEqual(
            TodoistKanbanApp._preferred_task_group_key(app, ["beta", "alpha"]),
            "label:label-2",
        )

    def test_navigation_repeat_gate_rejects_rapid_same_key_only(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app._last_navigation_key = None
        app._last_navigation_time = 0.0

        with patch("main.time.monotonic", side_effect=[1.0, 1.05, 1.06, 1.20]):
            self.assertTrue(TodoistKanbanApp._should_accept_navigation_key(app, "down"))
            self.assertFalse(TodoistKanbanApp._should_accept_navigation_key(app, "down"))
            self.assertTrue(TodoistKanbanApp._should_accept_navigation_key(app, "up"))
            self.assertTrue(TodoistKanbanApp._should_accept_navigation_key(app, "down"))


class MainAppFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_keys_move_between_groups_and_tasks(self) -> None:
        labels = [make_label("label-1", "alpha")]
        snapshot = make_snapshot(
            tasks=[
                make_task("task-1", "Alpha first", labels=["alpha"], order=1),
                make_task("task-2", "Unlabeled", order=2),
                make_task("task-3", "Alpha second", labels=["alpha"], order=3),
            ],
            labels=labels,
        )
        app = SnapshotPilotApp(snapshot)

        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(app.selected_group.key, "all")
            self.assertEqual(app.selected_task.id, "task-1")

            await pilot.press("right")
            self.assertEqual(app.selected_group.key, "unlabeled")
            self.assertEqual(app.selected_task.id, "task-2")

            await pilot.pause(TodoistKanbanApp.NAVIGATION_REPEAT_INTERVAL + 0.02)
            await pilot.press("right")
            self.assertEqual(app.selected_group.key, "label:label-1")
            self.assertEqual(app.selected_task.id, "task-1")

            await pilot.press("down")
            self.assertEqual(app.selected_task.id, "task-3")

    async def test_new_task_binding_opens_editor(self) -> None:
        app = SnapshotPilotApp(make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]))

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()

            self.assertIsInstance(app.screen, TaskEditorScreen)

    async def test_busy_state_blocks_editor_actions(self) -> None:
        app = SnapshotPilotApp(make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]))

        async with app.run_test() as pilot:
            await pilot.pause()
            app.busy = True
            await pilot.press("n")
            await pilot.pause()

            self.assertNotIsInstance(app.screen, TaskEditorScreen)

    async def test_space_and_x_bindings_open_confirm_screens(self) -> None:
        app = SnapshotPilotApp(make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]))

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)

            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)

    async def test_refresh_binding_calls_load_snapshot(self) -> None:
        app = SnapshotPilotApp(make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]))

        async with app.run_test() as pilot:
            await pilot.pause()
            before = app.refresh_requests
            await pilot.press("r")
            self.assertGreater(app.refresh_requests, before)

    async def test_label_manager_action_opens_label_screen(self) -> None:
        app = SnapshotPilotApp(
            make_snapshot(
                tasks=[make_task("task-1", "Alpha", labels=["alpha"])],
                labels=[make_label("label-1", "alpha")],
            )
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_manage_labels()
            await pilot.pause()

            self.assertIsInstance(app.screen, LabelManagerScreen)

    async def test_apply_snapshot_preserves_selected_group_and_task_by_id(self) -> None:
        labels = [make_label("label-1", "alpha")]
        initial_snapshot = make_snapshot(
            tasks=[
                make_task("task-1", "Alpha first", labels=["alpha"], order=1),
                make_task("task-2", "Alpha second", labels=["alpha"], order=2),
            ],
            labels=labels,
        )
        updated_snapshot = make_snapshot(
            tasks=[
                make_task("task-1", "Alpha first", labels=["alpha"], order=1),
                make_task("task-2", "Alpha second", labels=["alpha"], order=2),
                make_task("task-3", "Alpha third", labels=["alpha"], order=3),
            ],
            labels=labels,
        )
        app = SnapshotPilotApp(initial_snapshot)

        async with app.run_test() as pilot:
            await pilot.pause()
            app._select_group_by_key("label:label-1")
            app._select_task_by_id("task-2")
            await app._apply_snapshot(updated_snapshot, selected_task_id="task-2", selected_group_key="label:label-1")

            self.assertEqual(app.selected_group.key, "label:label-1")
            self.assertEqual(app.selected_task.id, "task-2")

    async def test_detail_markdown_widget_receives_task_description(self) -> None:
        app = SnapshotPilotApp(
            make_snapshot(
                tasks=[
                    make_task(
                        "task-1",
                        "Alpha",
                        description="# Heading\n\n- item",
                    )
                ],
                labels=[],
            )
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            markdown = app.query_one("#detail-markdown", Markdown)
            self.assertEqual(markdown._markdown, "# Heading\n\n- item")

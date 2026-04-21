from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import ui_styles as ui
from md_sync import MarkdownNote, SyncAction, SyncPlan, SyncPreview, SyncResolution, TaskPayload, TodoistTaskReplica, load_markdown_notes, load_sync_records
from textual.color import Color
from textual.widgets import Markdown

from app_types import LabelFormData, LabelMutationRequest, SelectionState, TaskFormData
from main import ActivePaneScroll, GroupChipButton, TaskCardWidget, TodoistGateway, TodoistKanbanApp, parse_args, resolve_token
from screens import ConfirmScreen, LabelManagerScreen, SyncPreviewScreen, TaskEditorScreen
from tests.support import make_due, make_label, make_snapshot, make_task


class SnapshotPilotApp(TodoistKanbanApp):
    def __init__(self, snapshot, *, sync_preview: SyncPreview | None = None, sync_error: str | None = None) -> None:
        super().__init__("test-token")
        self._snapshot = snapshot
        self._sync_preview = sync_preview
        self._sync_error = sync_error
        self.gateway = SimpleNamespace(close=lambda: None)
        self.refresh_requests = 0

    async def on_mount(self) -> None:
        self._refresh_ui()
        await self._apply_snapshot(
            self._snapshot,
            sync_preview=self._sync_preview,
            sync_error=self._sync_error,
        )

    def load_snapshot(self) -> None:
        self.refresh_requests += 1


class MainHelpersTests(unittest.TestCase):
    def test_parse_args_reads_cli_values(self) -> None:
        args = parse_args(["--token", "abc", "--due-lang", "pl", "--notes-root", "/tmp/notes"])

        self.assertEqual(args.token, "abc")
        self.assertEqual(args.due_lang, "pl")
        self.assertEqual(args.notes_root, "/tmp/notes")

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

    def test_build_task_panel_uses_task_panel_widget_height(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        selected_group = object()
        app.groups = [selected_group]
        app.selection = SelectionState(task_index=3)
        app._render_task_card = Mock()
        app.query_one = Mock(return_value=SimpleNamespace(size=SimpleNamespace(height=17)))
        app.CARD_TITLE_STYLE = "bold white"
        app.MUTED_TEXT_STYLE = "grey50"
        app.BORDER_STYLE = "red"

        with patch("main.build_task_panel", return_value="panel") as build_task_panel_mock:
            result = TodoistKanbanApp._build_task_panel(app)

        self.assertEqual(result, "panel")
        build_task_panel_mock.assert_called_once_with(
            selected_group,
            3,
            17,
            app._render_task_card,
            title_style="bold white",
            muted_style="grey50",
            border_style="red",
        )

    def test_render_task_card_caches_renderables_by_task_state(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app._task_card_render_cache = {}
        app.label_name_colors = {}
        task = make_task("task-1", "Alpha")
        inactive_card = object()
        active_card = object()

        with patch("main.build_task_card", side_effect=[inactive_card, active_card]) as build_task_card_mock:
            first = TodoistKanbanApp._render_task_card(app, task, selected=False, accent="red")
            second = TodoistKanbanApp._render_task_card(app, task, selected=False, accent="red")
            selected = TodoistKanbanApp._render_task_card(app, task, selected=True, accent="red")

        self.assertIs(first, inactive_card)
        self.assertIs(second, inactive_card)
        self.assertIs(selected, active_card)
        self.assertEqual(build_task_card_mock.call_count, 2)

    def test_group_button_label_uses_navigation_markers(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)

        self.assertEqual(
            TodoistKanbanApp._group_button_label(
                app,
                SimpleNamespace(key="all", title="All Tasks", tasks=[object()]),
            ),
            "◎ All Tasks [1]",
        )
        self.assertEqual(
            TodoistKanbanApp._group_button_label(
                app,
                SimpleNamespace(key="unlabeled", title="No Label", tasks=[]),
            ),
            "○ No Label [0]",
        )
        self.assertEqual(
            TodoistKanbanApp._group_button_label(
                app,
                SimpleNamespace(key="label:1", title="urgent", tasks=[object(), object()]),
            ),
            "● @urgent [2]",
        )

    def test_next_task_scrolls_inspector_when_inspector_pane_is_active(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app.active_pane = "inspector"
        app._scroll_inspector = Mock()
        app._is_main_screen_active = Mock(return_value=True)

        TodoistKanbanApp.action_next_task(app)

        app._scroll_inspector.assert_called_once_with(1)

    def test_finish_sync_preview_dispatches_worker_for_selected_action(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app.run_sync_action = Mock()
        app.run_sync_resolution = Mock()
        app._refresh_status = Mock()
        app.status = ""
        action = SyncAction(kind="create_todoist", sync_id="sync-1", reason="Apply it.")

        TodoistKanbanApp._finish_sync_preview(app, action)

        app.run_sync_action.assert_called_once_with(action)
        app.run_sync_resolution.assert_not_called()

    def test_finish_sync_preview_dispatches_worker_for_resolution_choice(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app.run_sync_action = Mock()
        app.run_sync_resolution = Mock()
        app._refresh_status = Mock()
        app.status = ""
        resolution = SyncResolution(
            conflict=SyncAction(kind="conflict", sync_id="sync-1", reason="Resolve it."),
            winner="markdown",
        )

        TodoistKanbanApp._finish_sync_preview(app, resolution)

        app.run_sync_resolution.assert_called_once_with(resolution)
        app.run_sync_action.assert_not_called()

    def test_apply_sync_action_creates_markdown_note_and_persists_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            notes_root = Path(tmp_dir) / "notes"
            state_path = notes_root / ".todoist-sync-state.json"

            app = TodoistKanbanApp.__new__(TodoistKanbanApp)
            app.sync_root = notes_root
            app.sync_state_path = state_path
            app.gateway = SimpleNamespace()

            action = SyncAction(
                kind="create_markdown",
                sync_id="sync-1",
                reason="Create local note.",
                payload=TaskPayload("Ship feature", "Details", ("work",), "friday"),
                todoist_id="todoist-1",
            )

            message = TodoistKanbanApp._apply_sync_action(app, action)

            self.assertIn("Created markdown note", message)
            notes = load_markdown_notes(notes_root)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].payload.title, "Ship feature")
            self.assertEqual(notes[0].sync_id, "sync-1")
            self.assertEqual(notes[0].todoist_id, "todoist-1")

            records = load_sync_records(state_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].sync_id, "sync-1")
            self.assertEqual(records[0].todoist_id, "todoist-1")

    def test_apply_sync_resolution_keep_markdown_updates_remote_and_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            notes_root = Path(tmp_dir) / "notes"
            state_path = notes_root / ".todoist-sync-state.json"
            note_path = notes_root / "task.md"

            app = TodoistKanbanApp.__new__(TodoistKanbanApp)
            app.sync_root = notes_root
            app.sync_state_path = state_path
            app.gateway = SimpleNamespace(
                update_task_by_id=Mock(return_value=make_task("todoist-1", "Local title", description="Local body"))
            )

            conflict = SyncAction(
                kind="conflict",
                sync_id="sync-1",
                reason="Both replicas changed.",
                markdown_path=note_path,
                todoist_id="todoist-1",
                details={"type": "concurrent-edit"},
                markdown_note=MarkdownNote(
                    path=note_path,
                    payload=TaskPayload("Local title", "Local body", ("alpha",), "tomorrow"),
                    sync_id="sync-1",
                    todoist_id="todoist-1",
                ),
                todoist_task=TodoistTaskReplica(
                    task_id="todoist-1",
                    payload=TaskPayload("Remote title", "Remote body"),
                ),
            )

            message = TodoistKanbanApp._apply_sync_resolution(
                app,
                SyncResolution(conflict=conflict, winner="markdown"),
            )

            self.assertIn("keeping markdown", message)
            app.gateway.update_task_by_id.assert_called_once_with("todoist-1", conflict.markdown_note.payload)
            notes = load_markdown_notes(notes_root)
            self.assertEqual(notes[0].payload.title, "Local title")
            records = load_sync_records(state_path)
            self.assertEqual(records[0].todoist_id, "todoist-1")

    def test_apply_sync_resolution_keep_todoist_rewrites_markdown_and_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            notes_root = Path(tmp_dir) / "notes"
            state_path = notes_root / ".todoist-sync-state.json"
            note_path = notes_root / "task.md"

            app = TodoistKanbanApp.__new__(TodoistKanbanApp)
            app.sync_root = notes_root
            app.sync_state_path = state_path
            app.gateway = SimpleNamespace()

            conflict = SyncAction(
                kind="conflict",
                sync_id="sync-1",
                reason="Both replicas changed.",
                markdown_path=note_path,
                todoist_id="todoist-1",
                details={"type": "concurrent-edit"},
                markdown_note=MarkdownNote(
                    path=note_path,
                    payload=TaskPayload("Local title", "Local body"),
                    sync_id="sync-1",
                    todoist_id="todoist-1",
                ),
                todoist_task=TodoistTaskReplica(
                    task_id="todoist-1",
                    payload=TaskPayload("Remote title", "Remote body", ("beta",), "friday"),
                ),
            )

            message = TodoistKanbanApp._apply_sync_resolution(
                app,
                SyncResolution(conflict=conflict, winner="todoist"),
            )

            self.assertIn("keeping Todoist", message)
            notes = load_markdown_notes(notes_root)
            self.assertEqual(notes[0].payload.title, "Remote title")
            self.assertEqual(notes[0].payload.due, "friday")
            records = load_sync_records(state_path)
            self.assertEqual(records[0].markdown_fingerprint, notes[0].fingerprint)

    def test_apply_sync_actions_runs_each_action_in_order(self) -> None:
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        seen: list[str] = []
        app._apply_sync_action = Mock(side_effect=lambda action: seen.append(action.sync_id) or f"applied:{action.sync_id}")

        actions = (
            SyncAction(kind="create_markdown", sync_id="sync-1", reason="First"),
            SyncAction(kind="update_markdown", sync_id="sync-2", reason="Second"),
        )

        messages = TodoistKanbanApp._apply_sync_actions(app, actions)

        self.assertEqual(messages, ["applied:sync-1", "applied:sync-2"])
        self.assertEqual(seen, ["sync-1", "sync-2"])

    def test_auto_sync_status_mentions_remaining_conflicts(self) -> None:
        preview = SyncPreview(
            notes_root=Path("/notes"),
            state_path=Path("/notes/.todoist-sync-state.json"),
            note_count=1,
            record_count=1,
            plan=SyncPlan(
                conflicts=(SyncAction(kind="conflict", sync_id="sync-1", reason="Resolve me."),),
            ),
        )

        message = TodoistKanbanApp._auto_sync_status(["Applied one."], preview, None)

        self.assertIn("Applied 1 sync action(s) automatically.", message)
        self.assertIn("1 conflict(s) still need manual resolution.", message)


class MainAppFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_converge_sync_applies_actions_until_only_conflicts_remain(self) -> None:
        snapshot = make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[])
        app = TodoistKanbanApp.__new__(TodoistKanbanApp)
        app.gateway = SimpleNamespace(load_snapshot=Mock(side_effect=[snapshot]))
        first_preview = SyncPreview(
            notes_root=Path("/notes"),
            state_path=Path("/notes/.todoist-sync-state.json"),
            note_count=1,
            record_count=0,
            plan=SyncPlan(
                actions=(SyncAction(kind="create_markdown", sync_id="sync-1", reason="Create local copy."),),
            ),
        )
        final_preview = SyncPreview(
            notes_root=Path("/notes"),
            state_path=Path("/notes/.todoist-sync-state.json"),
            note_count=1,
            record_count=1,
            plan=SyncPlan(
                conflicts=(SyncAction(kind="conflict", sync_id="sync-1", reason="Resolve manually."),),
            ),
        )
        app._load_sync_preview = AsyncMock(side_effect=[(first_preview, None), (final_preview, None)])
        app._apply_sync_actions = Mock(return_value=["Created markdown note for 'Alpha'."])

        resolved_snapshot, resolved_preview, resolved_error, messages = await TodoistKanbanApp._converge_sync(app, snapshot)

        self.assertIs(resolved_snapshot, snapshot)
        self.assertIs(resolved_preview, final_preview)
        self.assertIsNone(resolved_error)
        self.assertEqual(messages, ["Created markdown note for 'Alpha'."])
        app._apply_sync_actions.assert_called_once_with(first_preview.plan.actions)

    async def test_tab_navigation_cycles_panes_and_routes_keys(self) -> None:
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
            self.assertEqual(app.active_pane, "tasks")
            self.assertEqual(app.selected_group.key, "all")
            self.assertEqual(app.selected_task.id, "task-1")
            self.assertEqual(app.query_one("#group-rail").border_title, "Labels")
            self.assertEqual(app.query_one("#group-rail").border_subtitle, "")
            self.assertEqual(app.query_one("#detail-panel").border_title, "Inspector")
            self.assertNotIn("ACTIVE", app.query_one("#task-panel").border_subtitle)
            self.assertEqual(app.query_one("#detail-panel").border_subtitle, "")

            await pilot.press("down")
            self.assertEqual(app.selected_task.id, "task-2")

            await pilot.press("left")
            self.assertEqual(app.active_pane, "groups")
            self.assertEqual(app.query_one("#group-rail").border_subtitle, "")

            await pilot.pause(TodoistKanbanApp.NAVIGATION_REPEAT_INTERVAL + 0.02)
            await pilot.press("down")
            self.assertEqual(app.selected_group.key, "unlabeled")
            self.assertEqual(app.selected_task.id, "task-2")

            await pilot.pause(TodoistKanbanApp.NAVIGATION_REPEAT_INTERVAL + 0.02)
            await pilot.press("down")
            self.assertEqual(app.selected_group.key, "label:label-1")
            self.assertEqual(app.selected_task.id, "task-1")

            await pilot.press("right")
            self.assertEqual(app.active_pane, "tasks")
            self.assertEqual(app.query_one("#group-rail").border_subtitle, "")
            self.assertNotIn("ACTIVE", app.query_one("#task-panel").border_subtitle)
            await pilot.press("down")
            self.assertEqual(app.selected_task.id, "task-3")

            await pilot.press("right")
            self.assertEqual(app.active_pane, "inspector")

    async def test_group_navigation_works_with_overflowing_group_strip(self) -> None:
        labels = [make_label(f"label-{index}", f"label-{index}") for index in range(8)]
        snapshot = make_snapshot(
            tasks=[make_task(f"task-{index}", f"Task {index}", labels=[f"label-{index}"], order=index) for index in range(8)],
            labels=labels,
        )
        app = SnapshotPilotApp(snapshot)

        async with app.run_test(size=(70, 24)) as pilot:
            await pilot.pause()
            await pilot.press("left")
            self.assertEqual(app.active_pane, "groups")

            for _ in range(5):
                await pilot.pause(TodoistKanbanApp.NAVIGATION_REPEAT_INTERVAL + 0.02)
                await pilot.press("down")

            self.assertEqual(app.selected_group.key, "label:label-3")

    async def test_group_chip_mouse_down_activates_groups_pane(self) -> None:
        app = SnapshotPilotApp(
            make_snapshot(
                tasks=[make_task("task-1", "Alpha", labels=["alpha"])],
                labels=[make_label("label-1", "alpha")],
            )
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            label_button = app.query_one(GroupChipButton)
            label_button.on_mouse_down(Mock())
            await pilot.pause()

            self.assertEqual(app.active_pane, "groups")
            self.assertEqual(app.selected_group.key, "all")

    async def test_mouse_activation_switches_to_inspector_pane(self) -> None:
        app = SnapshotPilotApp(make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]))

        async with app.run_test() as pilot:
            await pilot.pause()

            inspector_panel = app.query_one("#detail-panel", ActivePaneScroll)
            inspector_panel.on_mouse_down(Mock())

            self.assertEqual(app.active_pane, "inspector")

    async def test_inspector_pane_keeps_task_selection_and_escape_returns_to_tasks(self) -> None:
        description = "\n".join(f"Line {index}" for index in range(80))
        app = SnapshotPilotApp(
            make_snapshot(
                tasks=[
                    make_task("task-1", "Alpha", description=description, order=1),
                    make_task("task-2", "Beta", order=2),
                ],
                labels=[],
            )
        )

        async with app.run_test(size=(70, 14)) as pilot:
            await pilot.pause()

            await pilot.press("tab")
            self.assertEqual(app.active_pane, "inspector")
            self.assertEqual(app.query_one("#detail-panel").border_subtitle, "")

            for _ in range(3):
                await pilot.press("down")
            await pilot.pause()
            self.assertEqual(app.selected_task.id, "task-1")

            await pilot.press("escape")
            self.assertEqual(app.active_pane, "tasks")
            await pilot.press("down")
            self.assertEqual(app.selected_task.id, "task-2")

    async def test_active_pane_uses_heavy_border(self) -> None:
        app = SnapshotPilotApp(make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]))

        async with app.run_test() as pilot:
            await pilot.pause()
            group_rail = app.query_one("#group-rail")
            task_panel = app.query_one("#task-panel")
            detail_panel = app.query_one("#detail-panel")
            self.assertEqual(group_rail.styles.border.top[0], "round")
            self.assertEqual(task_panel.styles.border.top[0], "heavy")
            self.assertEqual(detail_panel.styles.border.top[0], "round")

            await pilot.press("right")
            self.assertEqual(task_panel.styles.border.top[0], "round")
            self.assertEqual(detail_panel.styles.border.top[0], "heavy")

    async def test_task_card_mouse_down_switches_selected_task(self) -> None:
        app = SnapshotPilotApp(
            make_snapshot(
                tasks=[
                    make_task("task-1", "Alpha", order=1),
                    make_task("task-2", "Beta", order=2),
                ],
                labels=[],
            )
        )

        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()

            task_cards = list(app.query(TaskCardWidget))
            task_cards[1].on_mouse_down(Mock())
            await pilot.pause()

            self.assertEqual(app.active_pane, "tasks")
            self.assertEqual(app.selected_task.id, "task-2")

    async def test_task_navigation_reuses_visible_task_widgets(self) -> None:
        app = SnapshotPilotApp(
            make_snapshot(
                tasks=[make_task(f"task-{index}", f"Task {index}", order=index) for index in range(6)],
                labels=[],
            )
        )

        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            initial_cards = list(app.query(TaskCardWidget))
            initial_ids = [id(card) for card in initial_cards]

            await pilot.press("down")
            await pilot.pause()
            updated_cards = list(app.query(TaskCardWidget))

            self.assertEqual(app.selected_task.id, "task-1")
            self.assertEqual([id(card) for card in updated_cards], initial_ids)

    async def test_inactive_panes_use_grayish_border(self) -> None:
        app = SnapshotPilotApp(make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]))

        async with app.run_test() as pilot:
            await pilot.pause()
            group_rail = app.query_one("#group-rail")
            detail_panel = app.query_one("#detail-panel")

            self.assertEqual(group_rail.styles.border.top[1], Color.parse(ui.INACTIVE_TASK_BORDER))
            self.assertEqual(detail_panel.styles.border.top[1], Color.parse(ui.INACTIVE_TASK_BORDER))

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

    async def test_sync_binding_opens_sync_preview_screen(self) -> None:
        preview = SyncPreview(
            notes_root=Path("/notes"),
            state_path=Path("/notes/.todoist-sync-state.json"),
            note_count=1,
            record_count=0,
            plan=SyncPlan(
                actions=(
                    SyncAction(
                        kind="create_todoist",
                        sync_id="sync-1",
                        reason="Create a remote task.",
                    ),
                ),
            ),
        )
        app = SnapshotPilotApp(
            make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]),
            sync_preview=preview,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()

            self.assertIsInstance(app.screen, SyncPreviewScreen)

    async def test_snapshot_status_includes_sync_summary(self) -> None:
        preview = SyncPreview(
            notes_root=Path("/notes"),
            state_path=Path("/notes/.todoist-sync-state.json"),
            note_count=2,
            record_count=1,
            plan=SyncPlan(
                actions=(SyncAction(kind="create_todoist", sync_id="sync-1", reason="Create it."),),
                conflicts=(SyncAction(kind="conflict", sync_id="sync-2", reason="Resolve it."),),
            ),
        )
        app = SnapshotPilotApp(
            make_snapshot(tasks=[make_task("task-1", "Alpha")], labels=[]),
            sync_preview=preview,
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            self.assertIn("Markdown sync preview:", app.status)
            self.assertIn("2 note(s), 1 record(s), 1 action(s), 1 conflict(s)", app.status)

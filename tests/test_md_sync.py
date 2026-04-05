from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from md_sync import (
    BIND,
    CONFLICT,
    build_sync_preview,
    CREATE_MARKDOWN,
    CREATE_TODOIST,
    DELETE_MARKDOWN,
    DELETE_TODOIST,
    default_sync_state_path,
    FRONTMATTER_SYNC_VERSION,
    load_markdown_notes,
    load_sync_records,
    MarkdownNote,
    choose_markdown_note_path,
    render_markdown_note,
    save_sync_records,
    SyncPlanner,
    SyncRecord,
    TaskPayload,
    TodoistTaskReplica,
    UPDATE_MARKDOWN,
    UPDATE_TODOIST,
    write_markdown_note,
)


NOTE_PATH = Path("/notes/task.md")
OTHER_NOTE_PATH = Path("/notes/other.md")


def make_note(
    title: str = "Alpha",
    *,
    body: str = "",
    labels: tuple[str, ...] = (),
    due: str | None = None,
    sync_id: str | None = None,
    todoist_id: str | None = None,
    path: Path = NOTE_PATH,
) -> MarkdownNote:
    return MarkdownNote(
        path=path,
        payload=TaskPayload(title=title, body=body, labels=labels, due=due),
        sync_id=sync_id,
        todoist_id=todoist_id,
    )


def make_task(
    title: str = "Alpha",
    *,
    body: str = "",
    labels: tuple[str, ...] = (),
    due: str | None = None,
    task_id: str = "todoist-1",
) -> TodoistTaskReplica:
    return TodoistTaskReplica(
        task_id=task_id,
        payload=TaskPayload(title=title, body=body, labels=labels, due=due),
    )


class MarkdownTodoistSyncTests(unittest.TestCase):
    def assert_single_action(self, plan, kind: str):
        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].kind, kind)
        self.assertFalse(plan.conflicts)
        return plan.actions[0]

    def assert_single_conflict(self, plan, conflict_type: str):
        self.assertFalse(plan.actions)
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0].kind, CONFLICT)
        self.assertEqual(plan.conflicts[0].details["type"], conflict_type)
        return plan.conflicts[0]

    def test_case_new_markdown_note_creates_todoist_task(self) -> None:
        planner = SyncPlanner(id_factory=lambda: "sync-new")

        plan = planner.plan([make_note(body="Details", labels=("home",), due="tomorrow")], [], [])

        action = self.assert_single_action(plan, CREATE_TODOIST)
        self.assertEqual(action.sync_id, "sync-new")
        self.assertEqual(action.markdown_path, NOTE_PATH)
        self.assertEqual(action.payload.title, "Alpha")

    def test_case_new_todoist_task_creates_markdown_note(self) -> None:
        planner = SyncPlanner(id_factory=lambda: "sync-remote")

        plan = planner.plan([], [make_task(title="Remote task", body="Remote body", labels=("errands",))], [])

        action = self.assert_single_action(plan, CREATE_MARKDOWN)
        self.assertEqual(action.sync_id, "sync-remote")
        self.assertEqual(action.todoist_id, "todoist-1")
        self.assertEqual(action.payload.title, "Remote task")

    def test_case_tracked_pair_unchanged_emits_no_operations(self) -> None:
        note = make_note(sync_id="sync-1", todoist_id="todoist-1")
        task = make_task()
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())

        plan = SyncPlanner().plan([note], [task], [record])

        self.assertFalse(plan.actions)
        self.assertFalse(plan.conflicts)

    def test_case_markdown_only_edit_updates_todoist(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        changed_note = make_note(
            title="Alpha revised",
            body="New details",
            sync_id="sync-1",
            todoist_id="todoist-1",
        )

        plan = SyncPlanner().plan([changed_note], [make_task()], [record])

        action = self.assert_single_action(plan, UPDATE_TODOIST)
        self.assertEqual(action.todoist_id, "todoist-1")
        self.assertEqual(action.payload.title, "Alpha revised")

    def test_case_todoist_only_edit_updates_markdown(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        changed_task = make_task(title="Remote revised", body="Remote details")

        plan = SyncPlanner().plan(
            [make_note(sync_id="sync-1", todoist_id="todoist-1")],
            [changed_task],
            [record],
        )

        action = self.assert_single_action(plan, UPDATE_MARKDOWN)
        self.assertEqual(action.markdown_path, NOTE_PATH)
        self.assertEqual(action.payload.title, "Remote revised")

    def test_case_both_replicas_changed_to_same_payload_emits_no_operation(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        converged_note = make_note(
            title="Shared revision",
            body="Converged body",
            sync_id="sync-1",
            todoist_id="todoist-1",
        )
        converged_task = make_task(title="Shared revision", body="Converged body")

        plan = SyncPlanner().plan([converged_note], [converged_task], [record])

        self.assertFalse(plan.actions)
        self.assertFalse(plan.conflicts)

    def test_case_both_replicas_changed_differently_is_concurrent_edit_conflict(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        changed_note = make_note(
            title="Local revision",
            sync_id="sync-1",
            todoist_id="todoist-1",
        )
        changed_task = make_task(title="Remote revision")

        plan = SyncPlanner().plan([changed_note], [changed_task], [record])

        conflict = self.assert_single_conflict(plan, "concurrent-edit")
        self.assertEqual(conflict.markdown_path, NOTE_PATH)
        self.assertEqual(conflict.todoist_id, "todoist-1")

    def test_case_markdown_survives_without_known_remote_history_creates_todoist(self) -> None:
        note = make_note(sync_id="sync-1")
        record = SyncRecord(sync_id="sync-1", markdown_path=str(NOTE_PATH))

        plan = SyncPlanner().plan([note], [], [record])

        action = self.assert_single_action(plan, CREATE_TODOIST)
        self.assertEqual(action.sync_id, "sync-1")
        self.assertEqual(action.markdown_path, NOTE_PATH)

    def test_case_remote_delete_propagates_to_markdown_when_note_is_unchanged(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        tracked_note = make_note(sync_id="sync-1", todoist_id="todoist-1")

        plan = SyncPlanner().plan([tracked_note], [], [record])

        action = self.assert_single_action(plan, DELETE_MARKDOWN)
        self.assertEqual(action.markdown_path, NOTE_PATH)
        self.assertEqual(action.todoist_id, "todoist-1")

    def test_case_remote_delete_plus_local_edit_requires_manual_resolution(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        changed_note = make_note(
            title="Local revision",
            sync_id="sync-1",
            todoist_id="todoist-1",
        )

        plan = SyncPlanner().plan([changed_note], [], [record])

        conflict = self.assert_single_conflict(plan, "delete-vs-edit")
        self.assertEqual(conflict.details["winner"], "manual")

    def test_case_todoist_survives_without_known_markdown_history_creates_markdown(self) -> None:
        task = make_task(task_id="todoist-1")
        record = SyncRecord(sync_id="sync-1", todoist_id="todoist-1")

        plan = SyncPlanner().plan([], [task], [record])

        action = self.assert_single_action(plan, CREATE_MARKDOWN)
        self.assertEqual(action.sync_id, "sync-1")
        self.assertEqual(action.todoist_id, "todoist-1")

    def test_case_local_delete_propagates_to_todoist_when_task_is_unchanged(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        tracked_task = make_task(task_id="todoist-1")

        plan = SyncPlanner().plan([], [tracked_task], [record])

        action = self.assert_single_action(plan, DELETE_TODOIST)
        self.assertEqual(action.todoist_id, "todoist-1")
        self.assertEqual(action.markdown_path, NOTE_PATH)

    def test_case_local_delete_plus_remote_edit_requires_manual_resolution(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        changed_task = make_task(title="Remote revision")

        plan = SyncPlanner().plan([], [changed_task], [record])

        conflict = self.assert_single_conflict(plan, "delete-vs-edit")
        self.assertEqual(conflict.markdown_path, NOTE_PATH)
        self.assertEqual(conflict.todoist_id, "todoist-1")

    def test_case_bootstrap_match_binds_existing_replicas(self) -> None:
        planner = SyncPlanner(id_factory=lambda: "sync-bootstrap")
        note = make_note(
            body="Body",
            labels=("home",),
            due="tomorrow",
            todoist_id="todoist-1",
        )
        task = make_task(body="Body", labels=("home",), due="tomorrow")

        plan = planner.plan([note], [task], [])

        action = self.assert_single_action(plan, BIND)
        self.assertEqual(action.sync_id, "sync-bootstrap")

    def test_case_bootstrap_mismatch_is_conflict(self) -> None:
        planner = SyncPlanner(id_factory=lambda: "sync-bootstrap")
        note = make_note(title="Local title", todoist_id="todoist-1")
        task = make_task(title="Remote title")

        plan = planner.plan([note], [task], [])

        conflict = self.assert_single_conflict(plan, "bootstrap-content-mismatch")
        self.assertEqual(conflict.todoist_id, "todoist-1")

    def test_case_frontmatter_contains_sync_metadata_and_normalized_fields(self) -> None:
        note = make_note(
            labels=("work", "Home", "work"),
            due="friday",
            sync_id="sync-1",
            todoist_id="todoist-1",
        )

        frontmatter = note.frontmatter()

        self.assertEqual(frontmatter["sync_version"], FRONTMATTER_SYNC_VERSION)
        self.assertEqual(frontmatter["sync_id"], "sync-1")
        self.assertEqual(frontmatter["todoist_id"], "todoist-1")
        self.assertEqual(frontmatter["labels"], ["Home", "work"])
        self.assertEqual(frontmatter["due"], "friday")

    def test_case_payload_fingerprint_normalizes_whitespace_and_label_order(self) -> None:
        left = TaskPayload(
            title=" Alpha \n",
            body="One\r\nTwo\n",
            labels=("work", "Home", "work"),
            due=" friday ",
        )
        right = TaskPayload(
            title="Alpha",
            body="One\nTwo",
            labels=("Home", "work"),
            due="friday",
        )

        self.assertEqual(left.fingerprint, right.fingerprint)

    def test_case_note_with_todoist_id_can_recover_sync_id_from_existing_record(self) -> None:
        record = SyncRecord.capture("sync-1", markdown=make_note(), todoist=make_task())
        recovered_note = make_note(
            title="Alpha revised",
            todoist_id="todoist-1",
            path=OTHER_NOTE_PATH,
        )

        plan = SyncPlanner().plan([recovered_note], [make_task()], [record])

        action = self.assert_single_action(plan, UPDATE_TODOIST)
        self.assertEqual(action.sync_id, "sync-1")
        self.assertEqual(action.markdown_path, OTHER_NOTE_PATH)

    def test_case_load_markdown_notes_parses_frontmatter_heading_and_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            notes_root = Path(tmp_dir)
            note_path = notes_root / "alpha-task.md"
            note_path.write_text(
                "---\n"
                "sync_id: sync-1\n"
                "todoist_id: todoist-1\n"
                "labels:\n"
                "  - work\n"
                "  - Home\n"
                "due: friday\n"
                "---\n"
                "# Ship feature\n\n"
                "Line one\n"
                "Line two\n",
                encoding="utf-8",
            )

            notes = load_markdown_notes(notes_root)

        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note.path, note_path)
        self.assertEqual(note.sync_id, "sync-1")
        self.assertEqual(note.todoist_id, "todoist-1")
        self.assertEqual(note.payload.title, "Ship feature")
        self.assertEqual(note.payload.body, "Line one\nLine two")
        self.assertEqual(note.payload.labels, ("work", "Home"))
        self.assertEqual(note.payload.due, "friday")

    def test_case_build_sync_preview_loads_notes_and_state_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            notes_root = Path(tmp_dir)
            (notes_root / "local-task.md").write_text(
                "---\n"
                "sync_id: sync-1\n"
                "todoist_id: todoist-1\n"
                "---\n"
                "# Local task\n",
                encoding="utf-8",
            )
            state_path = default_sync_state_path(notes_root)
            state_path.write_text(
                '{"records":[{"sync_id":"sync-1","markdown_path":"/notes/local-task.md","todoist_id":"todoist-1","markdown_fingerprint":"old-local","todoist_fingerprint":"old-remote"}]}',
                encoding="utf-8",
            )

            preview = build_sync_preview(
                [make_task(title="Remote task", task_id="todoist-1")],
                notes_root=notes_root,
            )

        self.assertEqual(preview.notes_root, notes_root)
        self.assertEqual(preview.state_path, state_path)
        self.assertEqual(preview.note_count, 1)
        self.assertEqual(preview.record_count, 1)
        self.assertEqual(len(preview.plan.conflicts), 1)
        self.assertEqual(preview.plan.conflicts[0].details["type"], "concurrent-edit")

    def test_case_render_and_write_markdown_note_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            note = make_note(
                title="Ship feature",
                body="Line one\nLine two",
                labels=("work", "Home"),
                due="friday",
                sync_id="sync-1",
                todoist_id="todoist-1",
                path=Path(tmp_dir) / "ship-feature.md",
            )

            rendered = render_markdown_note(note)
            self.assertIn("sync_id: sync-1", rendered)
            self.assertIn("# Ship feature", rendered)

            write_markdown_note(note)
            loaded = load_markdown_notes(Path(tmp_dir))[0]

        self.assertEqual(loaded.payload.title, "Ship feature")
        self.assertEqual(loaded.payload.body, "Line one\nLine two")
        self.assertEqual(loaded.sync_id, "sync-1")
        self.assertEqual(loaded.todoist_id, "todoist-1")

    def test_case_save_and_load_sync_records_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / ".todoist-sync-state.json"
            records = [
                SyncRecord(
                    sync_id="sync-1",
                    markdown_path="/notes/task.md",
                    todoist_id="todoist-1",
                    markdown_fingerprint="local",
                    todoist_fingerprint="remote",
                )
            ]

            save_sync_records(state_path, records)
            loaded = load_sync_records(state_path)

        self.assertEqual(loaded, records)

    def test_case_choose_markdown_note_path_avoids_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            notes_root = Path(tmp_dir)
            (notes_root / "ship-feature.md").write_text("# existing\n", encoding="utf-8")

            selected = choose_markdown_note_path(notes_root, TaskPayload("Ship feature"))

        self.assertEqual(selected.name, "ship-feature-2.md")

    def test_case_duplicate_markdown_sync_ids_raise_error(self) -> None:
        planner = SyncPlanner()

        with self.assertRaisesRegex(ValueError, "Duplicate markdown sync_id: sync-1"):
            planner.plan(
                [
                    make_note(sync_id="sync-1"),
                    make_note(sync_id="sync-1", path=OTHER_NOTE_PATH),
                ],
                [],
                [],
            )

    def test_case_duplicate_todoist_bindings_raise_error(self) -> None:
        planner = SyncPlanner()

        with self.assertRaisesRegex(ValueError, "Duplicate todoist task_id: todoist-1"):
            planner.plan(
                [],
                [
                    make_task(task_id="todoist-1"),
                    make_task(task_id="todoist-1", title="Duplicate"),
                ],
                [SyncRecord(sync_id="sync-1", todoist_id="todoist-1")],
            )


if __name__ == "__main__":
    unittest.main()

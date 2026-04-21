from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4


DEFAULT_MARKDOWN_ROOT = Path("/home/coderbeep/Documents/30-obsidian-marknote/.todo/active")
FRONTMATTER_SYNC_VERSION = 1
SYNC_STATE_FILENAME = ".todoist-sync-state.json"

CREATE_MARKDOWN = "create_markdown"
CREATE_TODOIST = "create_todoist"
UPDATE_MARKDOWN = "update_markdown"
UPDATE_TODOIST = "update_todoist"
DELETE_MARKDOWN = "delete_markdown"
DELETE_TODOIST = "delete_todoist"
BIND = "bind"
CONFLICT = "conflict"


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def _normalize_due(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_labels(labels: Iterable[str]) -> tuple[str, ...]:
    deduped: dict[str, str] = {}
    for label in labels:
        normalized = label.strip()
        if not normalized:
            continue
        deduped.setdefault(normalized.casefold(), normalized)
    return tuple(sorted(deduped.values(), key=str.casefold))


def _fingerprint_payload(payload: "TaskPayload") -> str:
    encoded = json.dumps(payload.normalized(), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskPayload:
    title: str
    body: str = ""
    labels: tuple[str, ...] = ()
    due: str | None = None

    def normalized(self) -> dict[str, object]:
        return {
            "title": _normalize_text(self.title),
            "body": _normalize_text(self.body),
            "labels": list(_normalize_labels(self.labels)),
            "due": _normalize_due(self.due),
        }

    @property
    def fingerprint(self) -> str:
        return _fingerprint_payload(self)


@dataclass(frozen=True, slots=True)
class MarkdownNote:
    path: Path
    payload: TaskPayload
    sync_id: str | None = None
    todoist_id: str | None = None

    @property
    def fingerprint(self) -> str:
        return self.payload.fingerprint

    def frontmatter(self) -> dict[str, object]:
        data: dict[str, object] = {
            "sync_version": FRONTMATTER_SYNC_VERSION,
            "labels": list(_normalize_labels(self.payload.labels)),
        }
        if self.sync_id is not None:
            data["sync_id"] = self.sync_id
        if self.todoist_id is not None:
            data["todoist_id"] = self.todoist_id
        if self.payload.due is not None:
            data["due"] = self.payload.due
        return data


@dataclass(frozen=True, slots=True)
class TodoistTaskReplica:
    task_id: str
    payload: TaskPayload
    updated_at: str | None = None

    @property
    def fingerprint(self) -> str:
        return self.payload.fingerprint


@dataclass(frozen=True, slots=True)
class SyncRecord:
    sync_id: str
    markdown_path: str | None = None
    todoist_id: str | None = None
    markdown_fingerprint: str | None = None
    todoist_fingerprint: str | None = None

    @classmethod
    def capture(
        cls,
        sync_id: str,
        *,
        markdown: MarkdownNote | None = None,
        todoist: TodoistTaskReplica | None = None,
    ) -> "SyncRecord":
        return cls(
            sync_id=sync_id,
            markdown_path=str(markdown.path) if markdown is not None else None,
            todoist_id=todoist.task_id if todoist is not None else None,
            markdown_fingerprint=markdown.fingerprint if markdown is not None else None,
            todoist_fingerprint=todoist.fingerprint if todoist is not None else None,
        )

    @property
    def saw_markdown(self) -> bool:
        return self.markdown_path is not None or self.markdown_fingerprint is not None

    @property
    def saw_todoist(self) -> bool:
        return self.todoist_id is not None or self.todoist_fingerprint is not None


@dataclass(frozen=True, slots=True)
class SyncAction:
    kind: str
    sync_id: str
    reason: str
    payload: TaskPayload | None = None
    markdown_path: Path | None = None
    todoist_id: str | None = None
    details: dict[str, str] = field(default_factory=dict)
    markdown_note: MarkdownNote | None = None
    todoist_task: TodoistTaskReplica | None = None


@dataclass(frozen=True, slots=True)
class SyncResolution:
    conflict: SyncAction
    winner: str


@dataclass(frozen=True, slots=True)
class SyncPlan:
    actions: tuple[SyncAction, ...] = ()
    conflicts: tuple[SyncAction, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncPreview:
    notes_root: Path
    state_path: Path
    note_count: int
    record_count: int
    plan: SyncPlan


class SyncPlanner:
    """Plan convergence between markdown notes and Todoist tasks.

    Treat markdown files and Todoist tasks as two replicas of one logical task.
    The planner requires persisted SyncRecord state to distinguish deletes from
    never-seen objects and deliberately prefers explicit conflicts over unsafe
    automatic merges when both replicas changed since the last sync.
    """

    def __init__(self, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def plan(
        self,
        markdown_notes: Iterable[MarkdownNote],
        todoist_tasks: Iterable[TodoistTaskReplica],
        records: Iterable[SyncRecord],
    ) -> SyncPlan:
        actions: list[SyncAction] = []
        conflicts: list[SyncAction] = []

        records_by_sync = {record.sync_id: record for record in records}
        records_by_todoist_id = {
            record.todoist_id: record for record in records if record.todoist_id is not None
        }

        markdown_by_sync: dict[str, MarkdownNote] = {}
        todoist_by_sync: dict[str, TodoistTaskReplica] = {}
        bootstrap_records: dict[str, SyncRecord] = {}
        bootstrap_sync_by_todoist_id: dict[str, str] = {}
        new_markdown: list[MarkdownNote] = []
        new_todoist: list[TodoistTaskReplica] = []
        seen_todoist_task_ids: set[str] = set()

        for note in markdown_notes:
            sync_id = note.sync_id
            if sync_id is not None:
                self._put_unique(markdown_by_sync, sync_id, note, "markdown sync_id")
                if note.todoist_id is not None:
                    self._put_unique(
                        bootstrap_sync_by_todoist_id,
                        note.todoist_id,
                        sync_id,
                        "markdown todoist_id",
                    )
                continue

            if note.todoist_id is not None and note.todoist_id in records_by_todoist_id:
                sync_id = records_by_todoist_id[note.todoist_id].sync_id
                self._put_unique(markdown_by_sync, sync_id, note, "record todoist_id")
                continue

            if note.todoist_id is not None:
                sync_id = self._id_factory()
                self._put_unique(markdown_by_sync, sync_id, note, "bootstrap sync_id")
                self._put_unique(
                    bootstrap_sync_by_todoist_id,
                    note.todoist_id,
                    sync_id,
                    "bootstrap todoist_id",
                )
                bootstrap_records[sync_id] = SyncRecord(
                    sync_id=sync_id,
                    markdown_path=str(note.path),
                    todoist_id=note.todoist_id,
                )
                continue

            new_markdown.append(note)

        for task in todoist_tasks:
            if task.task_id in seen_todoist_task_ids:
                raise ValueError(f"Duplicate todoist task_id: {task.task_id}")
            seen_todoist_task_ids.add(task.task_id)

            if task.task_id in records_by_todoist_id:
                sync_id = records_by_todoist_id[task.task_id].sync_id
                self._put_unique(todoist_by_sync, sync_id, task, "todoist task_id")
                continue

            if task.task_id in bootstrap_sync_by_todoist_id:
                sync_id = bootstrap_sync_by_todoist_id[task.task_id]
                self._put_unique(todoist_by_sync, sync_id, task, "bootstrap task_id")
                continue

            new_todoist.append(task)

        known_sync_ids = set(records_by_sync)
        known_sync_ids.update(bootstrap_records)
        known_sync_ids.update(markdown_by_sync)
        known_sync_ids.update(todoist_by_sync)

        for sync_id in sorted(known_sync_ids):
            record = records_by_sync.get(sync_id) or bootstrap_records.get(sync_id) or SyncRecord(sync_id=sync_id)
            note = markdown_by_sync.get(sync_id)
            task = todoist_by_sync.get(sync_id)
            planned_actions, planned_conflicts = self._plan_known_pair(record, note, task)
            actions.extend(planned_actions)
            conflicts.extend(planned_conflicts)

        for note in new_markdown:
            sync_id = note.sync_id or self._id_factory()
            actions.append(
                SyncAction(
                    kind=CREATE_TODOIST,
                    sync_id=sync_id,
                    payload=note.payload,
                    markdown_path=note.path,
                    reason="Markdown note has no known remote binding and should create a Todoist task.",
                )
            )

        for task in new_todoist:
            actions.append(
                SyncAction(
                    kind=CREATE_MARKDOWN,
                    sync_id=self._id_factory(),
                    payload=task.payload,
                    todoist_id=task.task_id,
                    reason="Todoist task has no known local binding and should create a markdown note.",
                )
            )

        return SyncPlan(actions=tuple(actions), conflicts=tuple(conflicts))

    def _plan_known_pair(
        self,
        record: SyncRecord,
        note: MarkdownNote | None,
        task: TodoistTaskReplica | None,
    ) -> tuple[list[SyncAction], list[SyncAction]]:
        actions: list[SyncAction] = []
        conflicts: list[SyncAction] = []

        if note is not None and task is not None:
            if record.markdown_fingerprint is None and record.todoist_fingerprint is None:
                if note.fingerprint == task.fingerprint:
                    actions.append(
                        SyncAction(
                            kind=BIND,
                            sync_id=record.sync_id,
                            markdown_path=note.path,
                            todoist_id=task.task_id,
                            reason="Both replicas already exist with matching content and can be bound without rewriting either side.",
                        )
                    )
                else:
                    conflicts.append(
                        SyncAction(
                            kind=CONFLICT,
                            sync_id=record.sync_id,
                            markdown_path=note.path,
                            todoist_id=task.task_id,
                            reason="Both replicas exist before the first sync, but their content differs.",
                            details={"type": "bootstrap-content-mismatch"},
                            markdown_note=note,
                            todoist_task=task,
                        )
                    )
                return actions, conflicts

            note_changed = note.fingerprint != record.markdown_fingerprint
            task_changed = task.fingerprint != record.todoist_fingerprint

            if note_changed and not task_changed:
                actions.append(
                    SyncAction(
                        kind=UPDATE_TODOIST,
                        sync_id=record.sync_id,
                        payload=note.payload,
                        markdown_path=note.path,
                        todoist_id=task.task_id,
                        reason="Markdown changed since the last sync while Todoist stayed unchanged.",
                    )
                )
                return actions, conflicts

            if task_changed and not note_changed:
                actions.append(
                    SyncAction(
                        kind=UPDATE_MARKDOWN,
                        sync_id=record.sync_id,
                        payload=task.payload,
                        markdown_path=note.path,
                        todoist_id=task.task_id,
                        reason="Todoist changed since the last sync while markdown stayed unchanged.",
                    )
                )
                return actions, conflicts

            if note_changed and task_changed and note.fingerprint != task.fingerprint:
                conflicts.append(
                    SyncAction(
                        kind=CONFLICT,
                        sync_id=record.sync_id,
                        markdown_path=note.path,
                        todoist_id=task.task_id,
                        reason="Both replicas changed since the last sync and now disagree.",
                        details={"type": "concurrent-edit"},
                        markdown_note=note,
                        todoist_task=task,
                    )
                )

            return actions, conflicts

        if note is not None:
            if not record.saw_todoist:
                actions.append(
                    SyncAction(
                        kind=CREATE_TODOIST,
                        sync_id=record.sync_id,
                        payload=note.payload,
                        markdown_path=note.path,
                        reason="Markdown replica exists, but no Todoist task has ever been tracked for this item.",
                    )
                )
                return actions, conflicts

            note_changed = note.fingerprint != record.markdown_fingerprint
            if note_changed:
                conflicts.append(
                    SyncAction(
                        kind=CONFLICT,
                        sync_id=record.sync_id,
                        markdown_path=note.path,
                        todoist_id=record.todoist_id,
                        reason="Todoist disappeared while markdown changed locally after the last sync.",
                        details={"type": "delete-vs-edit", "winner": "manual"},
                        markdown_note=note,
                    )
                )
            else:
                actions.append(
                    SyncAction(
                        kind=DELETE_MARKDOWN,
                        sync_id=record.sync_id,
                        markdown_path=note.path,
                        todoist_id=record.todoist_id,
                        reason="Todoist disappeared and markdown still matches the last synced state.",
                    )
                )
            return actions, conflicts

        if task is not None:
            if not record.saw_markdown:
                actions.append(
                    SyncAction(
                        kind=CREATE_MARKDOWN,
                        sync_id=record.sync_id,
                        payload=task.payload,
                        todoist_id=task.task_id,
                        reason="Todoist replica exists, but no markdown note has ever been tracked for this item.",
                    )
                )
                return actions, conflicts

            task_changed = task.fingerprint != record.todoist_fingerprint
            if task_changed:
                conflicts.append(
                    SyncAction(
                        kind=CONFLICT,
                        sync_id=record.sync_id,
                        markdown_path=Path(record.markdown_path) if record.markdown_path is not None else None,
                        todoist_id=task.task_id,
                        reason="Markdown disappeared while Todoist changed remotely after the last sync.",
                        details={"type": "delete-vs-edit", "winner": "manual"},
                        todoist_task=task,
                    )
                )
            else:
                actions.append(
                    SyncAction(
                        kind=DELETE_TODOIST,
                        sync_id=record.sync_id,
                        markdown_path=Path(record.markdown_path) if record.markdown_path is not None else None,
                        todoist_id=task.task_id,
                        reason="Markdown disappeared and Todoist still matches the last synced state.",
                    )
                )
            return actions, conflicts

        return actions, conflicts

    @staticmethod
    def _put_unique(target: dict[str, object], key: str, value: object, label: str) -> None:
        if key in target:
            raise ValueError(f"Duplicate {label}: {key}")
        target[key] = value


def default_sync_state_path(notes_root: Path) -> Path:
    return notes_root / SYNC_STATE_FILENAME


def todoist_task_to_replica(task: object) -> TodoistTaskReplica:
    if isinstance(task, TodoistTaskReplica):
        return task

    due = getattr(task, "due", None)
    due_value = None
    if due is not None:
        due_value = getattr(due, "string", None) or getattr(due, "date", None)

    updated_at = getattr(task, "updated_at", None)
    return TodoistTaskReplica(
        task_id=str(getattr(task, "id")),
        payload=TaskPayload(
            title=str(getattr(task, "content")),
            body=str(getattr(task, "description", "") or ""),
            labels=tuple(getattr(task, "labels", None) or ()),
            due=str(due_value) if due_value is not None else None,
        ),
        updated_at=str(updated_at) if updated_at is not None else None,
    )


def read_markdown_note(path: Path) -> MarkdownNote:
    raw_text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw_text)
    title, note_body = _extract_note_title_and_body(path, frontmatter, body)

    raw_labels = frontmatter.get("labels", ())
    if isinstance(raw_labels, str):
        labels = tuple(label.strip() for label in raw_labels.split(",") if label.strip())
    elif isinstance(raw_labels, list):
        labels = tuple(str(label).strip() for label in raw_labels if str(label).strip())
    else:
        labels = ()

    due = frontmatter.get("due")
    sync_id = frontmatter.get("sync_id")
    todoist_id = frontmatter.get("todoist_id")

    return MarkdownNote(
        path=path,
        payload=TaskPayload(
            title=title,
            body=note_body,
            labels=labels,
            due=str(due).strip() if due not in (None, "") else None,
        ),
        sync_id=str(sync_id).strip() if sync_id not in (None, "") else None,
        todoist_id=str(todoist_id).strip() if todoist_id not in (None, "") else None,
    )


def load_markdown_notes(notes_root: Path) -> list[MarkdownNote]:
    if not notes_root.exists():
        raise FileNotFoundError(f"Markdown notes root does not exist: {notes_root}")
    if not notes_root.is_dir():
        raise NotADirectoryError(f"Markdown notes root is not a directory: {notes_root}")

    notes: list[MarkdownNote] = []
    for path in sorted(notes_root.rglob("*.md")):
        notes.append(read_markdown_note(path))
    return notes


def load_sync_records(state_path: Path) -> list[SyncRecord]:
    if not state_path.exists():
        return []

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    raw_records = payload.get("records", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_records, list):
        raise ValueError(f"Sync state file has invalid record payload: {state_path}")

    records: list[SyncRecord] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict) or "sync_id" not in raw_record:
            raise ValueError(f"Sync state file contains an invalid record: {state_path}")
        records.append(
            SyncRecord(
                sync_id=str(raw_record["sync_id"]),
                markdown_path=_optional_string(raw_record.get("markdown_path")),
                todoist_id=_optional_string(raw_record.get("todoist_id")),
                markdown_fingerprint=_optional_string(raw_record.get("markdown_fingerprint")),
                todoist_fingerprint=_optional_string(raw_record.get("todoist_fingerprint")),
            )
        )
    return records


def save_sync_records(state_path: Path, records: Iterable[SyncRecord]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "records": [
            {
                "sync_id": record.sync_id,
                "markdown_path": record.markdown_path,
                "todoist_id": record.todoist_id,
                "markdown_fingerprint": record.markdown_fingerprint,
                "todoist_fingerprint": record.todoist_fingerprint,
            }
            for record in records
        ]
    }
    state_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_markdown_note(note: MarkdownNote) -> None:
    note.path.parent.mkdir(parents=True, exist_ok=True)
    note.path.write_text(render_markdown_note(note), encoding="utf-8")


def render_markdown_note(note: MarkdownNote) -> str:
    frontmatter = note.frontmatter()
    lines = ["---"]
    for key in ("sync_version", "sync_id", "todoist_id", "labels", "due"):
        if key not in frontmatter:
            continue
        value = frontmatter[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_format_scalar(item)}")
            continue
        lines.append(f"{key}: {_format_scalar(value)}")

    lines.extend(
        [
            "---",
            f"# {note.payload.title.strip()}",
        ]
    )
    body = note.payload.body.strip()
    if body:
        lines.extend(["", body])
    return "\n".join(lines).rstrip() + "\n"


def choose_markdown_note_path(
    notes_root: Path,
    payload: TaskPayload,
    *,
    existing_paths: Iterable[Path] = (),
) -> Path:
    notes_root.mkdir(parents=True, exist_ok=True)
    reserved = {path.resolve() for path in existing_paths}
    base_name = _slugify(payload.title) or "task"
    candidate = notes_root / f"{base_name}.md"
    index = 2

    while candidate.exists() or candidate.resolve() in reserved:
        candidate = notes_root / f"{base_name}-{index}.md"
        index += 1
    return candidate


def upsert_sync_record(records: Iterable[SyncRecord], record: SyncRecord) -> list[SyncRecord]:
    updated: list[SyncRecord] = []
    replaced = False
    for current in records:
        if current.sync_id == record.sync_id:
            updated.append(record)
            replaced = True
            continue
        updated.append(current)
    if not replaced:
        updated.append(record)
    return sorted(updated, key=lambda item: item.sync_id)


def remove_sync_record(records: Iterable[SyncRecord], sync_id: str) -> list[SyncRecord]:
    return [record for record in records if record.sync_id != sync_id]


def build_sync_preview(
    todoist_tasks: Iterable[object],
    *,
    notes_root: Path,
    state_path: Path | None = None,
    planner: SyncPlanner | None = None,
) -> SyncPreview:
    resolved_notes_root = Path(notes_root)
    resolved_state_path = Path(state_path) if state_path is not None else default_sync_state_path(resolved_notes_root)
    notes = load_markdown_notes(resolved_notes_root)
    records = load_sync_records(resolved_state_path)
    replicas = [todoist_task_to_replica(task) for task in todoist_tasks]
    plan = (planner or SyncPlanner()).plan(notes, replicas, records)
    return SyncPreview(
        notes_root=resolved_notes_root,
        state_path=resolved_state_path,
        note_count=len(notes),
        record_count=len(records),
        plan=plan,
    )


def summarize_sync_preview(preview: SyncPreview) -> str:
    return (
        f"{preview.note_count} note(s), "
        f"{preview.record_count} record(s), "
        f"{len(preview.plan.actions)} action(s), "
        f"{len(preview.plan.conflicts)} conflict(s)"
    )


def _split_frontmatter(raw_text: str) -> tuple[dict[str, object], str]:
    normalized = raw_text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return ({}, normalized.strip())

    lines = normalized.split("\n")
    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return ({}, normalized.strip())

    frontmatter_text = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1:]).strip()
    return (_parse_frontmatter(frontmatter_text), body)


def _parse_frontmatter(raw_text: str) -> dict[str, object]:
    data: dict[str, object] = {}
    current_list_key: str | None = None

    for raw_line in raw_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if current_list_key is not None and stripped.startswith("- "):
            assert isinstance(data[current_list_key], list)
            data[current_list_key].append(_parse_scalar(stripped[2:].strip()))
            continue

        current_list_key = None
        if ":" not in raw_line:
            continue

        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()

        if value == "":
            data[key] = []
            current_list_key = key
            continue

        if value.startswith("[") and value.endswith("]"):
            data[key] = _parse_inline_list(value)
            continue

        data[key] = _parse_scalar(value)

    return data


def _parse_inline_list(raw_value: str) -> list[object]:
    inner = raw_value[1:-1].strip()
    if not inner:
        return []
    return [_parse_scalar(part.strip()) for part in inner.split(",")]


def _parse_scalar(raw_value: str) -> object:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    lowered = value.casefold()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def _extract_note_title_and_body(
    path: Path,
    frontmatter: dict[str, object],
    body: str,
) -> tuple[str, str]:
    explicit_title = frontmatter.get("title") or frontmatter.get("content")
    if isinstance(explicit_title, str) and explicit_title.strip():
        return (explicit_title.strip(), body.strip())

    body_lines = body.splitlines()
    for index, raw_line in enumerate(body_lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            return (stripped[2:].strip(), "\n".join(body_lines[index + 1:]).strip())
        break

    derived_title = path.stem.replace("_", " ").replace("-", " ").strip()
    return (derived_title or path.stem, body.strip())


def _optional_string(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _slugify(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "-"
        for character in value.strip()
    )
    parts = [part for part in normalized.split("-") if part]
    return "-".join(parts)


def _format_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)

    text = str(value)
    if text == "" or any(character in text for character in [":", "#", "[", "]", "{", "}", ","]):
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text

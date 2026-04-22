from __future__ import annotations

import unittest

import httpx

from app_utils import (
    build_label_groups,
    compact_multiline_text,
    compact_text,
    flatten_pages,
    format_error,
    humanize_color_name,
    parse_label_names,
    priority_sorted_tasks,
    task_has_priority,
    task_window,
    todoist_priority_level,
)
from tests.support import make_label, make_task


class AppUtilsTests(unittest.TestCase):
    def test_flatten_pages_flattens_nested_lists(self) -> None:
        self.assertEqual(flatten_pages([[1, 2], [3], []]), [1, 2, 3])

    def test_parse_label_names_splits_newlines_and_dedupes_case(self) -> None:
        raw = " Alpha,\nbeta, alpha , ,Beta"
        self.assertEqual(parse_label_names(raw), ["Alpha", "beta"])

    def test_humanize_color_name_title_cases_words(self) -> None:
        self.assertEqual(humanize_color_name("olive_green"), "Olive Green")

    def test_compact_text_leaves_short_content_untouched(self) -> None:
        self.assertEqual(compact_text("short text", limit=20), "short text")

    def test_compact_text_truncates_long_content(self) -> None:
        compact = compact_text("alpha beta gamma delta epsilon", limit=12)
        self.assertEqual(compact, "alpha beta...")

    def test_compact_multiline_text_limits_lines_and_marks_remaining_content(self) -> None:
        compact = compact_multiline_text("first line\nsecond line\nthird line", max_lines=2)
        self.assertEqual(compact, "first line\nsecond line...")

    def test_compact_multiline_text_truncates_long_line(self) -> None:
        compact = compact_multiline_text("alpha beta gamma delta", line_limit=12, max_lines=1)
        self.assertEqual(compact, "alpha beta...")

    def test_format_error_uses_http_json_payload_when_available(self) -> None:
        request = httpx.Request("GET", "https://example.com/tasks")
        response = httpx.Response(400, request=request, json={"detail": "Bad request"})
        error = httpx.HTTPStatusError("boom", request=request, response=response)

        formatted = format_error(error)

        self.assertIn("400 Bad Request", formatted)
        self.assertIn("Bad request", formatted)

    def test_format_error_falls_back_to_http_text_payload(self) -> None:
        request = httpx.Request("GET", "https://example.com/tasks")
        response = httpx.Response(502, request=request, content=b"gateway down")
        error = httpx.HTTPStatusError("boom", request=request, response=response)

        formatted = format_error(error)

        self.assertIn("502 Bad Gateway", formatted)
        self.assertIn("gateway down", formatted)

    def test_format_error_compacts_plain_exceptions(self) -> None:
        formatted = format_error(RuntimeError("plain failure"))
        self.assertEqual(formatted, "plain failure")

    def test_task_window_returns_empty_for_invalid_capacity(self) -> None:
        self.assertEqual(task_window(5, 2, 0), (0, 0))

    def test_task_window_keeps_selection_near_end_in_view(self) -> None:
        self.assertEqual(task_window(8, 6, 3), (5, 8))

    def test_todoist_priority_helpers_map_api_values_to_ui_levels(self) -> None:
        self.assertFalse(task_has_priority(make_task("task-1", "Normal", priority=1)))
        self.assertTrue(task_has_priority(make_task("task-2", "Flagged", priority=4)))
        self.assertEqual(todoist_priority_level(4), 1)
        self.assertEqual(todoist_priority_level(1), 4)

    def test_priority_sorted_tasks_keeps_priority_tasks_first_stably(self) -> None:
        tasks = [
            make_task("task-1", "Normal first", priority=1),
            make_task("task-2", "Flagged first", priority=4),
            make_task("task-3", "Normal second", priority=1),
            make_task("task-4", "Flagged second", priority=4),
        ]

        ordered = priority_sorted_tasks(tasks)

        self.assertEqual([task.id for task in ordered], ["task-2", "task-4", "task-1", "task-3"])

    def test_build_label_groups_creates_special_groups_and_label_groups(self) -> None:
        tasks = [
            make_task("task-1", "Alpha", labels=["urgent"]),
            make_task("task-2", "Beta"),
            make_task("task-3", "Gamma", labels=["Urgent", "home"]),
        ]
        labels = [
            make_label("label-1", "urgent", color="red"),
            make_label("label-2", "home", color="blue"),
        ]

        groups = build_label_groups(tasks, labels)

        group_keys = [group.key for group in groups]
        self.assertEqual(group_keys, ["all", "unlabeled", "label:label-1", "label:label-2"])
        urgent_group = next(group for group in groups if group.key == "label:label-1")
        home_group = next(group for group in groups if group.key == "label:label-2")
        self.assertEqual([task.id for task in urgent_group.tasks], ["task-1", "task-3"])
        self.assertEqual([task.id for task in home_group.tasks], ["task-3"])

    def test_build_label_groups_orders_priority_tasks_first_in_each_group(self) -> None:
        tasks = [
            make_task("task-1", "Normal urgent", labels=["urgent"], priority=1),
            make_task("task-2", "Flagged unlabeled", priority=4),
            make_task("task-3", "Flagged urgent", labels=["urgent"], priority=4),
            make_task("task-4", "Normal unlabeled", priority=1),
        ]
        labels = [make_label("label-1", "urgent")]

        groups = build_label_groups(tasks, labels)

        all_group = next(group for group in groups if group.key == "all")
        unlabeled_group = next(group for group in groups if group.key == "unlabeled")
        urgent_group = next(group for group in groups if group.key == "label:label-1")
        self.assertEqual([task.id for task in all_group.tasks], ["task-2", "task-3", "task-1", "task-4"])
        self.assertEqual([task.id for task in unlabeled_group.tasks], ["task-2", "task-4"])
        self.assertEqual([task.id for task in urgent_group.tasks], ["task-3", "task-1"])

from __future__ import annotations

import unittest
from datetime import date

from rich.panel import Panel
from rich.text import Text

import ui_styles as ui
from views import (
    build_calendar_widget,
    build_detail_markdown,
    build_detail_panel,
    build_label_manager_rows,
    build_status_bar,
    build_task_card,
    build_task_panel,
    build_workspace_header,
    group_label,
)
from tests.support import make_due, make_group, make_label, make_task, render_text


class ViewsTests(unittest.TestCase):
    def test_group_label_formats_special_and_named_groups(self) -> None:
        all_group = make_group("all", "All Tasks", tasks=[make_task("1", "One")])
        unlabeled_group = make_group("unlabeled", "No Label", tasks=[])
        named_group = make_group("label:1", "urgent", tasks=[make_task("2", "Two")])

        self.assertEqual(group_label(all_group), "All Tasks [1]")
        self.assertEqual(group_label(unlabeled_group), "No Label [0]")
        self.assertEqual(group_label(named_group), "@urgent [1]")

    def test_build_workspace_header_renders_counts(self) -> None:
        text = render_text(build_workspace_header("Inbox", 3, 2, busy=False))

        self.assertIn("TODOIST KANBAN", text)
        self.assertIn("INBOX", text)
        self.assertIn("3 TASKS  2 LABELS", text)

    def test_build_workspace_header_renders_syncing_state(self) -> None:
        text = render_text(build_workspace_header("Inbox", 3, 2, busy=True))
        self.assertIn("SYNCING", text)

    def test_build_calendar_widget_uses_due_date_month(self) -> None:
        panel = build_calendar_widget(make_task("task-1", "Alpha", due=make_due("2026-12-05", "Dec 5")))

        self.assertIsInstance(panel, Panel)
        self.assertEqual(panel.title, "DECEMBER 2026")
        self.assertEqual(panel.border_style, ui.INACTIVE_TASK_BORDER)

    def test_build_calendar_widget_falls_back_to_current_month_for_invalid_due(self) -> None:
        panel = build_calendar_widget(make_task("task-1", "Alpha", due=make_due("not-a-date", "someday")))
        self.assertEqual(panel.title, date.today().strftime("%B %Y").upper())

    def test_build_label_manager_rows_shows_empty_state(self) -> None:
        text = render_text(build_label_manager_rows([], 0))
        self.assertIn("No labels yet. Press a to create one.", text)

    def test_build_label_manager_rows_marks_selected_label(self) -> None:
        labels = [make_label("label-1", "urgent"), make_label("label-2", "home")]

        text = render_text(build_label_manager_rows(labels, 1))

        self.assertIn("urgent", text)
        self.assertIn("home", text)
        self.assertIn("ACTIVE", text)

    def test_build_label_manager_rows_keeps_borders_neutral(self) -> None:
        rows = build_label_manager_rows(
            [make_label("label-1", "urgent", color="red"), make_label("label-2", "home", color="blue")],
            0,
        )
        active_panel = rows.renderables[0]
        inactive_panel = rows.renderables[1]

        self.assertEqual(active_panel.border_style, ui.ACCENT_PRIMARY)
        self.assertEqual(str(active_panel.style), f"on {ui.ACTIVE_ROW_BG}")
        self.assertEqual(inactive_panel.border_style, ui.INACTIVE_TASK_BORDER)

    def test_build_task_panel_shows_empty_state(self) -> None:
        group = make_group("all", "All Tasks", tasks=[])

        text = render_text(
            build_task_panel(
                group,
                0,
                20,
                lambda task, *, selected, accent: Text(task.id),
                title_style="bold white",
                muted_style="grey50",
                border_style="red",
            )
        )

        self.assertIn("No tasks in this group.", text)
        self.assertIn("Press n to add a new Inbox task", text)

    def test_build_task_panel_shows_overflow_markers(self) -> None:
        tasks = [make_task(f"task-{index}", f"Task {index}") for index in range(6)]
        group = make_group("all", "All Tasks", tasks=tasks)

        text = render_text(
            build_task_panel(
                group,
                3,
                20,
                lambda task, *, selected, accent: Text(f"{task.id}:{selected}:{accent}"),
                title_style="bold white",
                muted_style="grey50",
                border_style="red",
            )
        )

        self.assertIn("2 earlier tasks above", text)
        self.assertIn("1 more task below", text)
        self.assertIn("task-3:True", text)

    def test_build_task_panel_fills_more_of_tall_viewports(self) -> None:
        tasks = [make_task(f"task-{index}", f"Task {index}") for index in range(8)]
        group = make_group("all", "All Tasks", tasks=tasks)

        text = render_text(
            build_task_panel(
                group,
                0,
                30,
                lambda task, *, selected, accent: Text(task.id),
                title_style="bold white",
                muted_style="grey50",
                border_style="red",
            )
        )

        self.assertIn("task-5", text)
        self.assertIn("2 more tasks below", text)

    def test_build_task_card_shows_due_labels_and_overflow_count(self) -> None:
        task = make_task(
            "task-1",
            "Alpha task",
            description="Detailed description",
            labels=["alpha", "beta", "gamma", "delta"],
            due=make_due("2026-04-08", "next wednesday"),
            priority=4,
        )

        panel = build_task_card(
            task,
            selected=True,
            accent=ui.ACCENT_PRIMARY,
            label_name_colors={
                "alpha": ui.ACCENT_PRIMARY,
                "beta": ui.ACCENT_SECONDARY,
                "gamma": ui.ACCENT_SOFT,
                "delta": ui.ACCENT_BORDER,
            },
            selected_text_style="bold white",
            body_text_style="white",
            subtle_text_style="grey70",
            border_style="grey50",
        )

        text = render_text(panel)

        self.assertEqual(panel.title, "ACTIVE")
        self.assertEqual(panel.border_style, ui.ACTIVE_TASK_BORDER)
        self.assertIn("⚑", text)
        self.assertIn("Alpha task", text)
        self.assertNotIn("⚑ Alpha task", text)
        self.assertNotIn("P1", text)
        self.assertIn("next wednesday", text)
        self.assertIn("@alpha", text)
        self.assertIn("+1", text)
        self.assertIn("Detailed description", text)
        self.assertNotIn("task-1", text)

    def test_build_task_card_marks_unselected_priority_task_in_frame(self) -> None:
        panel = build_task_card(
            make_task("task-1", "Alpha task", priority=4),
            selected=False,
            accent=ui.ACCENT_PRIMARY,
            label_name_colors={},
            selected_text_style="bold white",
            body_text_style="white",
            subtle_text_style="grey70",
            border_style="grey50",
        )

        self.assertEqual(panel.title, "⚑ PRIORITY")
        self.assertEqual(panel.border_style, ui.ACTIVE_TASK_BORDER)

    def test_build_detail_panel_without_task_shows_empty_state(self) -> None:
        text = render_text(
            build_detail_panel(
                None,
                make_group("all", "All Tasks"),
                title_style="bold white",
                muted_style="grey50",
                border_style="red",
            )
        )

        self.assertIn("No task selected.", text)
        self.assertIn("Pick a label group with h/l", text)

    def test_build_detail_panel_with_task_shows_fields(self) -> None:
        task = make_task(
            "task-1",
            "Alpha task",
            description="Full description",
            labels=["alpha", "beta"],
            due=make_due("2026-04-08", "next wednesday"),
        )
        text = render_text(
            build_detail_panel(
                task,
                make_group("label:1", "urgent"),
                title_style="bold white",
                muted_style="grey50",
                border_style="red",
            )
        )

        self.assertIn("INSPECTOR", text)
        self.assertIn("Alpha task", text)
        self.assertIn("none", text)
        self.assertIn("next wednesday", text)
        self.assertIn("alpha, beta", text)
        self.assertIn("DESCRIPTION", text)
        self.assertIn("Rendered below as Markdown.", text)

    def test_build_detail_markdown_returns_description_or_placeholder(self) -> None:
        described_task = make_task("task-1", "Alpha task", description="# Heading\n\n- item")
        empty_task = make_task("task-2", "Beta task", description="   ")

        self.assertEqual(build_detail_markdown(described_task), "# Heading\n\n- item")
        self.assertEqual(build_detail_markdown(empty_task), "_No description._")
        self.assertEqual(build_detail_markdown(None), "")

    def test_build_status_bar_returns_text_message(self) -> None:
        status = build_status_bar("Ready", busy=False, body_text_style=ui.TEXT_DEFAULT)

        self.assertIsInstance(status, Text)
        self.assertEqual(status.plain, "Ready")

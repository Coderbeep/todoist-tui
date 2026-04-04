from __future__ import annotations

import unittest

import ui_styles as ui


class UiStylesTests(unittest.TestCase):
    def test_input_cursor_uses_block_style(self) -> None:
        self.assertEqual(ui.APP_THEME.variables["input-cursor-background"], ui.ACCENT_PRIMARY)
        self.assertEqual(ui.APP_THEME.variables["input-cursor-foreground"], "#221a17")
        self.assertNotIn("input-cursor-text-style", ui.APP_THEME.variables)

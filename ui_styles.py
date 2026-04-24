from __future__ import annotations

from textual.theme import Theme

TODOIST_COLORS: tuple[tuple[str, str], ...] = (
    ("berry_red", "#B8255F"),
    ("red", "#DC4C3E"),
    ("orange", "#C77100"),
    ("yellow", "#B29104"),
    ("olive_green", "#949C31"),
    ("lime_green", "#65A33A"),
    ("green", "#369307"),
    ("mint_green", "#42A393"),
    ("teal", "#148FAD"),
    ("sky_blue", "#319DC0"),
    ("light_blue", "#6988A4"),
    ("blue", "#4180FF"),
    ("grape", "#692EC2"),
    ("violet", "#CA3FEE"),
    ("lavender", "#A4698C"),
    ("magenta", "#E05095"),
    ("salmon", "#C9766F"),
    ("charcoal", "#808080"),
    ("grey", "#999999"),
    ("taupe", "#8F7A69"),
)

COLOR_HEX_BY_NAME = {name: hex_value for name, hex_value in TODOIST_COLORS}
COLOR_SELECT_OPTIONS = [
    (f"{name.replace('_', ' ').title()} ({hex_value})", name)
    for name, hex_value in TODOIST_COLORS
]

APP_BG = "#282A2E"
SURFACE_BG = "#323337"
SHELL_BG = "#322c31"
OVERLAY_BG = "#18181c"
INPUT_BG = "#26252a"
INPUT_FOCUS_BG = "#312c31"
ACTIVE_ROW_BG = "#4a3538"
WORKSPACE_BG = "#24252B"
HEADER_BG = "#23232A"
PANEL_SHADE = "#2B2C31"
TAB_BG = "#262730"

ACCENT_PRIMARY = "#CD7C7D"
ACCENT_SECONDARY = "#6A9FB5"
ACCENT_SOFT = "#D98A8B"
ACCENT_WARNING = "#E5A8A9"
ACCENT_BORDER = "#B86F70"
ACCENT_BORDER_BLURRED = "#7F5B61"
ACTIVE_TASK_BORDER = "#c86f6f"
INACTIVE_TASK_BORDER = "#999999"

TEXT_PRIMARY = "#F2F4F7"
TEXT_DEFAULT = "#E7E9ED"
TEXT_MUTED = "#BCC2C9"
TEXT_SUBTLE = "#C1C6CD"
TEXT_INVERTED = "#FFFFFF"

APP_THEME = Theme(
    name="todoist-kanban",
    primary=ACCENT_PRIMARY,
    secondary=ACCENT_SECONDARY,
    accent=ACCENT_SOFT,
    warning=ACCENT_WARNING,
    error=ACTIVE_TASK_BORDER,
    success=ACCENT_PRIMARY,
    foreground=TEXT_DEFAULT,
    background=APP_BG,
    surface=SURFACE_BG,
    panel=SURFACE_BG,
    dark=True,
    variables={
        "border": ACCENT_BORDER,
        "border-blurred": ACCENT_BORDER_BLURRED,
        "block-cursor-background": ACCENT_PRIMARY,
        "block-cursor-foreground": "#221a17",
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": "#5a4641",
        "block-cursor-blurred-foreground": TEXT_DEFAULT,
        "block-cursor-blurred-text-style": "none",
        "button-color-foreground": "#221a17",
        "footer-background": SURFACE_BG,
        "footer-key-foreground": "#F4BF75",
        "input-cursor-background": ACCENT_PRIMARY,
        "input-cursor-foreground": "#221a17",
        "input-selection-background": f"{ACCENT_PRIMARY} 35%",
        "scrollbar": ACCENT_BORDER,
        "scrollbar-hover": ACCENT_SOFT,
        "scrollbar-active": ACCENT_PRIMARY,
    },
)

APP_CSS = f"""
Screen {{
    background: {APP_BG};
    color: {TEXT_DEFAULT};
}}

#app-shell {{
    layout: vertical;
    height: 1fr;
    margin: 1;
    padding: 0 1;
    background: {WORKSPACE_BG};
    border: round {ACCENT_BORDER};
}}

#workspace-header {{
    height: 3;
    margin: 1 1 0 1;
    padding: 0 1;
    background: {HEADER_BG};
    border: round {ACCENT_BORDER_BLURRED};
}}

#group-rail {{
    width: 24;
    height: 1fr;
    margin-right: 1;
    padding: 1;
    scrollbar-size-vertical: 1;
    background: {PANEL_SHADE};
    border: round {INACTIVE_TASK_BORDER};
    border-title-align: center;
    border-title-style: bold;
    border-title-color: {TEXT_PRIMARY};
}}

#group-strip {{
    width: 1fr;
    height: auto;
    background: {PANEL_SHADE};
}}

Button.group-chip {{
    width: 1fr;
    height: 2;
    min-width: 0;
    margin-bottom: 1;
    padding: 0 1;
    color: {TEXT_MUTED};
    background: {PANEL_SHADE};
    border: none;
    content-align: left middle;
    text-align: left;
}}

Button.group-chip.is-active {{
    text-style: bold;
    color: {TEXT_PRIMARY};
}}

#content {{
    height: 1fr;
    margin: 1;
}}

#task-panel {{
    width: 2fr;
    height: 1fr;
    padding: 1 2;
    margin-right: 1;
    scrollbar-size-vertical: 1;
    background: {PANEL_SHADE};
    border: round {INACTIVE_TASK_BORDER};
    border-title-align: center;
    border-subtitle-align: right;
    border-title-style: bold;
    border-title-color: {TEXT_PRIMARY};
    border-subtitle-style: bold;
    border-subtitle-color: {TEXT_MUTED};
}}

#task-metrics {{
    width: 1fr;
    height: auto;
    margin-bottom: 1;
    background: transparent;
}}

#task-list {{
    width: 1fr;
    height: auto;
    background: {PANEL_SHADE};
}}

.task-card-widget {{
    width: 1fr;
    height: auto;
    background: transparent;
}}

.task-panel-hint {{
    color: {ACCENT_BORDER};
    background: transparent;
}}

.task-panel-message {{
    background: transparent;
}}

.task-panel-spacer {{
    height: 1;
    background: transparent;
}}

#detail-stack {{
    width: 1fr;
    height: 1fr;
}}

#detail-panel {{
    height: 1fr;
    margin-bottom: 1;
    padding: 1 2;
    scrollbar-size-vertical: 1;
    background: {PANEL_SHADE};
    border: round {INACTIVE_TASK_BORDER};
    border-title-align: center;
    border-title-style: bold;
    border-title-color: {TEXT_PRIMARY};
}}

#detail-summary {{
    height: auto;
}}

#detail-markdown {{
    height: auto;
    min-height: 1;
    margin-top: 1;
    background: transparent;
}}

#calendar-panel {{
    height: auto;
    min-height: 11;
    padding: 1 2;
    background: {PANEL_SHADE};
    border: round {INACTIVE_TASK_BORDER};
}}

#status {{
    height: auto;
    margin: 0 1 1 1;
    padding: 0 2;
    color: {TEXT_DEFAULT};
    background: {HEADER_BG};
    border: round {ACCENT_BORDER_BLURRED};
}}
"""


def build_modal_css(
    shell_id: str,
    width: int,
    max_width: int,
    *,
    screen_selector: str = "Screen",
    overlay_opacity: int | None = None,
    max_height: str | None = None,
    include_text_area: bool = False,
    actions_id: str | None = None,
    active_row_selector: str | None = None,
) -> str:
    lines = [f"{screen_selector} {{", "    align: center middle;"]
    if overlay_opacity is not None:
        lines.append(f"    background: {OVERLAY_BG} {overlay_opacity}%;")
    lines.append("}")
    lines.append("")
    lines.extend(
        [
            f"#{shell_id} {{",
            f"    width: {width};",
            f"    max-width: {max_width};",
            "    height: auto;",
        ]
    )
    if max_height is not None:
        lines.append(f"    max-height: {max_height};")
    lines.extend(
        [
            "    padding: 1 2;",
            f"    border: round {ACCENT_BORDER};",
            f"    background: {SHELL_BG};",
            "}",
            "",
            ".editor-title,",
            "#confirm-title,",
            "#label-manager-title {",
            "    text-style: bold;",
            f"    color: {TEXT_PRIMARY};",
            "    margin-bottom: 1;",
            "}",
            "",
            ".editor-help,",
            "#confirm-message,",
            "#label-manager-help {",
            f"    color: {TEXT_MUTED};",
            "    margin-bottom: 1;",
            "}",
        ]
    )
    if include_text_area:
        lines.extend(
            [
                "",
                ".field-label {",
                f"    color: {TEXT_DEFAULT};",
                "    text-style: bold;",
                "    margin-top: 1;",
                "}",
                "",
                "Input,",
                "TextArea,",
                "Checkbox,",
                "Select > SelectCurrent {",
                f"    background: {INPUT_BG};",
                f"    border: tall {ACCENT_BORDER};",
                f"    color: {TEXT_DEFAULT};",
                "}",
                "",
                "Input:focus,",
                "TextArea:focus,",
                "Checkbox:focus,",
                "Select:focus > SelectCurrent {",
                f"    border: tall {ACCENT_PRIMARY};",
                f"    background: {INPUT_FOCUS_BG};",
                "}",
            ]
        )
    if active_row_selector is not None:
        lines.extend(
            [
                "",
                f"{active_row_selector} {{",
                f"    background: {ACTIVE_ROW_BG};",
                "}",
            ]
        )
    if actions_id is not None:
        lines.extend(
            [
                "",
                f"#{actions_id} {{",
                "    height: auto;",
                "    margin-top: 1;" if actions_id != "confirm-actions" else "    align-horizontal: right;",
                "    align-horizontal: right;" if actions_id != "confirm-actions" else "",
                "}",
                "",
                f"#{actions_id} Button {{",
                "    margin-left: 1;",
                "}",
            ]
        )
        lines = [line for line in lines if line != "" or (lines and lines[-1] != "")]
    return "\n".join(lines)


CONFIRM_SCREEN_CSS = build_modal_css(
    "confirm-shell",
    68,
    92,
    screen_selector="ConfirmScreen",
    overlay_opacity=78,
    actions_id="confirm-actions",
) + f"""

#confirm-shell {{
    min-width: 52;
    max-height: 94%;
    background: {PANEL_SHADE};
    border: round {ACCENT_BORDER_BLURRED};
    border-title-align: center;
    border-title-color: {TEXT_PRIMARY};
    border-title-style: bold;
}}

#confirm-message {{
    margin-top: 1;
    margin-bottom: 1;
    color: {TEXT_MUTED};
}}

#confirm-actions {{
    margin-top: 2;
    border-top: heavy {INACTIVE_TASK_BORDER};
    padding-top: 1;
    align-horizontal: right;
}}

#confirm-actions Button {{
    width: auto;
    min-width: 0;
    padding: 0 2;
}}
"""

TASK_EDITOR_SCREEN_CSS = (
    build_modal_css(
        "task-editor-shell",
        76,
        88,
        screen_selector="TaskEditorScreen",
        overlay_opacity=78,
        include_text_area=True,
        actions_id="task-editor-actions",
    )
    + f"""

#task-editor-shell {{
    min-width: 68;
    max-height: 94%;
    background: {PANEL_SHADE};
    border: round {ACCENT_BORDER_BLURRED};
    border-title-align: center;
    border-title-color: {TEXT_PRIMARY};
    border-title-style: bold;
}}

#task-editor-label-hint {{
    color: {TEXT_MUTED};
    margin-top: 0;
    margin-bottom: 1;
}}

#task-editor-content,
#task-editor-labels,
#task-editor-due {{
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: tall {INACTIVE_TASK_BORDER};
    border-title-color: {TEXT_SUBTLE};
    border-title-style: bold;
}}

#task-editor-content:focus,
#task-editor-labels:focus,
#task-editor-due:focus {{
    background: {INPUT_FOCUS_BG};
    border: tall {ACCENT_PRIMARY};
}}

#task-editor-description {{
    height: 8;
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: tall {INACTIVE_TASK_BORDER};
    border-title-color: {TEXT_SUBTLE};
    border-title-style: bold;
}}

#task-editor-description:focus {{
    background: {INPUT_FOCUS_BG};
    border: tall {ACCENT_PRIMARY};
}}

#task-editor-priority {{
    width: 1fr;
}}

#task-editor-actions {{
    margin-top: 2;
    border-top: heavy {INACTIVE_TASK_BORDER};
    padding-top: 1;
}}

#task-editor-actions Button {{
    width: auto;
    min-width: 0;
    padding: 0 2;
}}
"""
)

LABEL_EDITOR_SCREEN_CSS = (
    build_modal_css(
        "label-editor-shell",
        72,
        92,
        screen_selector="LabelEditorScreen",
        overlay_opacity=80,
        include_text_area=True,
        actions_id="label-editor-actions",
    )
    .replace("TextArea,\n", "")
    .replace("TextArea:focus,\n", "")
    + f"""

#label-editor-shell {{
    min-width: 60;
    max-height: 94%;
    background: {PANEL_SHADE};
    border: round {ACCENT_BORDER_BLURRED};
    border-title-align: center;
    border-title-color: {TEXT_PRIMARY};
    border-title-style: bold;
}}

#label-editor-name,
#label-editor-color,
#label-editor-favorite {{
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: tall {INACTIVE_TASK_BORDER};
    border-title-color: {TEXT_SUBTLE};
    border-title-style: bold;
}}

#label-editor-color > SelectCurrent {{
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: tall {INACTIVE_TASK_BORDER};
}}

#label-editor-color:focus > SelectCurrent {{
    background: {INPUT_FOCUS_BG};
    border: tall {INACTIVE_TASK_BORDER};
}}

#label-editor-color > SelectOverlay {{
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: tall {INACTIVE_TASK_BORDER};
}}

#label-editor-color > SelectOverlay:focus {{
    background: {HEADER_BG};
}}

#label-editor-name:focus,
#label-editor-favorite:focus {{
    background: {INPUT_FOCUS_BG};
    border: tall {ACCENT_PRIMARY};
}}

#label-editor-color:focus {{
    background: {INPUT_FOCUS_BG};
    border: tall {INACTIVE_TASK_BORDER};
}}

#label-editor-actions {{
    margin-top: 2;
    border-top: heavy {INACTIVE_TASK_BORDER};
    padding-top: 1;
}}

#label-editor-actions Button {{
    width: auto;
    min-width: 0;
    padding: 0 2;
}}
"""
)

LABEL_MANAGER_SCREEN_CSS = (
    build_modal_css(
        "label-manager-shell",
        88,
        96,
        screen_selector="LabelManagerScreen",
        overlay_opacity=78,
        max_height="94%",
        actions_id="label-manager-actions",
        active_row_selector=".label-row.is-active",
    )
    + f"""

#label-manager-shell {{
    min-width: 68;
    max-height: 94%;
    background: {PANEL_SHADE};
    border: round {ACCENT_BORDER_BLURRED};
    border-title-align: center;
    border-title-color: {TEXT_PRIMARY};
    border-title-style: bold;
}}

#label-manager-list {{
    height: 16;
    min-height: 8;
    margin-top: 1;
    padding: 0 1;
    background: transparent;
    color: {TEXT_DEFAULT};
    border: none;
}}

#label-manager-actions {{
    margin-top: 2;
    border-top: heavy {INACTIVE_TASK_BORDER};
    padding-top: 1;
    align-horizontal: center;
}}

#label-manager-actions Button {{
    width: auto;
    min-width: 0;
    padding: 0 2;
}}
"""
)

SYNC_PREVIEW_SCREEN_CSS = (
    build_modal_css(
        "sync-preview-shell",
        96,
        120,
        screen_selector="SyncPreviewScreen",
        overlay_opacity=78,
        max_height="94%",
        actions_id="sync-preview-actions",
    )
    + f"""

#sync-preview-shell {{
    min-width: 72;
    max-height: 94%;
    background: {PANEL_SHADE};
    border: round {ACCENT_BORDER_BLURRED};
    border-title-align: center;
    border-title-color: {TEXT_PRIMARY};
    border-title-style: bold;
}}

#sync-preview-summary {{
    height: auto;
    margin-top: 1;
    margin-bottom: 1;
    color: {TEXT_MUTED};
}}

#sync-preview-list {{
    height: auto;
    min-height: 4;
    margin-bottom: 1;
    padding: 0 1;
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: round {INACTIVE_TASK_BORDER};
}}

#sync-preview-markdown {{
    height: 1fr;
    min-height: 10;
    padding: 0 1;
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: round {INACTIVE_TASK_BORDER};
}}

#sync-preview-conflict-viewer {{
    height: 1fr;
    min-height: 14;
    margin-top: 1;
}}

#sync-preview-conflict-local,
#sync-preview-conflict-remote {{
    width: 1fr;
    height: 1fr;
    min-height: 14;
    padding: 0 1;
    background: {HEADER_BG};
    color: {TEXT_DEFAULT};
    border: round {INACTIVE_TASK_BORDER};
}}

#sync-preview-conflict-local {{
    margin-right: 1;
}}

#sync-preview-actions {{
    margin-top: 1;
    border-top: heavy {INACTIVE_TASK_BORDER};
    padding-top: 1;
    align-horizontal: right;
}}

#sync-preview-actions Button {{
    width: auto;
    min-width: 0;
    padding: 0 2;
}}
"""
)

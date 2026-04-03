# Todoist Kanban TUI

A Textual-based Todoist client with a keyboard-first kanban feel inspired by the `todocli/` board UI.

## What It Does

- Loads active tasks from your Todoist Inbox
- Groups the board by labels, with dedicated views for `All Tasks` and `No Label`
- Keeps the main screen focused on browsing tasks and task details
- Opens task creation/editing in a centered popup instead of a permanent editor pane
- Opens label creation/editing/deletion in a dedicated popup manager
- Runs with mouse support disabled

## Run

Set your Todoist API token first:

```bash
export TODOIST_API_TOKEN="your-token"
```

Then start the app with either:

```bash
uv run python main.py
```

or:

```bash
uv run todoist-tui
```

## Keyboard

- `h` / `l` or arrow left/right: switch label groups
- `j` / `k` or arrow down/up: move between tasks in the current group
- `n`: create a new task
- `e` or `Enter`: edit the selected task
- `Space`: complete the selected task
- `x`: delete the selected task
- `Shift+L` (`L`): open label management
- `r`: refresh from Todoist
- `q`: quit

Inside task and label popups:

- `Ctrl+S`: save
- `Esc`: cancel

Inside the label manager popup:

- `j` / `k`: move between labels
- `a`: create a label
- `e` or `Enter`: edit the selected label
- `x`: delete the selected label
- `Esc` or `q`: close

## Optional Configuration

- `TODOIST_DUE_LANG` sets the parsing language for due date text, for example `en` or `pl`
- You can also pass `--token` and `--due-lang` on the command line

## Notes

- New tasks are always created in the Inbox project Todoist marks as your Inbox
- Tasks with multiple labels can appear in multiple label groups
- Leaving the due field blank while editing a task removes its existing due date

---
**Source:** https://github.com/Textualize/textual/blob/main/docs/widgets/list_view.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# ListView

!!! tip "Added in version 0.6.0"

Displays a vertical list of `ListItem`s which can be highlighted and selected.
Supports keyboard navigation.

- [x] Focusable
- [x] Container

## Example

The example below shows an app with a simple `ListView`.

=== "Output"

    ```{.textual path="docs/examples/widgets/list_view.py"}
    ```

=== "list_view.py"

    ```python
    --8<-- "docs/examples/widgets/list_view.py"
    ```

=== "list_view.tcss"

    ```css
    --8<-- "docs/examples/widgets/list_view.tcss"
    ```

## Reactive Attributes

| Name    | Type  | Default | Description                      |
| ------- | ----- | ------- | -------------------------------- |
| `index` | `int` | `0`     | The currently highlighted index. |

## Messages

- [ListView.Highlighted][textual.widgets.ListView.Highlighted]
- [ListView.Selected][textual.widgets.ListView.Selected]

## Bindings

The list view widget defines the following bindings:

::: textual.widgets.ListView.BINDINGS
    options:
      show_root_heading: false
      show_root_toc_entry: false

## Component Classes

This widget has no component classes.

---


::: textual.widgets.ListView
    options:
      heading_level: 2

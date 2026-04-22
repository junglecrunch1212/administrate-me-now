---
**Source:** https://github.com/Textualize/textual/blob/main/docs/styles/scrollbar_colors/scrollbar_color_active.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# Scrollbar-color-active

The `scrollbar-color-active` style sets the color of the scrollbar when the thumb is being dragged.

## Syntax

--8<-- "docs/snippets/syntax_block_start.md"
<a href="./scrollbar_color_active">scrollbar-color-active</a>: <a href="../../../css_types/color">&lt;color&gt;</a> [<a href="../../../css_types/percentage">&lt;percentage&gt;</a>];
--8<-- "docs/snippets/syntax_block_end.md"

`scrollbar-color-active` accepts a [`<color>`](../../css_types/color.md) (with an optional opacity level defined by a [`<percentage>`](../../css_types/percentage.md)) that is used to define the color of a scrollbar when its thumb is being dragged.

## Example

=== "Output"

    ![](scrollbar_colors_demo.gif)

    !!! note

        The GIF above has reduced quality to make it easier to load in the documentation.
        Try running the example yourself with `textual run docs/examples/styles/scrollbars2.py`.

=== "scrollbars2.py"

    ```py
    --8<-- "docs/examples/styles/scrollbars2.py"
    ```

=== "scrollbars2.tcss"

    ```css hl_lines="6"
    --8<-- "docs/examples/styles/scrollbars2.tcss"
    ```

## CSS

```css
scrollbar-color-active: yellow;
```

## Python

```py
widget.styles.scrollbar_color_active = "yellow"
```

## See also

 - [`scrollbar-background-active`](./scrollbar_color_active.md) to set the scrollbar background color when the scrollbar is being dragged.
 - [`scrollbar-color`](./scrollbar_color.md) to set the color of scrollbars.
 - [`scrollbar-color-hover`](./scrollbar_color_hover.md) to set the scrollbar color when the mouse pointer is over it.

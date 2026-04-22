---
**Source:** https://github.com/Textualize/textual/blob/main/docs/css_types/vertical.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# &lt;vertical&gt;

The `<vertical>` CSS type represents a position along the vertical axis.

## Syntax

The [`<vertical>`](./vertical.md) type can take any of the following values:

| Value           | Description                                |
| --------------- | ------------------------------------------ |
| `bottom`        | Aligns at the bottom of the vertical axis. |
| `middle`        | Aligns in the middle of the vertical axis. |
| `top` (default) | Aligns at the top of the vertical axis.    |

## Examples

### CSS

```css
.container {
    align-vertical: top;
}
```

### Python

```py
widget.styles.align_vertical = "top"
```

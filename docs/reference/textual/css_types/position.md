---
**Source:** https://github.com/Textualize/textual/blob/main/docs/css_types/position.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# &lt;position&gt;

The `<position>` CSS type defines how the `offset` rule is applied.


## Syntax

A [`<position>`](./position.md) may be any of the following values:

| Value      | Alignment type                                               |
| ---------- | ------------------------------------------------------------ |
| `relative` | Offset is applied to widgets default position.               |
| `absolute` | Offset is applied to the origin (top left) of its container. |

## Examples

### CSS

```css
Label {
    position: absolute;
    offset: 10 5;
}
```

### Python

```py
widget.styles.position = "absolute"
widget.styles.offset = (10, 5)
```

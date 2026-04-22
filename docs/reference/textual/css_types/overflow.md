---
**Source:** https://github.com/Textualize/textual/blob/main/docs/css_types/overflow.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# &lt;overflow&gt;

The `<overflow>` CSS type represents overflow modes.

## Syntax

The [`<overflow>`](./overflow.md) type can take any of the following values:

| Value    | Description                            |
|----------|----------------------------------------|
| `auto`   | Determine overflow mode automatically. |
| `hidden` | Don't overflow.                        |
| `scroll` | Allow overflowing.                     |

## Examples

### CSS

```css
#container {
    overflow-y: hidden;  /* Don't overflow */
}
```

### Python

```py
widget.styles.overflow_y = "hidden"  # Don't overflow
```

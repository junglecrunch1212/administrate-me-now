---
**Source:** https://github.com/Textualize/textual/blob/main/docs/css_types/keyline.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# &lt;keyline&gt;

The `<keyline>` CSS type represents a line style used in the [keyline](../styles/keyline.md) rule.


## Syntax

| Value    | Description                |
| -------- | -------------------------- |
| `none`   | No line (disable keyline). |
| `thin`   | A thin line.               |
| `heavy`  | A heavy (thicker) line.    |
| `double` | A double line.             |

## Examples

### CSS

```css
Vertical {
    keyline: thin green;
}
```

### Python

```py
# A tuple of <keyline> and color
widget.styles.keyline = ("thin", "green")
```

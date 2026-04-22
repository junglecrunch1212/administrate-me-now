---
**Source:** https://github.com/Textualize/textual/blob/main/docs/css_types/hatch.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# &lt;hatch&gt;

The `<hatch>` CSS type represents a character used in the [hatch](../styles/hatch.md) rule.

## Syntax

| Value        | Description                    |
| ------------ | ------------------------------ |
| `cross`      | A diagonal crossed line.       |
| `horizontal` | A horizontal line.             |
| `left`       | A left leaning diagonal line.  |
| `right`      | A right leaning diagonal line. |
| `vertical`   | A vertical line.               |


## Examples

### CSS


```css
.some-class {
    hatch: cross green;
}
```

### Python

```py
widget.styles.hatch = ("cross", "red")
```

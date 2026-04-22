---
**Source:** https://github.com/Textualize/textual/blob/main/docs/css_types/number.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

# &lt;number&gt;

The `<number>` CSS type represents a real number, which can be an integer or a number with a decimal part (akin to a `float` in Python).

## Syntax

A [`<number>`](./number.md) is an [`<integer>`](./integer.md), optionally followed by the decimal point `.` and a decimal part composed of one or more digits.

## Examples

### CSS

```css
Grid {
    grid-size: 3 6  /* Integers are numbers */
}

.translucid {
    opacity: 0.5    /* Numbers can have a decimal part */
}
```

### Python

In Python, a rule that expects a CSS type `<number>` will accept an `int` or a `float`:

```py
widget.styles.grid_size = (3, 6)  # Integers are numbers
widget.styles.opacity = 0.5       # Numbers can have a decimal part
```

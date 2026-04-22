---
**Source:** https://github.com/Textualize/textual/blob/main/docs/api/await_remove.md

**Fetched:** 2026-04-22

**License:** MIT (Textualize/textual/LICENSE)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

---
title: "textual.await_remove"
---

This module contains the `AwaitRemove` class.
An `AwaitRemove` object is returned by [`Widget.remove()`][textual.widget.Widget.remove] and other methods which remove widgets.
You can await the return value if you need to know exactly when the widget(s) have been removed.
Or you can ignore it and Textual will wait for the widgets to be removed before handling the next message.

!!! note

    You are unlikely to need to explicitly create these objects yourself.


::: textual.await_remove

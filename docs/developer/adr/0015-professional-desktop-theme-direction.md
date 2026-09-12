# 15 — Professional desktop-app theme direction

**Status:** Accepted

## Context

The GUI's early pages (Project Home, Analysis workspace, New Analysis) were
built with mobile-app conventions: single-column scrolling lists, fixed
fractional-width panels, card-style chrome, minimal information density per
screen. This reads as a phone/tablet app rather than a desktop tool used
alongside dense scientific data, and doesn't match the comparison points the
user has in mind for what this tool should feel like day to day.

## Decision

Move the GUI, page by page, toward a professional desktop-app look —
explicitly benchmarked against tools like Adobe's products and MSDIAL rather
than mobile-app design language. Concretely, prefer:

- Multi-panel layouts over single-column stacks.
- Resizable panels (`SplitView`) over fixed fractional-width `RowLayout`s.
- Information-dense views showing real data, not placeholder-feeling lists.
- Right-click context menus for secondary actions (e.g. "copy output path")
  instead of surfacing every action as an always-visible inline button.
- An explicit light `palette` block on `ApplicationWindow` (see
  `Main.qml`) rather than relying on the host desktop theme, since Qt
  styles other than Material still inherit the platform's own (possibly
  dark) palette via platform-theme integration.

A top menubar is a deliberate **not yet**: worth doing eventually, but not
started until the user asks for it specifically, since the menu structure
itself needs to be thought through rather than added incidentally to
whichever page is being redone.

This is a standing direction applied incrementally, one page at a time, not
a single redesign pass — see
[GUI architecture](../architecture/gui.md#theme) for which pages have been
redone under it so far.

## Consequences

- Pages not yet revisited under this direction still look like the old
  mobile-app style; don't assume the whole app matches this ADR just
  because it's accepted — check which pages have actually been redone.
- Every new page or section built from now on should default to these
  conventions rather than the earlier mobile-app patterns, even before its
  own dedicated redesign pass would otherwise reach it.
- No top menubar until explicitly requested — don't add one speculatively
  while touching a page for an unrelated reason.

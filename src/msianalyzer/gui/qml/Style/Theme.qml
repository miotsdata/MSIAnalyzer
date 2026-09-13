pragma Singleton
import QtQuick

// Design tokens shared across every page — the single source of truth for
// the app's color scheme and type scale. Two ad hoc, hand-picked colors
// ("blue" page titles, a raw "#0078d4"/"#e6e6e6"/"#202020" button style)
// already drifted out of sync with the real theme once before
// (2026-09-14) — this exists so the next palette change doesn't require
// hunting down every hardcoded hex again.
//
// Every role in Main.qml's ApplicationWindow `palette {}` block reads
// from a token here (`window: Theme.backgroundColor`, `base:
// Theme.fieldColor`, `mid: Theme.borderColor`, ...) rather than the other
// way around — THIS file is the one true source of truth for the app's
// whole color scheme, not a mirror of a couple of values Main.qml
// happens to also need under a friendlier name.
//
// Light/dark switching (2026-09-15): `isDark` is a plain mutable
// property (not persisted across restarts yet — the menu bar's View >
// Light/Dark toggle just flips it for the running session; add
// persistence, e.g. QtCore's `Settings` QML type, once that's actually
// wanted). Every color token below is an `isDark ? dark : light` pair
// instead of one literal — since `Main.qml`'s palette reads *from* these
// tokens, flipping `isDark` re-themes the entire app live, with no other
// file needing to change.
QtObject {
    property bool isDark: true

    // --- Background / surface hierarchy -----------------------------

    // The page's own base canvas color — `palette.window`.
    readonly property color backgroundColor: isDark ? "#2d2d30" : "#f2f2f5"

    // A surface that sits visually "above" the background — card/tile
    // backgrounds, anything that should read as a distinct panel rather
    // than bare page background (e.g. Summary's stat tiles, previously
    // left at Rectangle's default white — a real bug once the palette
    // first went dark). `palette.alternateBase`. Some design systems call
    // this "surface" instead of "secondary" — same idea. Elevation reads
    // as "closer to the light source" in both directions: lighter than
    // the background in dark mode, brighter/whiter than it in light mode.
    readonly property color secondaryColor: isDark ? "#323234" : "#ffffff"

    // Recessed content areas — text fields, list/table backgrounds.
    // `palette.base`. The opposite direction from `secondaryColor`:
    // reads as "sunken" relative to the background in both modes.
    readonly property color fieldColor: isDark ? "#252526" : "#e9e9ed"

    // Raised interactive surfaces — button fills, tooltip backgrounds.
    // `palette.button` and `palette.toolTipBase` (both the same shade;
    // a tooltip reads as "just another raised surface", not a distinct
    // concept worth its own token).
    readonly property color controlColor: isDark ? "#3c3c3c" : "#e4e4e8"

    // --- Text -----------------------------------------------------------

    // The app's one standard foreground text color — `palette.windowText`,
    // `.text`, `.buttonText`, and `.toolTipText` all read this (they're
    // separate QPalette roles for separate widget contexts, but there's
    // never been a reason for them to actually differ in this app; split
    // them apart here the day one genuinely needs to).
    readonly property color textColor: isDark ? "#e0e0e0" : "#202020"

    // Text/icon color drawn on top of a `primaryColor`-filled area (e.g.
    // a selected list row's label) — `palette.highlightedText`. Named
    // after the QPalette role it maps to, not "onPrimaryColor" (the more
    // common design-system term) — QML reserves any `onXxx` property name
    // for signal handlers, so that name fails to even parse. White reads
    // fine on `primaryColor` in both modes, so no light/dark split here.
    readonly property color highlightedTextColor: "#ffffff"

    // `TextField`/`ComboBox` placeholder text — `palette.placeholderText`.
    readonly property color placeholderTextColor: isDark ? "#8a8a8a" : "#767676"

    // Secondary/hint text — captions, "no X selected" labels — general
    // body text that isn't a TextField placeholder, hence not
    // `placeholderTextColor` despite the similar muted intent.
    readonly property color mutedTextColor: isDark ? "#808080" : "#5a5a5a"

    // Text/icon color for a *disabled* control (e.g. a menu item that
    // can't be used without an open project) — dimmer than
    // `mutedTextColor`, which is still meant to be read normally, not to
    // signal "unavailable". `palette.disabled.windowText`/`.buttonText`/
    // `.text`.
    readonly property color disabledTextColor: isDark ? "#6a6a6a" : "#a8a8a8"

    // Error/failure text (e.g. a run's "Failed: ..." message). No QPalette
    // role covers semantic error state. The dark-mode soft red reads too
    // washed-out against a light background, hence a distinct, more
    // saturated shade rather than reusing the same hex both ways.
    readonly property color errorColor: isDark ? "#e06c75" : "#c0392b"

    // --- Accent ----------------------------------------------------------

    // The main accent/action color — `palette.highlight`. Every ordinary
    // Control already picks this up automatically via `highlighted:
    // true`/selection state; reach for this token directly only when
    // drawing a custom (non-Control) shape that needs to match. Slightly
    // deeper in light mode — the dark-mode blue reads a touch washed-out
    // against a near-white surface at the same saturation.
    readonly property color primaryColor: isDark ? "#3d8bd4" : "#2f6fb3"

    // --- Border / bevel scale ---------------------------------------

    // The one border/divider shade actually used by name throughout the
    // rest of the app (`palette.mid`, read directly — e.g.
    // `AppMenuItem.qml`, stat tiles, panel borders, the menu bar's own
    // bottom border). Needs to read as clearly *not background* — in
    // light mode that means darker than the background (correctly, `mid`
    // is traditionally the darker side of Qt's light/midlight/mid/dark
    // bevel scale), but in dark mode a border darker than an already-dark
    // background is nearly invisible; it needs to go *lighter* instead
    // (reported 2026-09-15: "the line dividing different sections... as
    // well as menubar border, should be lighter, not darker"). So this
    // one token breaks from the classical descending light > midlight >
    // control > mid > dark > shadow gradient in dark mode specifically —
    // deliberately, for visibility, not an oversight.
    readonly property color borderColor: isDark ? "#48484c" : "#c8c8ce"
    readonly property color lightColor: isDark ? "#4a4a4a" : "#ffffff"
    readonly property color midlightColor: isDark ? "#414141" : "#e0e0e6"
    readonly property color darkColor: isDark ? "#1e1e1e" : "#a0a0a8"
    // Actual drop-shadow alpha effects, not a surface color — stays near-
    // black regardless of theme, same as most design systems' "shadow".
    readonly property color shadowColor: "#000000"

    // --- Type scale --------------------------------------------------

    // A page's own title (e.g. "Create project", a project's name on
    // Project Home) and small caption/hint text under a control. Sizes,
    // not colors — no light/dark variant needed.
    readonly property int headingPixelSize: 18
    readonly property int captionPixelSize: 10
}

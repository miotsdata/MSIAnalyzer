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
// happens to also need under a friendlier name (2026-09-15: every
// remaining literal moved out of Main.qml's palette block into here, on
// top of the four — background/secondary/primary/disabled-text — that
// were already promoted).
//
// This shape (Theme owns the values, Main.qml's palette just wires them
// into QPalette roles) is also what makes a future light/dark toggle
// cheap: swap each `readonly property color` below for an `isDark ? … :
// …` pair bound to one shared `isDark` property, and the whole app
// re-themes live — Main.qml never needs to change again.
QtObject {
    // --- Background / surface hierarchy -----------------------------

    // The page's own base canvas color — `palette.window`.
    readonly property color backgroundColor: "#2d2d30"

    // A surface that sits visually "above" the background — card/tile
    // backgrounds, anything that should read as a distinct panel rather
    // than bare page background (e.g. Summary's stat tiles, previously
    // left at Rectangle's default white — a real bug once the palette
    // went dark). `palette.alternateBase`. Some design systems call this
    // "surface" instead of "secondary" — same idea.
    readonly property color secondaryColor: "#323234"

    // Recessed content areas — text fields, list/table backgrounds.
    // `palette.base`.
    readonly property color fieldColor: "#252526"

    // Raised interactive surfaces — button fills, tooltip backgrounds.
    // `palette.button` and `palette.toolTipBase` (both the same shade;
    // a tooltip reads as "just another raised surface", not a distinct
    // concept worth its own token).
    readonly property color controlColor: "#3c3c3c"

    // --- Text -----------------------------------------------------------

    // The app's one standard foreground text color — `palette.windowText`,
    // `.text`, `.buttonText`, and `.toolTipText` all read this (they're
    // separate QPalette roles for separate widget contexts, but there's
    // never been a reason for them to actually differ in this app; split
    // them apart here the day one genuinely needs to).
    readonly property color textColor: "#e0e0e0"

    // Text/icon color drawn on top of a `primaryColor`-filled area (e.g.
    // a selected list row's label) — `palette.highlightedText`. Named
    // after the QPalette role it maps to, not "onPrimaryColor" (the more
    // common design-system term) — QML reserves any `onXxx` property name
    // for signal handlers, so that name fails to even parse.
    readonly property color highlightedTextColor: "#ffffff"

    // `TextField`/`ComboBox` placeholder text — `palette.placeholderText`.
    readonly property color placeholderTextColor: "#8a8a8a"

    // Secondary/hint text — captions, "no X selected" labels — general
    // body text that isn't a TextField placeholder, hence not
    // `placeholderTextColor` despite the similar muted intent.
    readonly property color mutedTextColor: "#808080"

    // Text/icon color for a *disabled* control (e.g. a menu item that
    // can't be used without an open project) — dimmer than
    // `mutedTextColor`, which is still meant to be read normally, not to
    // signal "unavailable". `palette.disabled.windowText`/`.buttonText`/
    // `.text`.
    readonly property color disabledTextColor: "#6a6a6a"

    // Error/failure text (e.g. a run's "Failed: ..." message). No QPalette
    // role covers semantic error state.
    readonly property color errorColor: "#e06c75"

    // --- Accent ----------------------------------------------------------

    // The main accent/action color — `palette.highlight`. Every ordinary
    // Control already picks this up automatically via `highlighted:
    // true`/selection state; reach for this token directly only when
    // drawing a custom (non-Control) shape that needs to match.
    readonly property color primaryColor: "#3d8bd4"

    // --- Border / bevel scale ---------------------------------------

    // The one border/divider shade actually used by name throughout the
    // rest of the app (`palette.mid`, read directly — e.g.
    // `AppMenuItem.qml`, stat tiles, panel borders). The other three
    // below exist for completeness (Fusion's own internal bevel/shadow
    // rendering on standard Controls reads them) but nothing outside
    // Main.qml currently names them directly.
    readonly property color borderColor: "#2a2a2a"
    readonly property color lightColor: "#4a4a4a"
    readonly property color midlightColor: "#414141"
    readonly property color darkColor: "#1e1e1e"
    readonly property color shadowColor: "#000000"

    // --- Type scale --------------------------------------------------

    // A page's own title (e.g. "Create project", a project's name on
    // Project Home) and small caption/hint text under a control.
    readonly property int headingPixelSize: 18
    readonly property int captionPixelSize: 10
}

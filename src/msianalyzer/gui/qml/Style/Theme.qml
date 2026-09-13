pragma Singleton
import QtQuick

// Design tokens shared across every page — the single source of truth for
// the app's color scheme and type scale. Two ad hoc, hand-picked colors
// ("blue" page titles, a raw "#0078d4"/"#e6e6e6"/"#202020" button style)
// already drifted out of sync with the real theme once before
// (2026-09-14) — this exists so the next palette change doesn't require
// hunting down every hardcoded hex again.
//
// `backgroundColor`/`secondaryColor`/`primaryColor` are the three named
// tokens most design systems call "background/secondary/primary" — Main.qml's
// ApplicationWindow `palette {}` block reads all three of *these*
// (`window: Theme.backgroundColor`, `alternateBase: Theme.secondaryColor`,
// `highlight: Theme.primaryColor`) rather than the other way around, so
// THIS file is the one true source of truth for them, not a mirror of it.
// Every other palette role (`base`/`button`/`text`/border shades/...) is
// still just set directly in Main.qml's palette block — only promoted to
// a named Theme token here once something outside that block needs to
// reference it too (as `secondaryColor` now does, for card/tile
// backgrounds — see SummarySection.qml's stat tiles).
QtObject {
    // The page's own base canvas color — same value as `palette.window`.
    readonly property color backgroundColor: "#2d2d30"

    // A surface that sits visually "above" the background — card/tile
    // backgrounds, anything that should read as a distinct panel rather
    // than bare page background (e.g. Summary's stat tiles, previously
    // left at Rectangle's default white — a real bug once the palette
    // went dark). Same value as `palette.alternateBase`. Some design
    // systems call this "surface" instead of "secondary" — same idea.
    readonly property color secondaryColor: "#323234"

    // The main accent/action color — same value as `palette.highlight`.
    // Every ordinary Control already picks this up automatically via
    // `highlighted: true`/selection state; reach for this token directly
    // only when drawing a custom (non-Control) shape that needs to match.
    readonly property color primaryColor: "#3d8bd4"

    // Secondary/hint text — captions, placeholders-that-aren't-placeholders,
    // "no X selected" labels. `palette.placeholderText` exists but is
    // specifically for TextField placeholder text, not general secondary
    // body text, hence a separate token.
    readonly property color mutedTextColor: "#808080"

    // Error/failure text (e.g. a run's "Failed: ..." message). No QPalette
    // role covers semantic error state.
    readonly property color errorColor: "#e06c75"

    // Type scale: a page's own title (e.g. "Create project", a project's
    // name on Project Home) and small caption/hint text under a control.
    readonly property int headingPixelSize: 18
    readonly property int captionPixelSize: 10
}

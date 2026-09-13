pragma Singleton
import QtQuick

// Design tokens shared across every page — the single source of truth for
// values that aren't already covered by the app's `palette` (set once on
// ApplicationWindow in Main.qml, which every Control-derived item already
// inherits). Two ad hoc, hand-picked colors ("blue" page titles, a raw
// "#0078d4"/"#e6e6e6"/"#202020" button style) already drifted out of sync
// with the real theme once before (2026-09-14) — this exists so the next
// palette change doesn't require hunting down every hardcoded hex again.
//
// Deliberately lean: only tokens an existing page actually consumes today.
// A structural/chrome color (button fill, borders, selection highlight)
// should read from `palette.*` directly instead of duplicating a role
// here — `Theme` only holds what `palette` has no role for at all
// (semantic error color, secondary/caption text, the heading/caption
// type scale).
QtObject {
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

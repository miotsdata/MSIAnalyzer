import QtQuick
import QtQuick.Controls

// A `MenuItem` with a visible border — plain Fusion menu items blend into
// each other and into the surrounding panel, reported unclear ("menubar
// should be clear... each element of the menu should have its border
// also", 2026-09-14). A real QML type (not a wrapped/composed one) so
// every other property/signal (`text`, `enabled`, `visible`, `checkable`,
// `onTriggered`, ...) is inherited unchanged — this only replaces
// `background` and the text color. `contentItem` is left at Fusion's
// own default. Use it for every menu entry, context menus included.
MenuItem {
    id: control

    // A Menu is a popup, and the window palette's `disabled` group doesn't
    // reach it: a disabled item rendered in the normal text color. Set
    // explicitly so "not allowed" reads as grey.
    palette.text: enabled ? Theme.textColor : Theme.disabledTextColor

    background: Rectangle {
        implicitHeight: 28
        color: control.highlighted ? palette.highlight : "transparent"
        border.color: palette.mid
        border.width: 1
    }
}

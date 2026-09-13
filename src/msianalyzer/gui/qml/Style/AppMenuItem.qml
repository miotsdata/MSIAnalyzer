import QtQuick
import QtQuick.Controls

// A `MenuItem` with a visible border — plain Fusion menu items blend into
// each other and into the surrounding panel, reported unclear ("menubar
// should be clear... each element of the menu should have its border
// also", 2026-09-14). A real QML type (not a wrapped/composed one) so
// every other property/signal (`text`, `enabled`, `visible`, `checkable`,
// `onTriggered`, ...) is inherited unchanged — this only replaces
// `background`. `contentItem` is left at Fusion's own default, which
// already renders `text` in the right enabled/disabled palette color
// once that's configured (see Main.qml's palette `disabled` group).
MenuItem {
    id: control

    background: Rectangle {
        implicitHeight: 28
        color: control.highlighted ? palette.highlight : "transparent"
        border.color: palette.mid
        border.width: 1
    }
}

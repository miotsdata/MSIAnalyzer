import QtQuick
import QtQuick.Controls

// A full-opacity cover (not a dim/translucent one — the point is to hide
// a not-yet-fully-constructed page, not let it show through) with a
// centered spinner. Used while a Loader-based section (and, where
// relevant, its own async content like a WebEngineView plot) isn't ready
// yet — see AnalysisPage.qml.
Item {
    id: loadingOverlay
    objectName: "loadingOverlay"

    Rectangle {
        anchors.fill: parent
        color: palette.window
    }

    BusyIndicator {
        objectName: "loadingOverlaySpinner"
        anchors.centerIn: parent
        running: loadingOverlay.visible
    }
}

import QtQuick
import "qrc:/Style"

// Numbered point markers for CoregistrationWindow's landmark-picking
// canvases — the landmark-pair analogue of RoiOverlay.qml's polygon
// borders. Points are given in this canvas' own native pixel-coordinate
// space as continuous floats (NOT RoiOverlay's `+0.5`-offset grid-cell-
// center convention — that exists for testing whether a *cell* contains a
// polygon, meaningless for a single clicked point), and mapped onto
// screen via `effectiveScale` so this overlay shares the owning
// `ZoomableImage`'s exact pan/zoom transform, same as RoiOverlay.
Item {
    id: overlay
    objectName: "landmarkOverlay"

    property real effectiveScale: 1.0
    // [{x, y, index}, ...] — completed landmarks on this pane, colored by
    // index so a pair's H&E-side and MSI-side markers share a color.
    property var points: []
    // The single point clicked on THIS pane, awaiting its pair on the
    // other pane — null when nothing is pending here. Drawn hollow, at
    // the color its completed marker will get once paired.
    property var pendingPoint: null

    readonly property var _palette: [
        "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
        "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    ]
    function _colorFor(index) {
        return overlay._palette[index % overlay._palette.length]
    }

    Repeater {
        id: landmarkRepeater
        objectName: "landmarkOverlayRepeater"
        model: overlay.points

        delegate: Item {
            objectName: "landmarkMarker_" + modelData.index
            width: 18
            height: 18
            x: modelData.x * overlay.effectiveScale - width / 2
            y: modelData.y * overlay.effectiveScale - height / 2

            Rectangle {
                anchors.fill: parent
                radius: width / 2
                color: overlay._colorFor(modelData.index)
                border.color: "white"
                border.width: 1.5
            }
            Text {
                anchors.centerIn: parent
                text: modelData.index + 1
                color: "white"
                font.pixelSize: 10
                font.bold: true
            }
        }
    }

    Rectangle {
        id: pendingMarker
        objectName: "landmarkOverlayPending"
        visible: overlay.pendingPoint !== null
        width: 18
        height: 18
        radius: width / 2
        color: "transparent"
        border.width: 2
        border.color: overlay._colorFor(overlay.points.length)
        x: overlay.pendingPoint ? overlay.pendingPoint.x * overlay.effectiveScale - width / 2 : 0
        y: overlay.pendingPoint ? overlay.pendingPoint.y * overlay.effectiveScale - height / 2 : 0
    }
}

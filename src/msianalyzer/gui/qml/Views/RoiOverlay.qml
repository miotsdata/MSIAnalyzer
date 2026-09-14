import QtQuick
import QtQuick.Shapes

// A ROI border overlay, meant to be declared as a child of a
// `ZoomableImage` (using its `overlayContent` extension point) so it
// shares that component's exact pan/zoom coordinate frame — a vertex
// given as `[col, row]` (grid-index space, see
// `core/plotting/heatmap.pixel_grid_indices`) maps onto this overlay's own
// local pixel `((col + 0.5) * effectiveScale, (row + 0.5) * effectiveScale)`
// with no separate offset math, since `ZoomableImage`'s `overlayHolder`
// already tracks the Image element's position/size 1:1.
//
// Two modes:
//   - readOnly: true (Visual Inspection's "Show ROIs") — draws every
//     entry in `savedRois`, no interaction.
//   - readOnly: false (ROI Design's drawing canvas) — additionally draws
//     the in-progress `draftVertices` polygon (open, not closed — the
//     closing edge only appears once the polygon is actually saved and
//     becomes a `savedRois` entry) and an enlarged/highlighted first-
//     vertex marker as the "hover near the start point to close"
//     affordance (`firstVertexHighlighted`, driven by the owning canvas'
//     own HoverHandler + distance check — purely visual here).
Item {
    id: overlay
    objectName: "roiOverlay"

    property bool readOnly: true
    property real effectiveScale: 1.0
    property var savedRois: []
    property var draftVertices: []
    property color draftColor: "#ffffff"
    property bool firstVertexHighlighted: false

    function toItemX(col) { return (col + 0.5) * overlay.effectiveScale }
    function toItemY(row) { return (row + 0.5) * overlay.effectiveScale }

    // Vertices as on-screen points, with the first point repeated at the
    // end to close the loop — used for every *saved* ROI (a finished
    // polygon always renders closed).
    function _closedPoints(vertices) {
        var pts = (vertices || []).map(function (v) {
            return Qt.point(overlay.toItemX(v[0]), overlay.toItemY(v[1]))
        })
        if (pts.length > 0) pts.push(pts[0])
        return pts
    }

    Repeater {
        id: savedRoiRepeater
        objectName: "roiOverlaySavedRepeater"
        model: overlay.savedRois

        delegate: Shape {
            objectName: "roiOverlaySavedShape_" + modelData.name
            anchors.fill: parent
            ShapePath {
                strokeColor: modelData.color
                strokeWidth: 2
                fillColor: "transparent"
                PathPolyline { path: overlay._closedPoints(modelData.vertices) }
            }
        }
    }

    Shape {
        id: draftShape
        objectName: "roiOverlayDraftShape"
        anchors.fill: parent
        visible: !overlay.readOnly && overlay.draftVertices.length > 0
        ShapePath {
            strokeColor: overlay.draftColor
            strokeWidth: 2
            fillColor: "transparent"
            PathPolyline {
                path: (overlay.draftVertices || []).map(function (v) {
                    return Qt.point(overlay.toItemX(v[0]), overlay.toItemY(v[1]))
                })
            }
        }
    }

    Rectangle {
        id: closeAffordance
        objectName: "roiOverlayCloseAffordance"
        visible: !overlay.readOnly && overlay.draftVertices.length > 0
        radius: width / 2
        width: overlay.firstVertexHighlighted ? 16 : 8
        height: width
        color: overlay.draftColor
        border.color: "white"
        x: overlay.draftVertices.length
           ? overlay.toItemX(overlay.draftVertices[0][0]) - width / 2 : 0
        y: overlay.draftVertices.length
           ? overlay.toItemY(overlay.draftVertices[0][1]) - height / 2 : 0
    }
}

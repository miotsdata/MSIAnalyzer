import QtQuick
import "qrc:/Style"

// The interactive ROI-drawing surface: a `ZoomableImage` (so the sample's
// heatmap can be panned/zoomed while drawing) with a `RoiOverlay` and the
// vertex-placement handlers declared as its `overlayContent` — placing
// them there (rather than anchored on top of the whole component) puts
// them in the exact same scrolling/scaled coordinate frame as the image
// itself, so a tap position needs no separate offset math to become a
// grid-index vertex: `floor(position / effectiveScale)`.
//
// `TapHandler`, not `MouseArea`, for vertex placement — a `MouseArea`
// would grab the press before the enclosing `Flickable`'s own
// drag-to-pan ever sees it; `TapHandler` only fires on a genuine tap
// (press+release with negligible movement) and coexists with the pan
// gesture instead of stealing it.
//
// Zooming (mouse wheel, via ZoomableImage's own WheelHandler) always
// works, independent of `drawingEnabled` — RoiDesignWindow also exposes
// explicit +/−/reset buttons bound to `zoom` directly, since a working,
// discoverable way to zoom in to place vertices precisely is required,
// not just a convenience.
Item {
    id: canvas
    objectName: "roiDrawingCanvas"

    property alias source: zoomImage.source
    property alias zoom: zoomImage.zoom
    property var savedRois: []
    property var draftVertices: []
    property color draftColor: "white"
    // While false (idle/naming, before "Start Drawing"), taps don't place
    // vertices — only panning/zooming the image is active.
    property bool drawingEnabled: true

    // Fired on every tap that isn't a "close" tap — canvas.addVertex(col, row)
    // in RoiDesignWindow.qml appends it to draftVertices.
    signal vertexRequested(real col, real row)
    // Fired when the user taps the (highlighted) first-vertex affordance
    // with >= 3 vertices already placed.
    signal closePolygonRequested()

    // A fixed *screen*-pixel radius (not grid-space) so the close target
    // stays a constant visual size regardless of zoom level.
    readonly property real closeThresholdPx: 10
    property bool _nearFirstVertex: false

    ZoomableImage {
        id: zoomImage
        objectName: "roiZoomableImage"
        anchors.fill: parent

        RoiOverlay {
            id: overlay
            objectName: "roiOverlay"
            anchors.fill: parent
            readOnly: false
            effectiveScale: parent.effectiveScale
            savedRois: canvas.savedRois
            draftVertices: canvas.draftVertices
            draftColor: canvas.draftColor
            firstVertexHighlighted: canvas._nearFirstVertex
        }

        HoverHandler {
            id: hoverHandler
            objectName: "roiHoverHandler"
            onPointChanged: {
                if (canvas.draftVertices.length === 0) {
                    canvas._nearFirstVertex = false
                    return
                }
                var fx = overlay.toItemX(canvas.draftVertices[0][0])
                var fy = overlay.toItemY(canvas.draftVertices[0][1])
                var dx = point.position.x - fx
                var dy = point.position.y - fy
                canvas._nearFirstVertex = Math.sqrt(dx * dx + dy * dy) <= canvas.closeThresholdPx
            }
        }

        TapHandler {
            id: tapHandler
            objectName: "roiTapHandler"
            enabled: canvas.drawingEnabled
            onTapped: (eventPoint) => {
                var col = Math.floor(eventPoint.position.x / zoomImage.effectiveScale)
                var row = Math.floor(eventPoint.position.y / zoomImage.effectiveScale)
                if (canvas._nearFirstVertex && canvas.draftVertices.length >= 3) {
                    canvas.closePolygonRequested()
                } else {
                    canvas.vertexRequested(col, row)
                }
            }
        }
    }
}

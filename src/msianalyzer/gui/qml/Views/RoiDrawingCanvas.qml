import QtQuick
import "qrc:/Style"
import "qrc:/Utils/AffineTransform.js" as AffineTransform

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

    // Draw-on-H&E mode (see RoiDesignWindow.qml's surface toggle):
    // `source` above is then the sample's H&E image
    // (`image://he_image/...`), and every vertex this canvas reports is
    // still in MSI grid-index space (`vertexRequested`'s contract never
    // changes) — a tap is converted He-pixel -> grid via `heToGridMatrix`
    // right here, and every vertex handed to `RoiOverlay` for display is
    // converted the other way (grid -> He-pixel) via that matrix's
    // inverse, since drawn geometry always lives in grid space (RoiDesignWindow's
    // `draftVertices`/`savedRois` never change shape between modes).
    property bool heMode: false
    // `[[a, b, c], [d, e, f]]`, He-pixel -> grid-index — see
    // `AnalysisBridge.getRegistrationInfo`'s `matrix` field. `null` when
    // the selected sample has no fitted registration (heMode should be
    // false in that case, enforced by RoiDesignWindow, not here).
    property var heToGridMatrix: null
    // The MSI heatmap warped into H&E pixel space
    // (`image://he_overlay/...`, see core/registration/overlay.py) shown
    // faintly under the drawing overlay while in heMode, so a user can
    // still see the underlying signal while drawing on tissue morphology.
    property string heOverlaySource: ""

    readonly property var _gridToHeMatrix: (canvas.heMode && canvas.heToGridMatrix)
        ? AffineTransform.invert(canvas.heToGridMatrix) : null
    // A grid-space vertex `[col, row]`, mapped to this canvas' own
    // display space — unchanged in grid mode (`_gridToHeMatrix` is null),
    // He-pixel coordinates in heMode.
    function _displayVertex(v) {
        if (!canvas._gridToHeMatrix) return v
        var p = AffineTransform.apply(canvas._gridToHeMatrix, v[0], v[1])
        return [p.x, p.y]
    }
    readonly property var _displayDraftVertices: canvas.draftVertices.map(canvas._displayVertex)
    readonly property var _displaySavedRois: canvas.savedRois.map(function (r) {
        return {
            name: r.name, color: r.color,
            vertices: r.vertices.map(canvas._displayVertex),
        }
    })

    // Fired on every tap that isn't a "close" tap — canvas.addVertex(col, row)
    // in RoiDesignWindow.qml appends it to draftVertices. Always
    // grid-index coordinates, even in heMode (see heMode's own comment).
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

        Image {
            objectName: "roiHeatmapOverlayImage"
            anchors.fill: parent
            visible: canvas.heMode && canvas.heOverlaySource !== ""
            opacity: 0.6
            smooth: false
            cache: false
            asynchronous: false
            source: canvas.heMode ? canvas.heOverlaySource : ""
        }

        RoiOverlay {
            id: overlay
            objectName: "roiOverlay"
            anchors.fill: parent
            readOnly: false
            pixelSpace: canvas.heMode
            effectiveScale: parent.effectiveScale
            savedRois: canvas._displaySavedRois
            draftVertices: canvas._displayDraftVertices
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
                var first = canvas._displayVertex(canvas.draftVertices[0])
                var fx = overlay.toItemX(first[0])
                var fy = overlay.toItemY(first[1])
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
                if (canvas._nearFirstVertex && canvas.draftVertices.length >= 3) {
                    canvas.closePolygonRequested()
                    return
                }
                if (canvas.heMode && canvas.heToGridMatrix) {
                    var hex = eventPoint.position.x / zoomImage.effectiveScale
                    var hey = eventPoint.position.y / zoomImage.effectiveScale
                    var g = AffineTransform.apply(canvas.heToGridMatrix, hex, hey)
                    canvas.vertexRequested(g.x, g.y)
                } else {
                    var col = Math.floor(eventPoint.position.x / zoomImage.effectiveScale)
                    var row = Math.floor(eventPoint.position.y / zoomImage.effectiveScale)
                    canvas.vertexRequested(col, row)
                }
            }
        }
    }
}

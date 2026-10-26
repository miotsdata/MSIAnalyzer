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
    readonly property alias imageSize: zoomImage.imageSize
    property alias scaleBarUm: zoomImage.scaleBarUm
    property alias scaleBarPixelUm: zoomImage.scaleBarPixelUm
    property alias scaleBarColor: zoomImage.scaleBarColor
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
    // Same show/opacity controls as CoregistrationWindow's overlay —
    // RoiDesignWindow exposes them as a checkbox + slider next to "Draw
    // on H&E image", so the balance between tissue morphology and
    // chemical signal is adjustable while drawing here too.
    property bool showOverlay: true
    property real overlayOpacity: 0.6

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

    // Selecting and moving saved ROIs (ADR 58), only while not drawing.
    // A tap inside a saved ROI selects it (the topmost one), a tap on
    // empty space deselects (""). Dragging inside the selected ROI moves
    // it: the image's own drag-to-pan is paused while the pointer is over
    // it, so the drag reaches `moveDragHandler` instead of the Flickable.
    property bool selectionEnabled: false
    property string selectedRoiName: ""
    signal roiTapped(string name)
    // The finished drag, always in grid-index units (whole cells on the
    // grid surface; converted from H&E pixels in heMode).
    signal moveRequested(real dCol, real dRow)
    // Whether the pointer is over the selected ROI, which pauses the
    // image's pan. Refreshed on hover, on every press (a real compositor
    // may deliver no hover between selecting and dragging, and then the
    // image took the drag) and whenever the selection itself changes.
    property bool _pointerInSelected: false
    function _refreshPointerInSelected(x, y) {
        canvas._pointerInSelected = canvas.selectionEnabled && canvas._insideSelected(x, y)
    }
    onSelectedRoiNameChanged: {
        if (hoverHandler.hovered)
            canvas._refreshPointerInSelected(hoverHandler.point.position.x, hoverHandler.point.position.y)
        else
            canvas._pointerInSelected = false
    }

    // Ray-casting point-in-polygon, in overlay item pixels.
    function _contains(vertices, x, y) {
        var inside = false
        for (var i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
            var xi = overlay.toItemX(vertices[i][0]), yi = overlay.toItemY(vertices[i][1])
            var xj = overlay.toItemX(vertices[j][0]), yj = overlay.toItemY(vertices[j][1])
            if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi)
                inside = !inside
        }
        return inside
    }
    function _area(vertices) {
        var a = 0
        for (var i = 0, j = vertices.length - 1; i < vertices.length; j = i++)
            a += (vertices[j][0] + vertices[i][0]) * (vertices[j][1] - vertices[i][1])
        return Math.abs(a / 2)
    }
    // The smallest ROI containing the point, so an ROI drawn inside
    // another one can be picked by clicking it (the outer one by clicking
    // outside the inner one), whatever their drawing order.
    function _roiAt(x, y) {
        var best = "", bestArea = Infinity
        canvas._displaySavedRois.forEach(function (r) {
            var a = canvas._area(r.vertices)
            if (a < bestArea && canvas._contains(r.vertices, x, y)) {
                best = r.name
                bestArea = a
            }
        })
        return best
    }
    // Arrow-key moves not saved yet (grid units, set by RoiDesignWindow):
    // the selected ROI is drawn shifted by them, and hit-tested there.
    property point nudgeCells: Qt.point(0, 0)
    readonly property point _nudgePx: {
        var s = zoomImage.effectiveScale
        if (!canvas._gridToHeMatrix)
            return Qt.point(canvas.nudgeCells.x * s, canvas.nudgeCells.y * s)
        // Only the linear part of the grid -> He-pixel affine applies to
        // a displacement.
        var a = AffineTransform.apply(canvas._gridToHeMatrix, canvas.nudgeCells.x, canvas.nudgeCells.y)
        var o = AffineTransform.apply(canvas._gridToHeMatrix, 0, 0)
        return Qt.point((a.x - o.x) * s, (a.y - o.y) * s)
    }
    // The drag in progress, item pixels (moveDragHandler).
    property point _dragPx: Qt.point(0, 0)

    // Tested against the selected ROI's own outline, not "the ROI under
    // the pointer": a selected ROI overlapped by another must still move.
    function _insideSelected(x, y) {
        var rois = canvas._displaySavedRois
        for (var i = 0; i < rois.length; i++) {
            if (rois[i].name === canvas.selectedRoiName)
                return canvas._contains(rois[i].vertices, x - canvas._nudgePx.x, y - canvas._nudgePx.y)
        }
        return false
    }
    // Item-pixel drag translation -> what the preview shows. On the grid
    // surface it snaps to whole cells, so the preview is exactly where
    // the ROI will land.
    function _snappedOffset(t) {
        if (canvas.heMode) return t
        var s = zoomImage.effectiveScale
        return Qt.point(Math.round(t.x / s) * s, Math.round(t.y / s) * s)
    }
    function _finishMove(t) {
        var s = zoomImage.effectiveScale
        var dx = t.x / s, dy = t.y / s
        if (canvas.heMode && canvas.heToGridMatrix) {
            // Only the linear part of the He-pixel -> grid affine applies
            // to a displacement.
            var m = canvas.heToGridMatrix
            canvas.moveRequested(m[0][0] * dx + m[0][1] * dy, m[1][0] * dx + m[1][1] * dy)
        } else {
            canvas.moveRequested(Math.round(dx), Math.round(dy))
        }
    }

    // A fixed *screen*-pixel radius (not grid-space) so the close target
    // stays a constant visual size regardless of zoom level.
    readonly property real closeThresholdPx: 10
    property bool _nearFirstVertex: false

    ZoomableImage {
        id: zoomImage
        objectName: "roiZoomableImage"
        anchors.fill: parent
        interactive: !(canvas._pointerInSelected || moveDragHandler.active)

        Image {
            objectName: "roiHeatmapOverlayImage"
            anchors.fill: parent
            visible: canvas.heMode && canvas.showOverlay && canvas.heOverlaySource !== ""
            opacity: canvas.overlayOpacity
            smooth: false
            cache: false
            // image://he_overlay is an async provider (ADR 50): it renders off
            // the GUI thread regardless of this setting.
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
            selectedName: canvas.selectedRoiName
            moveOffset: Qt.point(canvas._nudgePx.x + canvas._dragPx.x, canvas._nudgePx.y + canvas._dragPx.y)
        }

        HoverHandler {
            id: hoverHandler
            objectName: "roiHoverHandler"
            // The move cursor tells the user a drag here moves the ROI.
            cursorShape: canvas._pointerInSelected ? Qt.SizeAllCursor : Qt.ArrowCursor
            onPointChanged: {
                canvas._refreshPointerInSelected(point.position.x, point.position.y)
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

        TapHandler {
            objectName: "roiSelectTapHandler"
            enabled: canvas.selectionEnabled
            onPressedChanged: {
                if (pressed) canvas._refreshPointerInSelected(point.position.x, point.position.y)
            }
            onTapped: (eventPoint) => canvas.roiTapped(
                canvas._roiAt(eventPoint.position.x, eventPoint.position.y))
        }

        DragHandler {
            id: moveDragHandler
            objectName: "roiMoveDragHandler"
            target: null
            enabled: canvas.selectionEnabled && canvas.selectedRoiName !== ""
            // Whether this drag began inside the selected ROI; a drag
            // starting anywhere else is left alone.
            property bool _moving: false
            property point _last: Qt.point(0, 0)
            onActiveChanged: {
                if (active) {
                    _moving = canvas._insideSelected(centroid.pressPosition.x, centroid.pressPosition.y)
                    _last = Qt.point(0, 0)
                    return
                }
                if (_moving && (_last.x !== 0 || _last.y !== 0)) canvas._finishMove(_last)
                canvas._dragPx = Qt.point(0, 0)
                _moving = false
            }
            onActiveTranslationChanged: {
                if (!active || !_moving) return
                _last = canvas._snappedOffset(activeTranslation)
                canvas._dragPx = _last
            }
        }
    }
}

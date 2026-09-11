import QtQuick

Flickable {
    id: root
    objectName: "zoomableImage"

    property alias source: image.source
    property real zoom: 1.0
    readonly property real minZoom: 1.0
    readonly property real maxZoom: 8.0

    clip: true
    boundsBehavior: Flickable.StopAtBounds
    contentWidth: Math.max(width, image.sourceSize.width * zoom)
    contentHeight: Math.max(height, image.sourceSize.height * zoom)

    onZoomChanged: {
        if (zoom < minZoom) zoom = minZoom
        else if (zoom > maxZoom) zoom = maxZoom
    }

    Image {
        id: image
        objectName: "zoomableImageContent"
        width: root.contentWidth
        height: root.contentHeight
        fillMode: Image.Stretch
        smooth: true
        cache: false
        // Deliberately synchronous: `asynchronous: true` runs
        // HeatmapImageProvider.requestImage() on Qt's background image
        // thread pool, and with 2+ visible tiles that means 2+ concurrent
        // calls into the same Python-implemented provider — reproduced as
        // an intermittent hang inside AnalysisPage's QML construction
        // (bisected to exactly this) whenever a test seeded more than one
        // sample. Root cause not fully pinned down (most likely matplotlib
        // colormap/Normalize global state under concurrent access, or a
        // PySide6 cross-thread callback edge case) but synchronous loading
        // reproduced zero hangs across 25+ full-suite stress runs, vs.
        // ~30-50% with async on. These tiles are small rasters (one
        // pixel per spatial coordinate) so synchronous rendering doesn't
        // meaningfully block the UI.
        asynchronous: false
    }

    WheelHandler {
        acceptedModifiers: Qt.NoModifier
        onWheel: (event) => {
            root.zoom *= event.angleDelta.y > 0 ? 1.15 : (1 / 1.15)
        }
    }
}

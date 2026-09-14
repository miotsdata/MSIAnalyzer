import QtQuick

Flickable {
    id: root
    objectName: "zoomableImage"

    property alias source: image.source
    property real zoom: 1.0
    readonly property real minZoom: 1.0
    readonly property real maxZoom: 8.0

    // Independently maxing width and height against the viewport (the
    // old contentWidth/Height) stretches the image to whatever box shape
    // the tile happens to be, distorting it unless the source's aspect
    // ratio exactly matches — this fits the *whole* image inside the
    // viewport at zoom 1 instead, preserving its aspect ratio, and scales
    // that uniformly as zoom increases.
    readonly property real baseScale: (image.sourceSize.width > 0 && image.sourceSize.height > 0)
        ? Math.min(root.width / image.sourceSize.width, root.height / image.sourceSize.height)
        : 1.0
    readonly property real effectiveScale: baseScale * zoom

    clip: true
    boundsBehavior: Flickable.StopAtBounds
    contentWidth: Math.max(width, image.sourceSize.width * effectiveScale)
    contentHeight: Math.max(height, image.sourceSize.height * effectiveScale)

    onZoomChanged: {
        if (zoom < minZoom) zoom = minZoom
        else if (zoom > maxZoom) zoom = maxZoom
    }

    // An insertion point for an overlay (ROI borders/draft polygon, see
    // RoiOverlay.qml) that must track the Image's own pan/zoom transform
    // exactly. Declaring children here — e.g. `ZoomableImage { RoiOverlay
    // { ... } }` — places them inside this Flickable's own scrolling
    // content, positioned/sized to match the Image, so they get correct
    // pan/zoom behavior for free with no manual mapToItem bookkeeping.
    // Additive: existing usages with no such children are unaffected.
    default property alias overlayContent: overlayHolder.data

    Image {
        id: image
        objectName: "zoomableImageContent"
        width: sourceSize.width * root.effectiveScale
        height: sourceSize.height * root.effectiveScale
        // Centered when smaller than the viewport (contentWidth/Height
        // floor at the viewport size) rather than stuck in the top-left
        // corner with blank space around it.
        x: Math.max(0, (root.contentWidth - width) / 2)
        y: Math.max(0, (root.contentHeight - height) / 2)
        fillMode: Image.PreserveAspectFit
        // Each source pixel is one spatial coordinate's worth of real
        // data — smooth (bilinear) interpolation blurred that into soft
        // blobs once scaled up past the tile's modest source resolution
        // ("out of focus"/"zoomed in" — reported as such). Off keeps
        // pixels crisp at any zoom level, which is what you actually
        // want for reading discrete per-pixel values off a heatmap.
        smooth: false
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

    // Exactly tracks the Image's own position/size — overlay children
    // placed here (via `overlayContent` above) sit in the same coordinate
    // frame as the image, so `(col + 0.5) * effectiveScale` (forwarded
    // below) lands exactly on the right raster pixel with no separate
    // offset math.
    Item {
        id: overlayHolder
        objectName: "zoomableImageOverlayHolder"
        x: image.x
        y: image.y
        width: image.width
        height: image.height
        property alias effectiveScale: root.effectiveScale
    }

    WheelHandler {
        acceptedModifiers: Qt.NoModifier
        onWheel: (event) => {
            root.zoom *= event.angleDelta.y > 0 ? 1.15 : (1 / 1.15)
        }
    }
}

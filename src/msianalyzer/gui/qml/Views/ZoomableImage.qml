import QtQuick
import QtQuick.Controls

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
    // One source pixel per spatial pixel, so this is the sample's own
    // width x height in pixels once loaded (0x0 before).
    readonly property size imageSize: image.sourceSize

    // Scale bar (ADR 53): hidden while either length is 0 (bar off, or
    // this sample's pixel size unknown). Pixels are assumed square.
    property real scaleBarUm: 0
    property real scaleBarPixelUm: 0
    property color scaleBarColor: "white"

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
        // Falls back to filling the viewport when there's no real source
        // size yet (source is "", still loading, or failed — status !==
        // Ready) rather than collapsing to 0x0: an image element sized
        // exactly 0x0 was found to silently swallow every tap/click
        // anywhere in this Flickable — not just on the image itself —
        // for every PointerHandler declared inside it (confirmed with a
        // minimal reproduction outside this app: a sibling TapHandler
        // stops firing the moment this Image's own size hits (0, 0), even
        // though the enclosing Flickable's own width/height stay
        // nonzero). Previously unnoticed because every existing caller
        // (Visual Inspection's heatmap tiles, ROI Design) always has a
        // real image by the time a user can click — CoregistrationWindow
        // is the first caller that can legitimately open with a pane
        // whose image hasn't resolved yet.
        width: image.status === Image.Ready ? sourceSize.width * root.effectiveScale : root.width
        height: image.status === Image.Ready ? sourceSize.height * root.effectiveScale : root.height
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
        // Heatmap tiles come from HeatmapImageProvider, a
        // QQuickAsyncImageProvider (ADR 50), so they always load off the
        // GUI thread whatever `asynchronous` says. They render one at a
        // time on the provider's own single render thread. This replaced
        // `asynchronous: false`, which ran every render (including a
        // cold, multi-second .h5ad read) on the GUI thread and froze the
        // app across a 16-sample grid. The old reason for keeping it
        // synchronous (concurrent Python renders hanging) doesn't apply:
        // the provider never renders concurrently.
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

    // Shown while this tile's image is being rendered. Parented to the
    // Flickable itself, not its content, so it stays centred in view
    // rather than scrolling with the image.
    BusyIndicator {
        objectName: "zoomableImageBusy"
        parent: root
        anchors.centerIn: parent
        width: Math.min(40, root.width / 3)
        height: width
        running: image.status === Image.Loading
        visible: running
    }

    // Parented to the Flickable like the BusyIndicator above, so it doesn't
    // scroll away while panning. Placed at the bottom-right of the *visible
    // part of the image*, not of the viewport: the image is centered with
    // its aspect ratio kept, so the viewport's own corner is often blank
    // margin outside the heatmap.
    Column {
        objectName: "scaleBar"
        parent: root
        readonly property real margin: 8
        x: Math.min(root.width, image.x + image.width - root.contentX) - width - margin
        y: Math.min(root.height, image.y + image.height - root.contentY) - height - margin
        spacing: 2
        visible: root.scaleBarUm > 0 && root.scaleBarPixelUm > 0 && image.status === Image.Ready

        Label {
            objectName: "scaleBarLabel"
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.scaleBarUm + " µm"
            color: root.scaleBarColor
            font.bold: true
        }
        Rectangle {
            objectName: "scaleBarRect"
            width: root.scaleBarPixelUm > 0
                   ? root.scaleBarUm / root.scaleBarPixelUm * root.effectiveScale : 0
            height: 4
            color: root.scaleBarColor
        }
    }

    WheelHandler {
        acceptedModifiers: Qt.NoModifier
        onWheel: (event) => {
            root.zoom *= event.angleDelta.y > 0 ? 1.15 : (1 / 1.15)
        }
    }
}

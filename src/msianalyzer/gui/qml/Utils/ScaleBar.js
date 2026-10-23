.pragma library

// Scale bar settings (ADR 53, AnalysisBridge.getScaleBar's shape) -> one
// sample's pixel size in µm, or 0 when the bar is off or the size unknown
// (ZoomableImage hides its bar at 0).
function pixelUm(settings, sampleName) {
    if (!settings || !settings.enabled || !settings.pixel_um) return 0
    var v = Number(settings.pixel_um[sampleName])
    return v > 0 ? v : 0
}

// "W × H px" for a loaded image's sourceSize, "" before it loads.
function sizeText(size) {
    return (size && size.width > 0) ? size.width + " × " + size.height + " px" : ""
}

// sizeText as a " · "-separated title suffix.
function sizeSuffix(size) {
    var text = sizeText(size)
    return text ? " · " + text : ""
}

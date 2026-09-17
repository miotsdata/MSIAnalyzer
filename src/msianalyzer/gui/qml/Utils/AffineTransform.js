.pragma library

// JS-side mirror of core/registration/transform.py's apply_transform/
// invert_transform — needed client-side (not round-tripped through
// Python) for live rendering feedback while drawing an ROI on the H&E
// pane (RoiDrawingCanvas.qml) and, in the future, similar coregistration
// UI: converting a clicked point / a stored polygon vertex between H&E
// pixel space and MSI grid-index space on every tap/repaint would be far
// too slow (and clunky) as a bridge round-trip.
//
// A matrix is always `[[a, b, c], [d, e, f]]` (same shape
// `AnalysisBridge.getRegistrationInfo`'s `matrix` field returns), such
// that `apply(matrix, x, y)` computes `(a*x + b*y + c, d*x + e*y + f)`.

function apply(matrix, x, y) {
    return {
        x: matrix[0][0] * x + matrix[0][1] * y + matrix[0][2],
        y: matrix[1][0] * x + matrix[1][1] * y + matrix[1][2],
    }
}

function invert(matrix) {
    var a = matrix[0][0], b = matrix[0][1], c = matrix[0][2]
    var d = matrix[1][0], e = matrix[1][1], f = matrix[1][2]
    var det = a * e - b * d
    var ia = e / det
    var ib = -b / det
    var id = -d / det
    var ie = a / det
    var ic = -(ia * c + ib * f)
    var iff = -(id * c + ie * f)
    return [[ia, ib, ic], [id, ie, iff]]
}

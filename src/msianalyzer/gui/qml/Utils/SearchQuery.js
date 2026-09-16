.pragma library

// Shared compound-search matching for the Annotate table and Visual
// Inspection's feature picker — one field for name, one for m/z (ADR 34;
// originally one combined auto-detecting box, ADR 29, split after real
// use turned up ambiguous cases: a short/numeric-looking compound name).
const MZ_TOLERANCE = 0.01

// Empty/whitespace: matches everything. Otherwise: case-insensitive
// substring match against `name` (never matches when `name` is
// empty/undefined — an unannotated feature has nothing to match against).
function matchesName(query, name) {
    var q = (query || "").trim()
    if (q === "")
        return true
    if (!name)
        return false
    return name.toLowerCase().indexOf(q.toLowerCase()) !== -1
}

// Empty/whitespace: matches everything. "<num>-<num>" (optional
// whitespace around the "-"): mz within [min, max] (order-independent, so
// "160-150" works the same as "150-160"). A single parseable number: mz
// within +/- MZ_TOLERANCE of it. Anything else unparseable: matches
// nothing (an m/z field isn't a name field).
function matchesMz(query, mz) {
    var q = (query || "").trim()
    if (q === "")
        return true

    var rangeMatch = q.match(/^(-?[0-9.]+)\s*-\s*(-?[0-9.]+)$/)
    if (rangeMatch) {
        var a = parseFloat(rangeMatch[1])
        var b = parseFloat(rangeMatch[2])
        if (!isNaN(a) && !isNaN(b)) {
            var lo = Math.min(a, b)
            var hi = Math.max(a, b)
            return mz !== null && mz !== undefined && mz >= lo && mz <= hi
        }
    }

    var single = parseFloat(q)
    if (!isNaN(single) && /^-?[0-9.]+$/.test(q)) {
        return mz !== null && mz !== undefined && Math.abs(mz - single) <= MZ_TOLERANCE
    }

    return false
}

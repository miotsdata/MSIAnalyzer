.pragma library

// Reading and setting one config field's control by its schema `kind`
// (see config_schema.py), shared by NewAnalysisPage and ConfigGroupForm.

// Coerces a `path_list`-shaped config value (`null` / a bare string /
// an array) into a plain JS array of strings. An array reaching QML
// from Python (e.g. a loaded run's `annotate.library_path`) crosses
// the Python/QML boundary as an array-*like* object that does NOT
// reliably satisfy `Array.isArray()` — iterating by `.length`/index
// instead is what actually works. `applyFieldValue`'s old
// `Array.isArray(value) ? value.join(...) : (value || "")` fallback
// silently mis-stringified such a value (reported: a 2-library config
// reloaded as `"//"` instead of the two joined paths) — traced with a
// debug property dumping `typeof`/`Array.isArray`/`JSON.stringify` on
// the actual value QML received.
function normalizePathListValue(value) {
    if (value === null || value === undefined || value === "")
        return []
    if (typeof value === "string")
        return [value]
    var out = []
    for (var i = 0; i < value.length; i++)
        out.push(value[i])
    return out
}

function parseFieldValue(kind, control) {
    var text = control.text !== undefined ? control.text.trim() : ""
    switch (kind) {
    case "bool":
        return control.checked
    case "choice":
        return control.currentText
    case "int":
        return parseInt(text, 10)
    case "float":
        return parseFloat(text)
    case "str":
        return text
    case "optional_bool":
        return text === "" ? null : (text.toLowerCase() === "true")
    case "optional_int":
        return text === "" ? null : parseInt(text, 10)
    case "optional_float":
        return text === "" ? null : parseFloat(text)
    case "optional_str":
        return text === "" ? null : text
    case "optional_str_list":
        if (text === "")
            return null
        return text.split(",").map(function (s) { return s.trim() })
                   .filter(function (s) { return s.length > 0 })
    case "float_list":
        return text.split(",").map(function (s) { return parseFloat(s.trim()) })
                   .filter(function (s) { return !isNaN(s) })
    case "path_list":
        if (text === "")
            return null
        var parts = text.split(";").map(function (s) { return s.trim() })
                         .filter(function (s) { return s.length > 0 })
        return parts.length <= 1 ? (parts[0] || null) : parts
    }
    return null
}

// Inverse of parseFieldValue — sets a control's value from a loaded
// config's raw value, for the "load from a previous analysis"
// template picker (applyConfig below). Same kind-by-kind shape as
// parseFieldValue, deliberately the mirror image of it.
function applyFieldValue(kind, control, value) {
    switch (kind) {
    case "bool":
        control.checked = value === true
        return
    case "choice":
        control.currentIndex = Math.max(0, control.find(String(value)))
        return
    case "optional_bool":
        control.text = (value === null || value === undefined) ? "" : String(value)
        return
    case "int":
    case "float":
    case "str":
    case "optional_int":
    case "optional_float":
    case "optional_str":
        control.text = (value === null || value === undefined) ? "" : String(value)
        return
    case "optional_str_list":
        control.text = (value && value.length > 0) ? value.join(", ") : ""
        return
    case "float_list":
        control.text = (value && value.length > 0) ? value.join(", ") : ""
        return
    case "path_list":
        control.text = normalizePathListValue(value).join("; ")
        return
    }
}

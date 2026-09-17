import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"
import QtQuick.Dialogs

Page {
    id: newAnalysisPage
    objectName: "newAnalysisPage"

    property var project
    property var sampleRows: []
    // Already-parsed samples: each entry here is a raw .db path that
    // skips the parse/pixel-map pipeline stages entirely (see ADR — core
    // Run._process_one_sample branches on mzml_path === null). Kept as a
    // separate list rather than folded into sampleRows because these
    // samples have no mzML/XML pair at all, just one path.
    property var dbOnlyPaths: []
    // target_list.adducts, picked from a polarity-filtered multi-select
    // (see the bespoke target-list tab below) rather than typed in —
    // cleared whenever polarity changes, since the two label sets are
    // disjoint and a stale selection would just get rejected by
    // TargetListConfig.__post_init__ anyway.
    property var targetListSelectedAdducts: []

    function toggleTargetListAdduct(label, checked) {
        var arr = targetListSelectedAdducts.slice()
        var idx = arr.indexOf(label)
        if (checked && idx === -1)
            arr.push(label)
        else if (!checked && idx !== -1)
            arr.splice(idx, 1)
        targetListSelectedAdducts = arr
    }

    // Native dialogs (GTK/KDE portal on Linux): FolderDialog gets
    // create-folder support, FileDialog gets working multi-select — the
    // QML fallback's multi-select turned out not to actually work.
    // FileDialog was briefly reverted to the QML fallback unconditionally
    // after a real-session freeze, but that freeze may be tied to the
    // Material style switch rather than to native FileDialog itself
    // (reportedly still working, with multi-select, up through commits
    // before the theme change) — under investigation, native restored
    // here pending that.
    readonly property bool useNativeDialogs: Qt.platform.pluginName !== "offscreen"

    // ------------------------------------------------------------------ //
    // Display helpers: show a bare filename instead of a full path when
    // every sample's file (of that kind) lives in the same folder — the
    // shared folder is redundant noise once you've picked more than one.
    // ------------------------------------------------------------------ //

    function dirName(path) {
        var idx = path.lastIndexOf("/")
        return idx >= 0 ? path.substring(0, idx) : ""
    }

    function baseName(path) {
        var idx = path.lastIndexOf("/")
        return idx >= 0 ? path.substring(idx + 1) : path
    }

    // The shared directory of every non-empty path in `paths`, or null if
    // there isn't one (no paths yet, or they're spread across folders).
    function commonDir(paths) {
        var dir = null
        for (var i = 0; i < paths.length; i++) {
            if (paths[i] === "")
                continue
            var d = dirName(paths[i])
            if (dir === null)
                dir = d
            else if (d !== dir)
                return null
        }
        return dir
    }

    function displayPath(path, commonDirValue) {
        if (path === "")
            return ""
        return commonDirValue !== null ? baseName(path) : path
    }

    readonly property var mzmlCommonDir: commonDir(sampleRows.map(function (r) { return r.mzml }))
    readonly property var xmlCommonDir: commonDir(sampleRows.map(function (r) { return r.xml }))
    readonly property var dbOnlyCommonDir: commonDir(dbOnlyPaths)

    // ------------------------------------------------------------------ //
    // Sample-row bookkeeping (io.mzml_paths / io.xml_paths, kept paired)
    // ------------------------------------------------------------------ //

    function addSampleRow() {
        sampleRows = sampleRows.concat([{ mzml: "", xml: "" }])
    }

    function removeSampleRow(index) {
        var rows = sampleRows.slice()
        rows.splice(index, 1)
        sampleRows = rows
    }

    function setSampleField(index, key, value) {
        var rows = sampleRows.slice()
        var row = Object.assign({}, rows[index])
        row[key] = value
        rows[index] = row
        sampleRows = rows
    }

    // ------------------------------------------------------------------ //
    // annotate.library_path — listed + removable, like the sample-row
    // table above, not a semicolon-joined text field (that's still how
    // target_list.paths works; this field alone was redesigned).
    // ------------------------------------------------------------------ //

    property var libraryPaths: []

    function addLibraryPaths(paths) {
        var merged = libraryPaths.slice()
        for (var i = 0; i < paths.length; i++) {
            if (merged.indexOf(paths[i]) === -1)
                merged.push(paths[i])
        }
        libraryPaths = merged
    }

    function removeLibraryPath(index) {
        var paths = libraryPaths.slice()
        paths.splice(index, 1)
        libraryPaths = paths
    }

    // Bulk mzML: every selected path becomes its own new row (mzml set,
    // xml left blank) — the primary way to populate the table, so you
    // don't add rows one at a time before you can even pick a file.
    //
    // Builds the whole new array and assigns `sampleRows` exactly once —
    // NOT addSampleRow()+setSampleField() per path. Each of those
    // reassigns `sampleRows` on its own, and every reassignment is a new
    // array *reference*, which the Repeater rendering the table treats as
    // a brand new model and rebuilds every row for — fine for one path,
    // O(paths.length^2) row (re)construction for a real multi-file pick
    // (visibly slow past a few dozen files, hence this).
    function addMzmlPathsAsNewRows(paths) {
        var rows = sampleRows.slice()
        for (var i = 0; i < paths.length; i++) {
            rows.push({ mzml: paths[i], xml: "" })
        }
        sampleRows = rows
    }

    // Bulk XML: fills existing rows' xml top-to-bottom in selection order.
    // mzML and XML files aren't necessarily picked in matching order, so
    // this is expected to need fixing up afterward with swapField (the
    // per-row up/down arrows) rather than getting every pairing right the
    // first time. Single assignment at the end, same reasoning as
    // addMzmlPathsAsNewRows above.
    function addXmlPathsSequentially(paths) {
        var rows = sampleRows.slice()
        for (var i = 0; i < paths.length; i++) {
            if (i < rows.length) {
                var row = Object.assign({}, rows[i])
                row.xml = paths[i]
                rows[i] = row
            } else {
                rows.push({ mzml: "", xml: paths[i] })
            }
        }
        sampleRows = rows
    }

    // Swaps just one field (mzml or xml) between two adjacent rows, e.g.
    // to line up a row's xml with the correct mzml after a bulk add —
    // moving only one column's cell rather than the whole row.
    function swapField(index, otherIndex, key) {
        if (otherIndex < 0 || otherIndex >= sampleRows.length)
            return
        var rows = sampleRows.slice()
        var a = Object.assign({}, rows[index])
        var b = Object.assign({}, rows[otherIndex])
        var tmp = a[key]
        a[key] = b[key]
        b[key] = tmp
        rows[index] = a
        rows[otherIndex] = b
        sampleRows = rows
    }

    function allSampleRowsFilled() {
        for (var i = 0; i < sampleRows.length; i++) {
            if (!sampleRows[i].mzml || !sampleRows[i].xml)
                return false
        }
        return true
    }

    // ------------------------------------------------------------------ //
    // Already-parsed db-only samples (io.db_paths, paired with a `null`
    // entry in mzml_paths/xml_paths at the same index — see collectConfig)
    // ------------------------------------------------------------------ //

    function addDbPathsAsNewEntries(paths) {
        dbOnlyPaths = dbOnlyPaths.concat(paths)
    }

    function removeDbOnlyPath(index) {
        var paths = dbOnlyPaths.slice()
        paths.splice(index, 1)
        dbOnlyPaths = paths
    }

    // ------------------------------------------------------------------ //
    // Generic-field lookup / value parsing (fields tab is data-driven from
    // ConfigSchema, see gui/utils/config_schema.py)
    // ------------------------------------------------------------------ //

    function findByObjectName(item, name) {
        for (var i = 0; i < item.children.length; i++) {
            var child = item.children[i]
            if (child.objectName === name)
                return child
            var found = findByObjectName(child, name)
            if (found)
                return found
        }
        return null
    }

    // Whether a field's control should be enabled, per its schema-declared
    // `enabledWhenField`/`enabledWhenEquals` (see config_schema.py's
    // `build_config_schema` docstring) — e.g. peak.filter_mad_log is only
    // enabled while peak.filter_mad is checked.
    function isFieldEnabled(groupKey, field) {
        if (!field.enabledWhenField)
            return true
        var control = findByObjectName(
            newAnalysisPage, "field_" + groupKey + "_" + field.enabledWhenField)
        if (!control)
            return true
        return control.checked === field.enabledWhenEquals
    }

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
        case "path_list":
            control.text = normalizePathListValue(value).join("; ")
            return
        }
    }

    function collectConfig() {
        var mzmlPaths = []
        var xmlPaths = []
        var dbPaths = []
        for (var i = 0; i < sampleRows.length; i++) {
            mzmlPaths.push(sampleRows[i].mzml)
            xmlPaths.push(sampleRows[i].xml)
            dbPaths.push(null)
        }
        // Already-parsed samples: null mzml/xml entries, real db_paths
        // entry at the same index — this is the sentinel core's
        // Run._process_one_sample checks for (see IOConfig docstring).
        for (var d = 0; d < dbOnlyPaths.length; d++) {
            mzmlPaths.push(null)
            xmlPaths.push(null)
            dbPaths.push(dbOnlyPaths[d])
        }

        var configDict = {}
        configDict["io"] = {
            "project_folder": project ? project.folder : "",
            "mzml_paths": mzmlPaths,
            "xml_paths": xmlPaths,
            "db_paths": dbPaths,
            "out_dir": outDirField.text
        }

        configDict["target_list"] = {
            "paths": parseFieldValue("path_list", targetListPathsField),
            "polarity": targetListPolarityCombo.currentText,
            "adducts": targetListSelectedAdducts.length > 0
                ? targetListSelectedAdducts : null,
            "match_ppm": parseFloat(targetListMatchPpmField.text)
        }

        for (var g = 0; g < ConfigSchema.groups.length; g++) {
            var group = ConfigSchema.groups[g]
            var groupDict = {}
            for (var f = 0; f < group.fields.length; f++) {
                var field = group.fields[f]
                // library_path is list+remove state (newAnalysisPage.libraryPaths),
                // not a control's .text — see that property's own comment.
                if (field.kind === "path_list") {
                    groupDict[field.name] = newAnalysisPage.libraryPaths.length === 0 ? null
                        : (newAnalysisPage.libraryPaths.length === 1
                           ? newAnalysisPage.libraryPaths[0] : newAnalysisPage.libraryPaths.slice())
                    continue
                }
                var control = findByObjectName(
                    newAnalysisPage, "field_" + group.key + "_" + field.name)
                groupDict[field.name] = control
                    ? parseFieldValue(field.kind, control) : field.default
            }
            configDict[group.key] = groupDict
        }

        return configDict
    }

    // "Load from a previous analysis": prefills every tab from another
    // run's saved Config.to_dict() (project.runs[id].config, already a
    // plain dict — see ProjectModel.runs). Deliberately does NOT go
    // through Config.from_dict (that's strict: an unknown/removed field
    // raises) — a field the loaded config doesn't have is just left at
    // its schema default, and a field this form no longer has a control
    // for is silently skipped, since the user is about to review every
    // tab anyway, not start a run sight-unseen.
    function applyConfig(configDict) {
        var io = configDict.io || {}
        var mzmlPaths = io.mzml_paths || []
        var xmlPaths = io.xml_paths || []
        var dbPaths = io.db_paths || []
        var rows = []
        var dbOnly = []
        for (var i = 0; i < mzmlPaths.length; i++) {
            if (mzmlPaths[i] !== null && mzmlPaths[i] !== undefined)
                rows.push({mzml: mzmlPaths[i], xml: xmlPaths[i] || ""})
            else
                dbOnly.push(dbPaths[i])
        }
        sampleRows = rows
        dbOnlyPaths = dbOnly
        outDirField.text = io.out_dir || (project ? project.folder + "/output" : "")

        var tl = configDict.target_list || {}
        if (tl.paths !== undefined)
            applyFieldValue("path_list", targetListPathsField, tl.paths)
        // Polarity before adducts — the combo's own onCurrentTextChanged
        // clears targetListSelectedAdducts on every polarity change
        // (built this session, for the manual-edit case), which would
        // otherwise immediately wipe out an adducts list set first.
        if (tl.polarity)
            targetListPolarityCombo.currentIndex = tl.polarity === "negative" ? 1 : 0
        targetListSelectedAdducts = tl.adducts || []
        if (tl.match_ppm !== undefined)
            targetListMatchPpmField.text = String(tl.match_ppm)

        for (var g = 0; g < ConfigSchema.groups.length; g++) {
            var group = ConfigSchema.groups[g]
            var groupValues = configDict[group.key]
            if (!groupValues)
                continue
            for (var f = 0; f < group.fields.length; f++) {
                var field = group.fields[f]
                if (!(field.name in groupValues))
                    continue
                if (field.kind === "path_list") {
                    newAnalysisPage.libraryPaths = normalizePathListValue(groupValues[field.name])
                    continue
                }
                var control = findByObjectName(
                    newAnalysisPage, "field_" + group.key + "_" + field.name)
                if (control)
                    applyFieldValue(field.kind, control, groupValues[field.name])
            }
        }
    }

    // The template picker's "Start blank" entry — resets every tab back
    // to its schema defaults, undoing whatever applyConfig last loaded.
    function resetToBlank() {
        sampleRows = []
        dbOnlyPaths = []
        outDirField.text = project ? project.folder + "/output" : ""

        applyFieldValue("path_list", targetListPathsField, null)
        targetListPolarityCombo.currentIndex = 0
        targetListSelectedAdducts = []
        targetListMatchPpmField.text = String(ConfigSchema.targetListSchema.fields.match_ppm.default)

        newAnalysisPage.libraryPaths = []

        for (var g = 0; g < ConfigSchema.groups.length; g++) {
            var group = ConfigSchema.groups[g]
            for (var f = 0; f < group.fields.length; f++) {
                var field = group.fields[f]
                if (field.kind === "path_list")
                    continue
                var control = findByObjectName(
                    newAnalysisPage, "field_" + group.key + "_" + field.name)
                if (control)
                    applyFieldValue(field.kind, control, field.default)
            }
        }
    }

    QtObject {
        id: mzmlDialogTarget
        property int rowIndex: -1
    }
    QtObject {
        id: xmlDialogTarget
        property int rowIndex: -1
    }

    FileDialog {
        id: mzmlDialog
        objectName: "mzmlDialog"
        options: newAnalysisPage.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        nameFilters: ["mzML files (*.mzML *.mzml)", "All files (*)"]
        onAccepted: newAnalysisPage.setSampleField(
            mzmlDialogTarget.rowIndex, "mzml", Router.toLocalPath(selectedFile))
    }
    FileDialog {
        id: xmlDialog
        objectName: "xmlDialog"
        options: newAnalysisPage.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        nameFilters: ["Raster XML (*.xml)", "All files (*)"]
        onAccepted: newAnalysisPage.setSampleField(
            xmlDialogTarget.rowIndex, "xml", Router.toLocalPath(selectedFile))
    }
    FileDialog {
        id: bulkMzmlDialog
        objectName: "bulkMzmlDialog"
        options: newAnalysisPage.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        fileMode: FileDialog.OpenFiles
        nameFilters: ["mzML files (*.mzML *.mzml)", "All files (*)"]
        onAccepted: {
            var paths = []
            for (var i = 0; i < selectedFiles.length; i++)
                paths.push(Router.toLocalPath(selectedFiles[i]))
            newAnalysisPage.addMzmlPathsAsNewRows(paths)
        }
    }
    FileDialog {
        id: bulkXmlDialog
        objectName: "bulkXmlDialog"
        options: newAnalysisPage.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Raster XML (*.xml)", "All files (*)"]
        onAccepted: {
            var paths = []
            for (var i = 0; i < selectedFiles.length; i++)
                paths.push(Router.toLocalPath(selectedFiles[i]))
            newAnalysisPage.addXmlPathsSequentially(paths)
        }
    }
    FileDialog {
        id: bulkDbDialog
        objectName: "bulkDbDialog"
        options: newAnalysisPage.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        fileMode: FileDialog.OpenFiles
        nameFilters: ["msianalyzer databases (*.db)", "All files (*)"]
        onAccepted: {
            var paths = []
            for (var i = 0; i < selectedFiles.length; i++)
                paths.push(Router.toLocalPath(selectedFiles[i]))
            newAnalysisPage.addDbPathsAsNewEntries(paths)
        }
    }
    FolderDialog {
        id: outDirDialog
        objectName: "outDirDialog"
        // Native where available — the QML fallback dialog can't create a
        // new folder, the platform's own picker can (GTK/KDE both have a
        // "Create Folder" action).
        options: newAnalysisPage.useNativeDialogs ? 0 : FolderDialog.DontUseNativeDialog
        onAccepted: outDirField.text = Router.toLocalPath(selectedFolder)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        RowLayout {
            Layout.fillWidth: true

            Label {
                text: "New Analysis — " + (project ? project.name : "")
                font.pixelSize: 16
                font.bold: true
                Layout.fillWidth: true
            }

            Button {
                objectName: "backToProjectButton"
                text: "Back to project"
                onClicked: if (project) Router.showProjectHomeRequested(project)

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        // Prefills every tab from a previous run's saved config
        // (applyConfig) so re-running an existing analysis with a couple
        // of settings tweaked doesn't mean refilling the whole form —
        // "Start blank" (the default selection) resets back to schema
        // defaults instead. Visible above the tab bar regardless of
        // which tab is open, since it can touch any of them.
        RowLayout {
            objectName: "templateRow"
            Layout.fillWidth: true
            visible: project && project.runsList && project.runsList.length > 0

            Label { text: "Load from previous analysis:" }
            ComboBox {
                id: templateCombo
                objectName: "templateCombo"
                Layout.fillWidth: true
                textRole: "label"
                valueRole: "id"
                model: {
                    var options = [{id: "", label: "Start blank"}]
                    var runs = (project && project.runsList) ? project.runsList : []
                    for (var i = 0; i < runs.length; i++) {
                        options.push({
                            id: runs[i].id,
                            label: runs[i].start_date_display + " — " + runs[i].status
                        })
                    }
                    return options
                }
                currentIndex: 0
                // onActivated (a real user pick), not onCurrentIndexChanged
                // — re-picking the same entry after manual edits should
                // still re-apply it, which a currentIndex-unchanged
                // onCurrentIndexChanged binding wouldn't fire for.
                onActivated: {
                    if (templateCombo.currentValue === "") {
                        newAnalysisPage.resetToBlank()
                        return
                    }
                    var run = project.runs[templateCombo.currentValue]
                    if (run && run.config)
                        newAnalysisPage.applyConfig(run.config)
                }

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        // A plain TabBar is a single row: with 13 tabs it has to either
        // shrink each button (eliding the label, the original complaint)
        // or scroll sideways (tabs hidden off-screen). Neither shows every
        // label at once. A Flow of checkable Buttons instead wraps whole
        // buttons onto as many rows as needed — each label stays on one
        // line at its natural width, never split mid-word; short rows like
        // "align all mzs" | "group MS2" sit together, a long one like
        // "TIC normalization" gets its own row's worth of space.
        Flow {
            id: tabBar
            objectName: "tabBar"
            Layout.fillWidth: true
            spacing: 4

            property int currentIndex: 0

            Button {
                objectName: "tabButton_io"
                text: "input/output"
                checkable: true
                checked: tabBar.currentIndex === 0
                highlighted: checked
                onClicked: tabBar.currentIndex = 0

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Button {
                // Bespoke tab (not from the generic ConfigSchema.groups
                // Repeater below) — target_list.adducts needs a
                // polarity-filtered multi-select, which the generic
                // one-control-per-field renderer can't produce. See
                // gui/utils/config_schema.py's module docstring.
                objectName: "tabButton_target_list"
                text: "target list matching"
                checkable: true
                checked: tabBar.currentIndex === 1
                highlighted: checked
                onClicked: tabBar.currentIndex = 1

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Repeater {
                model: ConfigSchema.groups
                delegate: Button {
                    objectName: "tabButton_" + modelData.key
                    text: modelData.title
                    checkable: true
                    checked: tabBar.currentIndex === index + 2
                    highlighted: checked
                    onClicked: tabBar.currentIndex = index + 2

                    HoverHandler {
                        cursorShape: Qt.PointingHandCursor
                    }
                }
            }
        }

        StackLayout {
            id: stackLayout
            objectName: "stackLayout"
            currentIndex: tabBar.currentIndex
            Layout.fillWidth: true
            Layout.fillHeight: true

            // --- IO tab (bespoke) ---
            Flickable {
                id: ioFlickable
                clip: true
                contentWidth: width
                contentHeight: ioColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                ColumnLayout {
                    id: ioColumn
                    width: ioFlickable.width
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Project folder:"; Layout.preferredWidth: 160 }
                        Label {
                            objectName: "projectFolderLabel"
                            text: project ? project.folder : ""
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Output folder:"; Layout.preferredWidth: 160 }
                        TextField {
                            id: outDirField
                            objectName: "outDirField"
                            Layout.fillWidth: true
                            text: project ? project.folder + "/output" : ""
                        }
                        Button {
                            text: "Browse..."
                            onClicked: outDirDialog.open()

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Samples"; font.bold: true; Layout.fillWidth: true }
                        Button {
                            objectName: "addMzmlFilesButton"
                            text: "Add mzML files..."
                            onClicked: bulkMzmlDialog.open()

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        Button {
                            objectName: "addXmlFilesButton"
                            text: "Add XML files..."
                            onClicked: bulkXmlDialog.open()

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                    }
                    Label {
                        text: "mzML and XML are picked separately and lined up by row "
                              + "order — use the ▲/▼ arrows to fix a mismatched pairing."
                        color: Theme.mutedTextColor
                        font.pixelSize: Theme.captionPixelSize
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }

                    Repeater {
                        model: sampleRows
                        delegate: RowLayout {
                            id: sampleRow
                            objectName: "sampleRow_" + index
                            Layout.fillWidth: true

                            Label {
                                objectName: "sampleRowMzml_" + index
                                text: modelData.mzml === "" ? "(no mzML selected)"
                                      : newAnalysisPage.displayPath(modelData.mzml, newAnalysisPage.mzmlCommonDir)
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                                HoverHandler { id: mzmlHover_ }
                                // Full path on hover — the label itself
                                // drops it once every mzML shares a folder.
                                ToolTip.visible: mzmlHover_.hovered && modelData.mzml !== ""
                                ToolTip.text: modelData.mzml
                            }
                            ColumnLayout {
                                spacing: 0
                                Button {
                                    objectName: "moveMzmlUpButton_" + index
                                    text: "▲"
                                    Layout.preferredWidth: 24
                                    Layout.preferredHeight: 16
                                    enabled: index > 0
                                    onClicked: newAnalysisPage.swapField(index, index - 1, "mzml")

                                    HoverHandler {
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }
                                Button {
                                    objectName: "moveMzmlDownButton_" + index
                                    text: "▼"
                                    Layout.preferredWidth: 24
                                    Layout.preferredHeight: 16
                                    enabled: index < sampleRows.length - 1
                                    onClicked: newAnalysisPage.swapField(index, index + 1, "mzml")

                                    HoverHandler {
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }
                            }
                            Button {
                                text: "mzML..."
                                onClicked: {
                                    mzmlDialogTarget.rowIndex = index
                                    mzmlDialog.open()
                                }

                                HoverHandler {
                                    cursorShape: Qt.PointingHandCursor
                                }
                            }
                            Label {
                                objectName: "sampleRowXml_" + index
                                text: modelData.xml === "" ? "(no XML selected)"
                                      : newAnalysisPage.displayPath(modelData.xml, newAnalysisPage.xmlCommonDir)
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                                HoverHandler { id: xmlHover_ }
                                ToolTip.visible: xmlHover_.hovered && modelData.xml !== ""
                                ToolTip.text: modelData.xml
                            }
                            ColumnLayout {
                                spacing: 0
                                Button {
                                    objectName: "moveXmlUpButton_" + index
                                    text: "▲"
                                    Layout.preferredWidth: 24
                                    Layout.preferredHeight: 16
                                    enabled: index > 0
                                    onClicked: newAnalysisPage.swapField(index, index - 1, "xml")

                                    HoverHandler {
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }
                                Button {
                                    objectName: "moveXmlDownButton_" + index
                                    text: "▼"
                                    Layout.preferredWidth: 24
                                    Layout.preferredHeight: 16
                                    enabled: index < sampleRows.length - 1
                                    onClicked: newAnalysisPage.swapField(index, index + 1, "xml")

                                    HoverHandler {
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }
                            }
                            Button {
                                text: "XML..."
                                onClicked: {
                                    xmlDialogTarget.rowIndex = index
                                    xmlDialog.open()
                                }

                                HoverHandler {
                                    cursorShape: Qt.PointingHandCursor
                                }
                            }
                            Button {
                                objectName: "removeSampleRowButton_" + index
                                text: "Remove"
                                onClicked: newAnalysisPage.removeSampleRow(index)

                                HoverHandler {
                                    cursorShape: Qt.PointingHandCursor
                                }
                            }
                        }
                    }

                    Button {
                        id: addSampleButton
                        objectName: "addSampleButton"
                        text: "Add Sample"
                        onClicked: newAnalysisPage.addSampleRow()

                        HoverHandler {
                            cursorShape: Qt.PointingHandCursor
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 12
                        Label {
                            text: "Already-parsed samples"
                            font.bold: true
                            Layout.fillWidth: true
                        }
                        Button {
                            objectName: "addDbFilesButton"
                            text: "Add db files..."
                            onClicked: bulkDbDialog.open()

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                    }
                    Label {
                        text: "Pick .db files that have already been parsed and pixel-mapped "
                              + "(e.g. from a previous run) to skip mzML/XML parsing for those "
                              + "samples — they can be mixed freely with the samples above."
                        color: Theme.mutedTextColor
                        font.pixelSize: Theme.captionPixelSize
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }

                    Repeater {
                        model: dbOnlyPaths
                        delegate: RowLayout {
                            id: dbOnlyRow
                            objectName: "dbOnlyRow_" + index
                            Layout.fillWidth: true

                            Label {
                                objectName: "dbOnlyRowPath_" + index
                                text: newAnalysisPage.displayPath(modelData, newAnalysisPage.dbOnlyCommonDir)
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                                HoverHandler { id: dbOnlyHover_ }
                                ToolTip.visible: dbOnlyHover_.hovered
                                ToolTip.text: modelData
                            }
                            Button {
                                objectName: "removeDbOnlyPathButton_" + index
                                text: "Remove"
                                onClicked: newAnalysisPage.removeDbOnlyPath(index)

                                HoverHandler {
                                    cursorShape: Qt.PointingHandCursor
                                }
                            }
                        }
                    }
                }
            }

            // --- Target list matching tab (bespoke): adducts needs a
            // polarity-filtered multi-select, which the generic
            // one-control-per-field renderer (below) can't produce. See
            // gui/utils/config_schema.py's module docstring.
            Flickable {
                id: targetListFlickable
                objectName: "groupTab_target_list"
                clip: true
                contentWidth: width
                contentHeight: targetListColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                ColumnLayout {
                    id: targetListColumn
                    width: targetListFlickable.width
                    spacing: 8

                    readonly property var adductOptions:
                        targetListPolarityCombo.currentText === "negative"
                        ? ConfigSchema.negativeAdducts : ConfigSchema.positiveAdducts

                    Label {
                        objectName: "groupDescription_target_list"
                        text: ConfigSchema.targetListSchema.description
                        color: Theme.mutedTextColor
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                        Layout.bottomMargin: 8
                    }

                    RowLayout {
                        objectName: "fieldRow_target_list_paths"
                        Layout.fillWidth: true

                        Label {
                            text: ConfigSchema.targetListSchema.fields.paths.label
                            Layout.preferredWidth: 260
                        }
                        ToolButton {
                            objectName: "field_target_list_paths_help"
                            text: "?"
                            implicitWidth: 22
                            implicitHeight: 22
                            property string helpText: ConfigSchema.targetListSchema.fields.paths.help
                            onClicked: newAnalysisPage.openFieldHelp(
                                ConfigSchema.targetListSchema.fields.paths.label, helpText)

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        TextField {
                            id: targetListPathsField
                            objectName: "field_target_list_paths"
                            readOnly: true
                            Layout.fillWidth: true
                        }
                        Button {
                            text: "Browse..."
                            onClicked: targetListPathsDialog.open()

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                    }

                    RowLayout {
                        objectName: "fieldRow_target_list_polarity"
                        Layout.fillWidth: true

                        Label {
                            text: ConfigSchema.targetListSchema.fields.polarity.label
                            Layout.preferredWidth: 260
                        }
                        ToolButton {
                            objectName: "field_target_list_polarity_help"
                            text: "?"
                            implicitWidth: 22
                            implicitHeight: 22
                            property string helpText: ConfigSchema.targetListSchema.fields.polarity.help
                            onClicked: newAnalysisPage.openFieldHelp(
                                ConfigSchema.targetListSchema.fields.polarity.label, helpText)

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        ComboBox {
                            id: targetListPolarityCombo
                            objectName: "field_target_list_polarity"
                            Layout.fillWidth: true
                            model: ["positive", "negative"]
                            currentIndex: 0
                            // The two adduct label sets are disjoint, so a
                            // selection made under the old polarity is
                            // never valid under the new one.
                            onCurrentTextChanged: newAnalysisPage.targetListSelectedAdducts = []
                        }
                    }

                    ColumnLayout {
                        objectName: "fieldRow_target_list_adducts"
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: "Adducts"
                                Layout.preferredWidth: 260
                            }
                            Label {
                                text: "(none selected = every standard adduct for the selected polarity)"
                                color: Theme.mutedTextColor
                                font.pixelSize: Theme.captionPixelSize
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            Layout.leftMargin: 260
                            spacing: 4

                            Repeater {
                                model: targetListColumn.adductOptions
                                delegate: CheckBox {
                                    objectName: "field_target_list_adduct_" + modelData
                                    text: modelData
                                    checked: newAnalysisPage.targetListSelectedAdducts.indexOf(modelData) !== -1
                                    onToggled: newAnalysisPage.toggleTargetListAdduct(modelData, checked)

                                    HoverHandler {
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        objectName: "fieldRow_target_list_match_ppm"
                        Layout.fillWidth: true

                        Label {
                            text: ConfigSchema.targetListSchema.fields.match_ppm.label
                            Layout.preferredWidth: 260
                        }
                        ToolButton {
                            objectName: "field_target_list_match_ppm_help"
                            text: "?"
                            implicitWidth: 22
                            implicitHeight: 22
                            property string helpText: ConfigSchema.targetListSchema.fields.match_ppm.help
                            onClicked: newAnalysisPage.openFieldHelp(
                                ConfigSchema.targetListSchema.fields.match_ppm.label, helpText)

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        TextField {
                            id: targetListMatchPpmField
                            objectName: "field_target_list_match_ppm"
                            Layout.fillWidth: true
                            text: String(ConfigSchema.targetListSchema.fields.match_ppm.default)
                        }
                    }
                }
            }

            // --- Generic, schema-driven tabs (every group but io and
            // target_list) ---
            Repeater {
                model: ConfigSchema.groups
                delegate: Flickable {
                    id: groupTab
                    objectName: "groupTab_" + modelData.key
                    clip: true
                    property string groupKey: modelData.key
                    property var groupFields: modelData.fields
                    contentWidth: width
                    contentHeight: groupColumn.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds

                    ColumnLayout {
                        id: groupColumn
                        width: groupTab.width
                        spacing: 8

                        Label {
                            objectName: "groupDescription_" + groupTab.groupKey
                            text: modelData.description
                            color: Theme.mutedTextColor
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                            Layout.bottomMargin: 8
                        }

                        Repeater {
                            model: groupTab.groupFields
                            delegate: RowLayout {
                                id: fieldRow
                                objectName: "fieldRow_" + groupTab.groupKey + "_" + modelData.name
                                Layout.fillWidth: true
                                property string fieldObjectName:
                                    "field_" + groupTab.groupKey + "_" + modelData.name
                                // Fields with no `enabledWhenField` are
                                // always enabled. Others read the
                                // controlling field's own `checked` —
                                // QML's automatic dependency tracking
                                // follows that read through
                                // `isFieldEnabled`'s `findByObjectName`
                                // call, so this stays reactive to the
                                // controlling checkbox without any extra
                                // wiring, as long as that field is
                                // declared earlier in the same group
                                // (true for every current use: the schema
                                // only sets `enabledWhenField` pointing at
                                // an earlier sibling — see config_schema.py).
                                enabled: newAnalysisPage.isFieldEnabled(groupTab.groupKey, modelData)

                                Label {
                                    text: modelData.label
                                    Layout.preferredWidth: 260
                                }

                                ToolButton {
                                    objectName: fieldRow.fieldObjectName + "_help"
                                    text: "?"
                                    implicitWidth: 22
                                    implicitHeight: 22
                                    // Kept as a plain property (not just
                                    // read off modelData at click time) so
                                    // tests can still read it directly, as
                                    // before.
                                    property string helpText: modelData.help
                                    onClicked: newAnalysisPage.openFieldHelp(modelData.label, helpText)

                                    HoverHandler {
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }

                                CheckBox {
                                    objectName: modelData.kind === "bool" ? fieldRow.fieldObjectName : ""
                                    visible: modelData.kind === "bool"
                                    checked: modelData.default === true
                                    Layout.fillWidth: true

                                    HoverHandler {
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                }

                                TextField {
                                    objectName: (modelData.kind !== "bool" && modelData.kind !== "path_list")
                                                ? fieldRow.fieldObjectName : ""
                                    visible: modelData.kind !== "bool" && modelData.kind !== "path_list"
                                    text: (modelData.default === null || modelData.default === undefined)
                                          ? "" : String(modelData.default)
                                    Layout.fillWidth: true
                                }

                                // library_path: a picked-files list with a
                                // remove button per row (like the io tab's
                                // sample-row table), not a single
                                // semicolon-joined text field — the user
                                // wanted to see and prune what's selected,
                                // not read/edit a joined string.
                                ColumnLayout {
                                    objectName: modelData.kind === "path_list" ? fieldRow.fieldObjectName : ""
                                    visible: modelData.kind === "path_list"
                                    Layout.fillWidth: true
                                    spacing: 4

                                    RowLayout {
                                        Layout.fillWidth: true

                                        Label {
                                            objectName: "libraryPathEmptyLabel"
                                            visible: newAnalysisPage.libraryPaths.length === 0
                                            text: "No libraries selected"
                                            color: Theme.mutedTextColor
                                            Layout.fillWidth: true
                                        }
                                        Item { Layout.fillWidth: newAnalysisPage.libraryPaths.length > 0 }

                                        Button {
                                            objectName: "libraryPathBrowseButton"
                                            text: "Browse..."
                                            onClicked: libraryPathDialog.open()

                                            HoverHandler {
                                                cursorShape: Qt.PointingHandCursor
                                            }
                                        }
                                    }

                                    Repeater {
                                        model: newAnalysisPage.libraryPaths
                                        delegate: RowLayout {
                                            objectName: "libraryPathRow_" + index
                                            Layout.fillWidth: true

                                            Label {
                                                text: modelData
                                                elide: Text.ElideMiddle
                                                Layout.fillWidth: true
                                            }
                                            Button {
                                                objectName: "removeLibraryPathButton_" + index
                                                text: "Remove"
                                                onClicked: newAnalysisPage.removeLibraryPath(index)

                                                HoverHandler {
                                                    cursorShape: Qt.PointingHandCursor
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        Button {
            id: runButton
            objectName: "runButton"
            text: "Run"
            enabled: (sampleRows.length > 0 || dbOnlyPaths.length > 0)
                     && outDirField.text.trim() !== "" && allSampleRowsFilled()
            onClicked: Router.runAnalysisRequested(project, collectConfig())

            HoverHandler {
                cursorShape: Qt.PointingHandCursor
            }
        }
    }

    FileDialog {
        id: libraryPathDialog
        objectName: "libraryPathDialog"
        options: newAnalysisPage.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Library database (*.db)", "All files (*)"]
        onAccepted: {
            var paths = []
            for (var i = 0; i < selectedFiles.length; i++)
                paths.push(Router.toLocalPath(selectedFiles[i]))
            newAnalysisPage.addLibraryPaths(paths)
        }
    }

    FileDialog {
        // Its own dialog, deliberately not reusing libraryPathDialog above
        // — a shared dialog hardcoded to write into one specific field is
        // exactly the kind of thing that silently corrupts a second
        // path-list field (see ADR 26 / target-list-annotation-feature
        // memory). Writes directly to targetListPathsField by id rather
        // than a hardcoded findByObjectName lookup, for the same reason.
        id: targetListPathsDialog
        objectName: "targetListPathsDialog"
        options: newAnalysisPage.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Target list (*.csv *.txt)", "All files (*)"]
        onAccepted: {
            var paths = []
            for (var i = 0; i < selectedFiles.length; i++)
                paths.push(Router.toLocalPath(selectedFiles[i]))
            targetListPathsField.text = paths.join("; ")
        }
    }

    // A field's help text is Markdown (General description / Formula /
    // Range / Interaction paragraphs — see the Config dataclasses' own
    // docstring convention) with real paragraph breaks and **bold**/*em*
    // markup, which a hover ToolTip can't render usefully. One shared,
    // read-only dialog for every "?" button on this page — safe to share
    // (unlike libraryPathDialog/targetListPathsDialog above, which write
    // into a specific field) since opening it only ever displays whatever
    // was passed in, never writes anywhere.
    function openFieldHelp(title, body) {
        fieldHelpDialog.helpTitle = title
        fieldHelpDialog.helpBody = body
        fieldHelpDialog.open()
    }

    Dialog {
        id: fieldHelpDialog
        objectName: "fieldHelpDialog"
        modal: true
        standardButtons: Dialog.Close
        anchors.centerIn: parent
        width: Math.min(520, newAnalysisPage.width - 80)
        height: Math.min(420, newAnalysisPage.height - 80)

        property string helpTitle: ""
        property string helpBody: ""

        title: helpTitle

        ScrollView {
            anchors.fill: parent
            clip: true

            Text {
                objectName: "fieldHelpDialogText"
                width: fieldHelpDialog.availableWidth
                textFormat: Text.MarkdownText
                wrapMode: Text.Wrap
                text: fieldHelpDialog.helpBody
            }
        }
    }
}

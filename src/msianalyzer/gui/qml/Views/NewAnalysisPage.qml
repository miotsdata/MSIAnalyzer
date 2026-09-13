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

        for (var g = 0; g < ConfigSchema.groups.length; g++) {
            var group = ConfigSchema.groups[g]
            var groupDict = {}
            for (var f = 0; f < group.fields.length; f++) {
                var field = group.fields[f]
                var control = findByObjectName(
                    newAnalysisPage, "field_" + group.key + "_" + field.name)
                groupDict[field.name] = control
                    ? parseFieldValue(field.kind, control) : field.default
            }
            configDict[group.key] = groupDict
        }

        return configDict
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
            }
            Repeater {
                model: ConfigSchema.groups
                delegate: Button {
                    objectName: "tabButton_" + modelData.key
                    text: modelData.title
                    checkable: true
                    checked: tabBar.currentIndex === index + 1
                    highlighted: checked
                    onClicked: tabBar.currentIndex = index + 1
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
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Samples"; font.bold: true; Layout.fillWidth: true }
                        Button {
                            objectName: "addMzmlFilesButton"
                            text: "Add mzML files..."
                            onClicked: bulkMzmlDialog.open()
                        }
                        Button {
                            objectName: "addXmlFilesButton"
                            text: "Add XML files..."
                            onClicked: bulkXmlDialog.open()
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
                                }
                                Button {
                                    objectName: "moveMzmlDownButton_" + index
                                    text: "▼"
                                    Layout.preferredWidth: 24
                                    Layout.preferredHeight: 16
                                    enabled: index < sampleRows.length - 1
                                    onClicked: newAnalysisPage.swapField(index, index + 1, "mzml")
                                }
                            }
                            Button {
                                text: "mzML..."
                                onClicked: {
                                    mzmlDialogTarget.rowIndex = index
                                    mzmlDialog.open()
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
                                }
                                Button {
                                    objectName: "moveXmlDownButton_" + index
                                    text: "▼"
                                    Layout.preferredWidth: 24
                                    Layout.preferredHeight: 16
                                    enabled: index < sampleRows.length - 1
                                    onClicked: newAnalysisPage.swapField(index, index + 1, "xml")
                                }
                            }
                            Button {
                                text: "XML..."
                                onClicked: {
                                    xmlDialogTarget.rowIndex = index
                                    xmlDialog.open()
                                }
                            }
                            Button {
                                objectName: "removeSampleRowButton_" + index
                                text: "Remove"
                                onClicked: newAnalysisPage.removeSampleRow(index)
                            }
                        }
                    }

                    Button {
                        id: addSampleButton
                        objectName: "addSampleButton"
                        text: "Add Sample"
                        onClicked: newAnalysisPage.addSampleRow()
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
                            }
                        }
                    }
                }
            }

            // --- Generic, schema-driven tabs (every group but io) ---
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
                                    // Attached properties (ToolTip.*) aren't
                                    // readable via QObject.property() from
                                    // Python, so the text is also a plain
                                    // property here for tests to read.
                                    property string helpText: modelData.help
                                    ToolTip.visible: hovered
                                    ToolTip.text: helpText
                                }

                                CheckBox {
                                    objectName: modelData.kind === "bool" ? fieldRow.fieldObjectName : ""
                                    visible: modelData.kind === "bool"
                                    checked: modelData.default === true
                                    Layout.fillWidth: true
                                }

                                TextField {
                                    objectName: (modelData.kind !== "bool" && modelData.kind !== "path_list")
                                                ? fieldRow.fieldObjectName : ""
                                    visible: modelData.kind !== "bool" && modelData.kind !== "path_list"
                                    text: (modelData.default === null || modelData.default === undefined)
                                          ? "" : String(modelData.default)
                                    Layout.fillWidth: true
                                }

                                RowLayout {
                                    visible: modelData.kind === "path_list"
                                    Layout.fillWidth: true

                                    TextField {
                                        id: libraryPathField
                                        objectName: modelData.kind === "path_list" ? fieldRow.fieldObjectName : ""
                                        readOnly: true
                                        Layout.fillWidth: true
                                    }
                                    Button {
                                        text: "Browse..."
                                        onClicked: libraryPathDialog.open()
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
            var field = findByObjectName(newAnalysisPage, "field_annotate_library_path")
            if (field)
                field.text = paths.join("; ")
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Page {
    id: newAnalysisPage
    objectName: "newAnalysisPage"

    property var project
    property var sampleRows: []

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

    function allSampleRowsFilled() {
        for (var i = 0; i < sampleRows.length; i++) {
            if (!sampleRows[i].mzml || !sampleRows[i].xml)
                return false
        }
        return true
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
        for (var i = 0; i < sampleRows.length; i++) {
            mzmlPaths.push(sampleRows[i].mzml)
            xmlPaths.push(sampleRows[i].xml)
        }

        var configDict = {}
        configDict["io"] = {
            "project_folder": project ? project.folder : "",
            "mzml_paths": mzmlPaths,
            "xml_paths": xmlPaths,
            "db_paths": [],
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
        options: FileDialog.DontUseNativeDialog
        nameFilters: ["mzML files (*.mzML *.mzml)", "All files (*)"]
        onAccepted: newAnalysisPage.setSampleField(
            mzmlDialogTarget.rowIndex, "mzml", Router.toLocalPath(selectedFile))
    }
    FileDialog {
        id: xmlDialog
        objectName: "xmlDialog"
        options: FileDialog.DontUseNativeDialog
        nameFilters: ["Raster XML (*.xml)", "All files (*)"]
        onAccepted: newAnalysisPage.setSampleField(
            xmlDialogTarget.rowIndex, "xml", Router.toLocalPath(selectedFile))
    }
    FolderDialog {
        id: outDirDialog
        objectName: "outDirDialog"
        options: FolderDialog.DontUseNativeDialog
        onAccepted: outDirField.text = Router.toLocalPath(selectedFolder)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        Text {
            text: "New Analysis — " + (project ? project.name : "")
            font.pixelSize: 18
            font.bold: true
        }

        TabBar {
            id: tabBar
            objectName: "tabBar"
            Layout.fillWidth: true

            TabButton {
                objectName: "tabButton_io"
                text: "input/output"
            }
            Repeater {
                model: ConfigSchema.groups
                delegate: TabButton {
                    objectName: "tabButton_" + modelData.key
                    text: modelData.title
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
                        Text { text: "Project folder:"; Layout.preferredWidth: 160 }
                        Text {
                            objectName: "projectFolderLabel"
                            text: project ? project.folder : ""
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Output folder:"; Layout.preferredWidth: 160 }
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

                    Text { text: "Samples"; font.bold: true }

                    Repeater {
                        model: sampleRows
                        delegate: RowLayout {
                            id: sampleRow
                            objectName: "sampleRow_" + index
                            Layout.fillWidth: true

                            Text {
                                objectName: "sampleRowMzml_" + index
                                text: modelData.mzml === "" ? "(no mzML selected)" : modelData.mzml
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                            }
                            Button {
                                text: "mzML..."
                                onClicked: {
                                    mzmlDialogTarget.rowIndex = index
                                    mzmlDialog.open()
                                }
                            }
                            Text {
                                objectName: "sampleRowXml_" + index
                                text: modelData.xml === "" ? "(no XML selected)" : modelData.xml
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
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

                        Repeater {
                            model: groupTab.groupFields
                            delegate: RowLayout {
                                id: fieldRow
                                Layout.fillWidth: true
                                property string fieldObjectName:
                                    "field_" + groupTab.groupKey + "_" + modelData.name

                                Text {
                                    text: modelData.name
                                    Layout.preferredWidth: 260
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
            enabled: sampleRows.length > 0 && outDirField.text.trim() !== ""
                     && allSampleRowsFilled()
            onClicked: Router.runAnalysisRequested(project, collectConfig())
        }
    }

    FileDialog {
        id: libraryPathDialog
        objectName: "libraryPathDialog"
        options: FileDialog.DontUseNativeDialog
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

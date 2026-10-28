import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "qrc:/Style"
import "qrc:/Utils/ConfigFields.js" as ConfigFields

// One config group's fields as a form, built from a `ConfigSchema.groups`
// entry: the generic part of New Analysis' group tabs, for a dialog that
// edits some groups of an existing analysis (ReannotateDialog, ADR 63).
// Controls are named "field_<group>_<field>", as in New Analysis.
ColumnLayout {
    id: form
    spacing: 8

    property var group: null            // {key, title, description, fields}
    property var values: ({})           // {field: value}; reassigning reloads every control
    property var libraryPaths: []       // a `path_list` field's entries
    signal helpRequested(string title, string body)

    readonly property bool useNativeDialogs: Qt.platform.pluginName !== "offscreen"

    // name -> control, filled as the delegates are created. `registered`
    // is what makes bindings that look controls up re-evaluate.
    property var controls: ({})
    property int registered: 0

    function valueOf(field) {
        return (form.values && field.name in form.values) ? form.values[field.name] : field.default
    }

    function loadField(field) {
        if (field.kind === "path_list") {
            form.libraryPaths = ConfigFields.normalizePathListValue(form.valueOf(field))
            return
        }
        var control = form.controls[field.name]
        if (control) ConfigFields.applyFieldValue(field.kind, control, form.valueOf(field))
    }

    onValuesChanged: {
        if (!form.group) return
        for (var i = 0; i < form.group.fields.length; i++) form.loadField(form.group.fields[i])
    }

    function collect() {
        var out = {}
        for (var i = 0; i < form.group.fields.length; i++) {
            var field = form.group.fields[i]
            if (field.kind === "path_list") {
                out[field.name] = form.libraryPaths.length === 0 ? null
                    : (form.libraryPaths.length === 1 ? form.libraryPaths[0] : form.libraryPaths.slice())
                continue
            }
            var control = form.controls[field.name]
            out[field.name] = control ? ConfigFields.parseFieldValue(field.kind, control) : form.valueOf(field)
        }
        return out
    }

    // Same rule as New Analysis: a field naming an `enabledWhenField`
    // follows that (earlier, bool) field's checkbox.
    function isEnabled(field) {
        form.registered  // dependency: re-evaluate once the controller exists
        if (!field.enabledWhenField) return true
        var control = form.controls[field.enabledWhenField]
        return !control || control.checked === field.enabledWhenEquals
    }

    function addLibraryPaths(paths) {
        var merged = form.libraryPaths.slice()
        for (var i = 0; i < paths.length; i++)
            if (merged.indexOf(paths[i]) === -1) merged.push(paths[i])
        form.libraryPaths = merged
    }

    Label {
        objectName: "groupDescription_" + (form.group ? form.group.key : "")
        text: form.group ? form.group.description : ""
        color: Theme.mutedTextColor
        wrapMode: Text.Wrap
        Layout.fillWidth: true
        Layout.bottomMargin: 8
    }

    Repeater {
        model: form.group ? form.group.fields : []
        delegate: ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            // A field naming a `section` starts a titled part of the tab.
            Label {
                visible: !!modelData.section
                text: modelData.section || ""
                font.bold: true
                Layout.topMargin: 8
            }
            RowLayout {
                id: row
                Layout.fillWidth: true
                enabled: form.isEnabled(modelData)
                readonly property string controlName: "field_" + form.group.key + "_" + modelData.name

                Label {
                    text: modelData.label
                    Layout.preferredWidth: 240
                }
                ToolButton {
                    objectName: row.controlName + "_help"
                    text: "?"
                    implicitWidth: 22
                    implicitHeight: 22
                    onClicked: form.helpRequested(modelData.label, modelData.help)
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
                AppCheckBox {
                    id: check
                    objectName: modelData.kind === "bool" ? row.controlName : ""
                    visible: modelData.kind === "bool"
                    Layout.fillWidth: true
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
                TextField {
                    id: textField
                    objectName: modelData.kind !== "bool" && modelData.kind !== "path_list" ? row.controlName : ""
                    visible: modelData.kind !== "bool" && modelData.kind !== "path_list"
                    placeholderText: modelData.kind === "float_list" ? "comma-separated, e.g. 154.0863, 200.1234"
                                     : (modelData.kind === "optional_str_list" ? "comma-separated" : "")
                    Layout.fillWidth: true
                }
                ColumnLayout {
                    objectName: modelData.kind === "path_list" ? row.controlName : ""
                    visible: modelData.kind === "path_list"
                    Layout.fillWidth: true
                    spacing: 4

                    RowLayout {
                        Label {
                            visible: form.libraryPaths.length === 0
                            text: "No libraries selected"
                            color: Theme.mutedTextColor
                            Layout.fillWidth: true
                        }
                        Item { Layout.fillWidth: form.libraryPaths.length > 0 }
                        Button {
                            objectName: row.controlName + "_browse"
                            text: "Browse..."
                            onClicked: libraryDialog.open()
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                    Repeater {
                        model: form.libraryPaths
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: modelData
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                            Button {
                                objectName: "removeLibraryPathButton_" + index
                                text: "Remove"
                                onClicked: {
                                    var paths = form.libraryPaths.slice()
                                    paths.splice(index, 1)
                                    form.libraryPaths = paths
                                }
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                            }
                        }
                    }
                }

                Component.onCompleted: {
                    if (modelData.kind !== "path_list")
                        form.controls[modelData.name] = modelData.kind === "bool" ? check : textField
                    form.loadField(modelData)
                    form.registered += 1
                }
            }
        }
    }

    FileDialog {
        id: libraryDialog
        options: form.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Library database (*.db)", "All files (*)"]
        onAccepted: {
            var paths = []
            for (var i = 0; i < selectedFiles.length; i++)
                paths.push(Router.toLocalPath(selectedFiles[i]))
            form.addLibraryPaths(paths)
        }
    }
}

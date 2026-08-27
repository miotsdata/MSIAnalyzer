// src/msianalyzer/gui/qml/Main/Main.qml
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: window
    visible: true
    width: 800
    height: 600
    minimumWidth: 500
    minimumHeight: 400
    title: "MSIAnalyzer"

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 16

        Button {
            id: loadProjectButton
            objectName: "loadProjectButton"
            text: "Load Project"
            Layout.preferredWidth: 220
            Layout.alignment: Qt.AlignHCenter
            onClicked: projectFolderDialog.open()
        }

        Button {
            id: createProjectButton
            objectName: "createProjectButton"
            text: "Create Project"
            Layout.preferredWidth: 220
            Layout.alignment: Qt.AlignHCenter
            //onClicked: appBridge.createProject()
        }
    }

    FolderDialog {
        id: projectFolderDialog
        objectName: "projectFolderDialog"
        options: FolderDialog.DontUseNativeDialog
        //onAccepted: appBridge.setProjectFolder(selectedFolder.toString())
    }
}

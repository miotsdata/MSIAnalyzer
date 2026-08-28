import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
//import Core

Page {
    id: startPage

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

            onClicked: Router.createProjectRequested()
        }
    }

    FolderDialog {
        id: projectFolderDialog
        objectName: "projectFolderDialog"

        options: FolderDialog.DontUseNativeDialog
        onAccepted: {
            Router.projectFolderChosen(selectedFolder.toString())
        }
    }
}

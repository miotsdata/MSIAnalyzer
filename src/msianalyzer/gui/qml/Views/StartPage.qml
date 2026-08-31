import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Page {
    id: startPage
    objectName: "startPage"

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

            onClicked: Router.createProjectPageRequested()
        }
    }

    FolderDialog {
        id: projectFolderDialog
        objectName: "projectFolderDialog"

        options: FolderDialog.DontUseNativeDialog
        onAccepted: {
            Router.projectFolderChosen(Router.toLocalPath(selectedFolder))
        }
    }
}

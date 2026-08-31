import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Page {
    id: createProjectPage
    objectName: "createProjectPage"

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 16

        Text {
            text: "Create project"
            color: "blue"
            font.family: "Arial"
            font.pixelSize: 20
        }

        TextField {
            id: createProjectNameInput
            objectName: "createProjectNameInput"
            placeholderText: "Project name"
        }

        RowLayout {
            spacing: 8

            TextField {
                id: createProjectPathInput
                objectName: "createProjectPathInput"
                placeholderText: "Project folder"
                readOnly: true
                Layout.fillWidth: true
            }

            Button {
                id: browseFolderButton
                objectName: "browseFolderButton"
                text: "Browse..."
                onClicked: createProjectFolderDialog.open()
            }
        }

        Button {
            id: createProjectButtonProjectPage
            objectName: "createProjectButton"
            text: "Create"
            enabled: createProjectNameInput.text.trim() !== ""
                     && createProjectPathInput.text.trim() !== ""

            onClicked: Router.createProjectRequested(createProjectNameInput.text.trim(), createProjectPathInput.text.trim())
        }
    }

    FolderDialog {
        id: createProjectFolderDialog
        objectName: "createProjectFolderDialog"
        options: FolderDialog.DontUseNativeDialog
        onAccepted: {
            createProjectPathInput.text = selectedFolder.toString()
        }
    }
}

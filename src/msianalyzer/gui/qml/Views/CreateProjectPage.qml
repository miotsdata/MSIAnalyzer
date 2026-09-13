import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"
import QtQuick.Dialogs

Page {
    id: createProjectPage
    objectName: "createProjectPage"

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 16

        Label {
            text: "Create project"
            font.pixelSize: Theme.headingPixelSize
            font.bold: true
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
        options: Qt.platform.pluginName === "offscreen" ? FolderDialog.DontUseNativeDialog : 0
        onAccepted: {
            createProjectPathInput.text = Router.toLocalPath(selectedFolder)        
        }
    }
}

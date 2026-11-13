import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "qrc:/Style"

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

            HoverHandler {
                cursorShape: Qt.PointingHandCursor
            }
        }

        Button {
            id: createProjectButton
            objectName: "createProjectButton"
            text: "Create Project"
            Layout.preferredWidth: 220
            Layout.alignment: Qt.AlignHCenter

            onClicked: Router.createProjectPageRequested()

            HoverHandler {
                cursorShape: Qt.PointingHandCursor
            }
        }

        Label {
            text: "Open recent"
            font.bold: true
            visible: recentRepeater.count > 0
            Layout.topMargin: 16
            Layout.alignment: Qt.AlignHCenter
        }
        Repeater {
            id: recentRepeater
            objectName: "recentProjectsRepeater"
            model: Router ? Router.recentProjects : []
            delegate: ItemDelegate {
                objectName: "recentProjectButton_" + index
                Layout.preferredWidth: 420
                Layout.alignment: Qt.AlignHCenter
                contentItem: ColumnLayout {
                    spacing: 0
                    Label { text: modelData.split(/[\\/]/).pop(); font.bold: true }
                    Label {
                        text: modelData
                        color: Theme.mutedTextColor
                        font.pixelSize: Theme.captionPixelSize
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                }
                onClicked: Router.projectFolderChosen(modelData)
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }
    }

    FolderDialog {
        id: projectFolderDialog
        objectName: "projectFolderDialog"

        // Native where available — see NewAnalysisPage.qml's
        // `useNativeDialogs` for why this is conditional on the QPA
        // platform rather than unconditionally native.
        options: Qt.platform.pluginName === "offscreen" ? FolderDialog.DontUseNativeDialog : 0
        onAccepted: {
            Router.projectFolderChosen(Router.toLocalPath(selectedFolder))
        }
    }
}

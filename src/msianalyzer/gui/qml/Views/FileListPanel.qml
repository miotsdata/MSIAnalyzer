import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A titled, bordered panel listing filenames (not full paths) — used for
// Project Home's mzML/xml file lists (ProjectHomePage.qml), which are
// otherwise identical apart from their title/model/empty-state text.
Rectangle {
    id: fileListPanel

    // Short, stable tag (e.g. "mzml"/"xml") for building objectNames below
    // — kept separate from `title` so those names don't embed arbitrary
    // display text.
    property string kind: ""
    property string title: ""
    property var files: []
    property string emptyText: "No files."

    color: "transparent"
    border.color: palette.mid
    border.width: 1
    radius: 4

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6

        Text {
            objectName: "fileListPanelTitle_" + fileListPanel.kind
            text: fileListPanel.title + " (" + fileListPanel.files.length + ")"
            font.bold: true
        }

        Text {
            objectName: "fileListPanelEmptyLabel_" + fileListPanel.kind
            visible: fileListPanel.files.length === 0
            text: fileListPanel.emptyText
            color: "gray"
        }

        Flickable {
            objectName: "fileListPanelFlickable_" + fileListPanel.kind
            visible: fileListPanel.files.length > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: filesColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: filesColumn
                width: parent.width
                spacing: 2

                Repeater {
                    objectName: "fileListPanelRepeater_" + fileListPanel.kind
                    model: fileListPanel.files

                    delegate: Text {
                        Layout.fillWidth: true
                        text: modelData
                        elide: Text.ElideMiddle

                        HoverHandler { id: fileHover }
                        ToolTip.visible: fileHover.hovered
                        ToolTip.text: modelData
                    }
                }
            }
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

Page {
    id: projectHomePage
    objectName: "projectHomePage"

    property var project

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        Label {
            id: projectNameLabel
            objectName: "projectNameLabel"
            text: project ? project.name : ""
            font.pixelSize: Theme.headingPixelSize
            font.bold: true
        }

        SplitView {
            id: mainSplitView
            objectName: "mainSplitView"
            orientation: Qt.Horizontal
            Layout.fillWidth: true
            Layout.fillHeight: true

            // A visible gap between the two panels rather than the
            // default hairline-thin handle they otherwise butt up
            // against — a thin divider line centered in a wider
            // (invisible except on hover) drag area.
            handle: Rectangle {
                implicitWidth: 12
                color: "transparent"

                Rectangle {
                    anchors.centerIn: parent
                    width: 1
                    height: parent.height
                    color: SplitHandle.hovered ? palette.dark : palette.mid
                }
            }

            // Left: the analyses list — the primary thing this page is
            // for, given a sensible default width but user-resizable
            // (SplitView, not a fixed fraction of the row's own width —
            // the latter fed back into Qt Quick Layouts' own rearrange
            // pass and triggered "Detected recursive rearrange").
            ColumnLayout {
                id: analysesColumn
                objectName: "analysesColumn"
                SplitView.preferredWidth: 480
                SplitView.minimumWidth: 320
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true

                    Label {
                        text: "Analyses"
                        font.pixelSize: 14
                        font.bold: true
                        Layout.fillWidth: true
                    }

                    Button {
                        id: newAnalysisButton
                        objectName: "newAnalysisButton"
                        text: "New Analysis"
                        onClicked: Router.newAnalysisPageRequested(project)

                        HoverHandler {
                            cursorShape: Qt.PointingHandCursor
                        }
                    }
                }

                Label {
                    id: emptyStateLabel
                    objectName: "emptyStateLabel"
                    visible: !project || project.runsList.length === 0
                    text: "No analyses yet."
                    color: Theme.mutedTextColor
                }

                Flickable {
                    id: runsListView
                    objectName: "runsListView"
                    visible: project && project.runsList.length > 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: width
                    contentHeight: runsColumn.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds

                    ColumnLayout {
                        id: runsColumn
                        width: runsListView.width
                        spacing: 8

                        Repeater {
                            id: runsRepeater
                            objectName: "runsRepeater"
                            model: project ? project.runsList : []

                            delegate: Rectangle {
                                Layout.fillWidth: true
                                height: runRowColumn.implicitHeight + 16
                                objectName: "runRow_" + modelData.id
                                color: "transparent"
                                border.color: palette.mid
                                border.width: 1
                                radius: 4

                                MouseArea {
                                    objectName: "runRowMouseArea_" + modelData.id
                                    anchors.fill: parent
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    // "the analyses cards... should make
                                    // the user understand that they can
                                    // be clickable (on hover, use the
                                    // classic 'hand' logo)"
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: (mouse) => {
                                        if (mouse.button === Qt.RightButton) {
                                            runContextMenu.popup()
                                        } else {
                                            Router.analysisSelected(modelData.id)
                                        }
                                    }
                                }

                                // "the paths should be relative to project,
                                // not absolute (if user wants the full
                                // path, it can right click and use copy
                                // path)" — the row itself shows the
                                // project-relative display strings;
                                // this menu is the escape hatch to the
                                // real absolute paths.
                                Menu {
                                    id: runContextMenu
                                    objectName: "runContextMenu_" + modelData.id

                                    MenuItem {
                                        objectName: "copyOutDirMenuItem_" + modelData.id
                                        text: "Copy output path"
                                        enabled: modelData.out_dir !== ""
                                        onTriggered: Router.copyToClipboard(modelData.out_dir)
                                    }
                                    MenuItem {
                                        objectName: "copyConfigPathMenuItem_" + modelData.id
                                        text: "Copy config path"
                                        enabled: modelData.config_path !== ""
                                        onTriggered: Router.copyToClipboard(modelData.config_path)
                                    }
                                    MenuSeparator {}
                                    MenuItem {
                                        objectName: "deleteRunMenuItem_" + modelData.id
                                        text: "Delete analysis…"
                                        onTriggered: deleteRunDialog.open()
                                    }
                                }

                                // Confirms before deleting — this permanently
                                // removes the output folder and config file
                                // from disk, not just the list entry.
                                Dialog {
                                    id: deleteRunDialog
                                    objectName: "deleteRunDialog_" + modelData.id
                                    title: "Delete analysis?"
                                    modal: true
                                    standardButtons: Dialog.Yes | Dialog.No
                                    anchors.centerIn: Overlay.overlay

                                    Label {
                                        width: 320
                                        wrapMode: Text.WordWrap
                                        text: "This permanently deletes the output folder ("
                                            + modelData.out_dir_display
                                            + ") and its config file from disk, and removes "
                                            + "this analysis from the project. This cannot be undone."
                                    }

                                    onAccepted: {
                                        var error = project.deleteRun(modelData.id)
                                        if (error !== "") {
                                            Router.showErrorRequested(error)
                                        }
                                    }
                                }

                                ColumnLayout {
                                    id: runRowColumn
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 2

                                    Label {
                                        objectName: "runRowDate"
                                        text: modelData.start_date_display
                                        font.bold: true
                                    }
                                    Label {
                                        objectName: "runRowOutDir"
                                        visible: modelData.out_dir !== ""
                                        text: "Output: " + modelData.out_dir_display
                                    }
                                    Label {
                                        objectName: "runRowConfigPath"
                                        visible: modelData.config_path !== ""
                                        text: "Config: " + modelData.config_path_display
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Right: the project's data/ directory, split into mzML (top)
            // and XML (bottom) — filenames only, same "no absolute paths
            // cluttering the view" spirit as the analyses list.
            ColumnLayout {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 240
                spacing: 16

                FileListPanel {
                    id: mzmlFilesPanel
                    objectName: "mzmlFilesPanel"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    kind: "mzml"
                    title: "mzML files"
                    files: project ? project.mzmlFiles : []
                    emptyText: "No mzML files in data/."
                }

                FileListPanel {
                    id: xmlFilesPanel
                    objectName: "xmlFilesPanel"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    kind: "xml"
                    title: "XML files"
                    files: project ? project.xmlFiles : []
                    emptyText: "No XML files in data/."
                }
            }
        }
    }
}

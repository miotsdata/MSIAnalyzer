import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Page {
    id: projectHomePage
    objectName: "projectHomePage"

    property var project

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        Text {
            id: projectNameLabel
            objectName: "projectNameLabel"
            text: project ? project.name : ""
            color: "blue"
            font.family: "Arial"
            font.pixelSize: 20
        }

        RowLayout {
            Layout.fillWidth: true

            Text {
                text: "Analyses"
                font.pixelSize: 16
                Layout.fillWidth: true
            }

            Button {
                id: newAnalysisButton
                objectName: "newAnalysisButton"
                text: "New Analysis"
                onClicked: Router.newAnalysisPageRequested(project)
            }
        }

        Text {
            id: emptyStateLabel
            objectName: "emptyStateLabel"
            visible: !project || project.runsList.length === 0
            text: "No analyses yet."
            color: "gray"
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
                        border.color: "#cccccc"
                        border.width: 1
                        radius: 4

                        MouseArea {
                            anchors.fill: parent
                            onClicked: Router.analysisSelected(modelData.id)
                        }

                        ColumnLayout {
                            id: runRowColumn
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 2

                            Text {
                                objectName: "runRowDate"
                                text: modelData.start_date
                                font.bold: true
                            }
                            Text {
                                objectName: "runRowOutDir"
                                text: "Output: " + modelData.out_dir
                            }
                            Text {
                                objectName: "runRowConfigPath"
                                text: "Config: " + modelData.config_path
                            }
                        }
                    }
                }
            }
        }
    }
}

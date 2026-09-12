import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Page {
    id: runningAnalysisPage
    objectName: "runningAnalysisPage"

    property var project
    property string runId
    property var stepStatuses: ({})
    property string errorMessage: ""

    Connections {
        target: CoreBridge
        function onRunStepChanged(step, status) {
            var updated = Object.assign({}, stepStatuses)
            updated[step] = status
            stepStatuses = updated
        }
        function onRunFailed(message) {
            errorMessage = message
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        RowLayout {
            Layout.fillWidth: true

            Text {
                text: "Running Analysis — " + (project ? project.name : "")
                font.pixelSize: 16
                font.bold: true
                Layout.fillWidth: true
            }

            Button {
                id: backButton
                objectName: "backButton"
                text: "Back to project"
                // Navigating away doesn't cancel the run — it keeps going
                // on its worker thread regardless of which page is shown,
                // same as it already did when this button only appeared
                // on failure.
                onClicked: if (project) Router.showProjectHomeRequested(project)
            }
        }

        ColumnLayout {
            spacing: 6

            Repeater {
                id: stepsRepeater
                objectName: "stepsRepeater"
                model: CoreBridge.RUN_STEPS

                delegate: RowLayout {
                    Layout.fillWidth: true
                    objectName: "stepRow_" + modelData

                    Text {
                        text: modelData
                        Layout.preferredWidth: 220
                    }
                    Text {
                        objectName: "stepStatus_" + modelData
                        text: stepStatuses[modelData] || "pending"
                    }
                }
            }
        }

        Text {
            id: errorText
            objectName: "errorText"
            visible: errorMessage !== ""
            text: "Failed: " + errorMessage
            color: "red"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}

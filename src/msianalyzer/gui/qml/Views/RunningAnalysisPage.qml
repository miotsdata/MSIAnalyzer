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

        Text {
            text: "Running Analysis — " + (project ? project.name : "")
            font.pixelSize: 18
            font.bold: true
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

        Button {
            id: backButton
            objectName: "backButton"
            text: "Back to project"
            visible: errorMessage !== ""
            onClicked: Router.showProjectHomeRequested(project)
        }
    }
}

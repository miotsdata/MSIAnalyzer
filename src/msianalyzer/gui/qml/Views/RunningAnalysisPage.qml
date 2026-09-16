import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

Page {
    id: runningAnalysisPage
    objectName: "runningAnalysisPage"

    property var project
    property string runId
    property var stepStatuses: ({})
    property string errorMessage: ""
    // Only meaningful for the "process_samples" step — the other steps
    // have nothing finer than their own started/completed/skipped/failed
    // status to show (see the progress bar below: indeterminate for those,
    // a real "N of M" count only here).
    property int samplesDone: 0
    property int samplesTotal: 0

    Connections {
        target: CoreBridge
        function onRunStepChanged(step, status) {
            var updated = Object.assign({}, stepStatuses)
            updated[step] = status
            stepStatuses = updated
        }
        function onRunSampleProgress(done, total) {
            runningAnalysisPage.samplesDone = done
            runningAnalysisPage.samplesTotal = total
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

            Label {
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

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        ColumnLayout {
            spacing: 6

            Repeater {
                id: stepsRepeater
                objectName: "stepsRepeater"
                model: CoreBridge.RUN_STEPS

                delegate: ColumnLayout {
                    Layout.fillWidth: true
                    objectName: "stepRow_" + modelData
                    spacing: 2

                    RowLayout {
                        Layout.fillWidth: true

                        Label {
                            text: modelData
                            Layout.preferredWidth: 220
                        }
                        Label {
                            objectName: "stepStatus_" + modelData
                            text: {
                                var status = runningAnalysisPage.stepStatuses[modelData] || "pending"
                                // "the one on samples, with number of samples" —
                                // the only step with anything finer than its own
                                // started/completed/skipped/failed status to show.
                                if (modelData === "process_samples" && status === "started"
                                        && runningAnalysisPage.samplesTotal > 0) {
                                    return status + " (" + runningAnalysisPage.samplesDone
                                           + "/" + runningAnalysisPage.samplesTotal + ")"
                                }
                                return status
                            }
                        }
                    }

                    // "the other [steps], you see based on what it is
                    // running" — an indeterminate bar for every step while
                    // it's actually running (no finer progress than
                    // started/completed exists for those), a real
                    // determinate one only for process_samples once its
                    // first on_sample_progress callback has arrived.
                    ProgressBar {
                        objectName: "stepProgressBar_" + modelData
                        Layout.fillWidth: true
                        Layout.leftMargin: 220
                        visible: runningAnalysisPage.stepStatuses[modelData] === "started"
                        indeterminate: modelData !== "process_samples"
                                       || runningAnalysisPage.samplesTotal === 0
                        from: 0
                        to: modelData === "process_samples"
                            ? Math.max(1, runningAnalysisPage.samplesTotal) : 1
                        value: modelData === "process_samples"
                               ? runningAnalysisPage.samplesDone : 0
                    }
                }
            }
        }

        Label {
            id: errorText
            objectName: "errorText"
            visible: errorMessage !== ""
            text: "Failed: " + errorMessage
            color: Theme.errorColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}

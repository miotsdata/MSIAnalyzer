import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

ApplicationWindow {
    id: window

    visible: true
    // Bigger than the old 900x650 default — several sections (the 13-tab
    // config Flow, Visual Inspection's control panel + heatmap grid) need
    // real room; minimumWidth/Height stop the window being resized below
    // where that content stops fitting at all.
    width: 1280
    height: 850
    minimumWidth: 1000
    minimumHeight: 700
    title: "MSIAnalyzer"

    StackView {
        id: stackView
        objectName: "stackView"
        anchors.fill: parent

        initialItem: "qrc:/Views/StartPage.qml"
    }

    Connections {
        target: Router
        function onShowProjectHomeRequested(project) {
            stackView.push("qrc:/Views/ProjectHomePage.qml", {"project": project})
        }

        function onCreateProjectPageRequested() {
            stackView.push("qrc:/Views/CreateProjectPage.qml")
        }

        function onNewAnalysisPageRequested(project) {
            stackView.push("qrc:/Views/NewAnalysisPage.qml", {"project": project})
        }

        function onShowRunningPageRequested(project, runId) {
            stackView.push("qrc:/Views/RunningAnalysisPage.qml", {"project": project, "runId": runId})
        }

        function onShowAnalysisRequested(analysis) {
            stackView.push("qrc:/Views/AnalysisPage.qml", {"analysis": analysis})
        }

        function onShowErrorRequested(message) {
            errorDialog.text = message
            errorDialog.open()
        }
    }

    MessageDialog {
        id: errorDialog
        objectName: "errorDialog"
        buttons: MessageDialog.Ok
        modality: Qt.ApplicationModal
    }
}

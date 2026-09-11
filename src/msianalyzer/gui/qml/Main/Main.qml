import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Dialogs

ApplicationWindow {
    id: window

    visible: true
    width: 900
    height: 650
    title: "MSIAnalyzer"

    Material.theme: Material.Light
    Material.primary: Material.Blue
    Material.accent: Material.Blue

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

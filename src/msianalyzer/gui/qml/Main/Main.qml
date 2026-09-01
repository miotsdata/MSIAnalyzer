import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

ApplicationWindow {
    id: window

    visible: true
    width: 900
    height: 650
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

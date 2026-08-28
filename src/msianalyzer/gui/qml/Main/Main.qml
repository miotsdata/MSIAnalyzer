import QtQuick
import QtQuick.Controls

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
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Page {
    id: projectHomePage
    objectName: "projectHomePage"

    property var project

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 16
        Text {
            id: projectNameLabel
            objectName: "projectNameLabel"
            text: project ? project.name : ""
            color: "blue"
            font.family: "Arial"
            font.pixelSize: 20
        }
    }
}

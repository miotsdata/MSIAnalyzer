import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Page {
    id: analysisPage
    objectName: "analysisPage"

    property var analysis

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Text {
            objectName: "analysisPageTitle"
            text: "Analysis — " + (analysis ? analysis.projectName : "")
            font.pixelSize: 18
            font.bold: true
            Layout.margins: 16
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            ColumnLayout {
                id: navRail
                objectName: "navRail"
                Layout.preferredWidth: 180
                Layout.minimumWidth: 180
                Layout.maximumWidth: 180
                Layout.fillHeight: true
                Layout.margins: 8
                spacing: 4

                Repeater {
                    id: navRepeater
                    objectName: "navRepeater"
                    model: ["Summary", "MS1 Spectra", "Annotations", "Visual Inspection"]

                    delegate: Button {
                        objectName: "navButton_" + index
                        text: modelData
                        Layout.fillWidth: true
                        checkable: true
                        checked: sectionStack.currentIndex === index
                        onClicked: sectionStack.currentIndex = index
                    }
                }

                Item { Layout.fillHeight: true }
            }

            StackLayout {
                id: sectionStack
                objectName: "sectionStack"
                currentIndex: 0
                Layout.fillWidth: true
                Layout.fillHeight: true

                Loader {
                    id: summaryLoader
                    objectName: "summarySectionLoader"
                    source: "qrc:/Views/SummarySection.qml"
                    // Qt.binding, not a one-time assignment: a plain
                    // `item.analysis = analysisPage.analysis` only runs once,
                    // right when this Loader finishes — for a page reached
                    // via StackView.push({"analysis": ...}), analysisPage's
                    // own `analysis` property can still be unset at that
                    // exact moment, permanently leaving the section's copy
                    // null.
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }

                Item {
                    objectName: "comingSoon_ms1"
                    Text {
                        anchors.centerIn: parent
                        text: "MS1 Spectra — coming soon"
                        font.pixelSize: 16
                        color: "gray"
                    }
                }
                Loader {
                    id: annotationsLoader
                    objectName: "annotationsSectionLoader"
                    source: "qrc:/Views/AnnotationsSection.qml"
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }
                Item {
                    objectName: "comingSoon_visual"
                    Text {
                        anchors.centerIn: parent
                        text: "Visual Inspection — coming soon"
                        font.pixelSize: 16
                        color: "gray"
                    }
                }
            }
        }
    }
}

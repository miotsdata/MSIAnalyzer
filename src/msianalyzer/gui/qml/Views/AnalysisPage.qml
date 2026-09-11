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

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 16

            Text {
                objectName: "analysisPageTitle"
                text: "Analysis — " + (analysis ? analysis.projectName : "")
                font.pixelSize: 18
                font.bold: true
                Layout.fillWidth: true
            }

            Button {
                objectName: "backToProjectButton"
                text: "Back to project"
                onClicked: if (analysis) Router.showProjectHomeRequested(analysis.project)
            }
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

                Loader {
                    id: ms1Loader
                    objectName: "ms1SectionLoader"
                    // Loaded lazily (unlike the other sections' Loaders):
                    // this section owns a WebEngineView, and StackLayout
                    // keeps every page as a permanent sibling rather than
                    // destroying hidden ones — an eagerly-loaded WebEngineView
                    // would sit in the tree even while another tab is active,
                    // and any childItems()-based search from `root` for that
                    // *other* tab's content would recurse into it looking for
                    // a non-match, which is unsafe (see find_visual_child).
                    active: sectionStack.currentIndex === 1
                    source: "qrc:/Views/MS1SpectraSection.qml"
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }
                Loader {
                    id: annotationsLoader
                    objectName: "annotationsSectionLoader"
                    source: "qrc:/Views/AnnotationsSection.qml"
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }
                Loader {
                    id: visualLoader
                    objectName: "visualSectionLoader"
                    source: "qrc:/Views/VisualInspectionSection.qml"
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }
            }
        }
    }
}

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

        // Title, section nav and "back to project" all on one row — the
        // nav rail used to be a fixed 180px-wide left-hand column, taking
        // width away from every section's own content (which all need the
        // room). Moving it up here next to the title gives every section
        // the page's full width instead.
        RowLayout {
            objectName: "analysisPageHeader"
            Layout.fillWidth: true
            Layout.margins: 16
            spacing: 24

            ColumnLayout {
                id: analysisTitleBlock
                objectName: "analysisTitleBlock"
                spacing: 0

                Text {
                    objectName: "analysisPageTitle"
                    text: "Project: " + (analysis ? analysis.projectName : "")
                    font.pixelSize: 16
                    font.bold: true
                }
                Text {
                    objectName: "analysisPageRunId"
                    text: "Analysis: " + (analysis ? analysis.runId : "")
                    font.pixelSize: 12
                    color: "gray"
                }
                Text {
                    objectName: "analysisPageDate"
                    text: "Date: " + (analysis ? analysis.startDateDisplay : "")
                    font.pixelSize: 12
                    color: "gray"
                }
            }

            RowLayout {
                id: navRail
                objectName: "navRail"
                spacing: 4

                Repeater {
                    id: navRepeater
                    objectName: "navRepeater"
                    model: ["Summary", "MS1 Spectra", "Annotations", "Visual Inspection"]

                    delegate: Button {
                        objectName: "navButton_" + index
                        text: modelData
                        checkable: true
                        checked: sectionStack.currentIndex === index
                        onClicked: sectionStack.currentIndex = index
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Button {
                objectName: "backToProjectButton"
                text: "Back to project"
                onClicked: if (analysis) Router.showProjectHomeRequested(analysis.project)
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: palette.mid
        }

        StackLayout {
            id: sectionStack
            objectName: "sectionStack"
            currentIndex: 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 8

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

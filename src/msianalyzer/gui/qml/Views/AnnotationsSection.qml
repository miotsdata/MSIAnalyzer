import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: annotationsSection
    objectName: "annotationsSection"

    property var analysis
    property var rows: (analysis && analysis.analysisDbPath)
                        ? AnalysisBridge.getAnnotationTable(analysis.analysisDbPath) : []

    Text {
        id: emptyStateLabel
        objectName: "annotationsEmptyStateLabel"
        visible: rows.length === 0
        anchors.centerIn: parent
        text: "No annotated features."
        color: "gray"
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        visible: rows.length > 0
        spacing: 8

        Text {
            text: "Annotated features (" + rows.length + ")"
            font.bold: true
            font.pixelSize: 16
        }

        Flickable {
            id: tableFlickable
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: tableColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: tableColumn
                width: tableFlickable.width
                spacing: 4

                Repeater {
                    id: annotationsRepeater
                    objectName: "annotationsRepeater"
                    model: annotationsSection.rows

                    delegate: Rectangle {
                        objectName: "annotationRow_" + modelData.feature_id
                        Layout.fillWidth: true
                        height: 48
                        border.color: "#dddddd"
                        border.width: 1
                        radius: 4

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                popupLoader.item.featureId = modelData.feature_id
                                popupLoader.item.open()
                            }
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8

                            Text {
                                objectName: "annotationRowFeature_" + modelData.feature_id
                                text: "Feature " + modelData.feature_id
                                Layout.preferredWidth: 100
                            }
                            Text {
                                objectName: "annotationRowCompound_" + modelData.feature_id
                                text: modelData.compound_name || modelData.inchikey || "?"
                                Layout.fillWidth: true
                            }
                            Text {
                                objectName: "annotationRowScore_" + modelData.feature_id
                                text: "score " + Number(modelData.best_score).toFixed(3)
                                Layout.preferredWidth: 100
                            }
                        }
                    }
                }
            }
        }
    }

    Loader {
        id: popupLoader
        objectName: "candidatesPopupLoader"
        source: "qrc:/Views/AnnotationCandidatesPopup.qml"
        // Qt.binding — see the comment on summaryLoader in AnalysisPage.qml;
        // same risk, doubly so here since this Loader is itself nested
        // inside another Loader's just-loaded content.
        onLoaded: item.analysis = Qt.binding(function () { return annotationsSection.analysis })
    }
}

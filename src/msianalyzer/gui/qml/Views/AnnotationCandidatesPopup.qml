import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine

Popup {
    id: candidatesPopup
    objectName: "candidatesPopup"
    modal: true
    focus: true
    width: 900
    height: 600
    anchors.centerIn: Overlay.overlay

    property var analysis
    property int featureId: -1
    property var candidates: (analysis && analysis.analysisDbPath && featureId >= 0)
                              ? AnalysisBridge.getAnnotationCandidates(analysis.analysisDbPath, featureId)
                              : []
    property string sampleFilter: ""
    property string libraryFilter: ""
    property var selectedCandidate: null

    readonly property var sampleNames: {
        var names = []
        for (var i = 0; i < candidates.length; i++) {
            var n = candidates[i].sample_name
            if (n && names.indexOf(n) < 0)
                names.push(n)
        }
        return names
    }
    readonly property var libraryNames: {
        var names = []
        for (var i = 0; i < candidates.length; i++) {
            var n = candidates[i].library_name
            if (n && names.indexOf(n) < 0)
                names.push(n)
        }
        return names
    }
    readonly property var filteredCandidates: {
        var result = []
        for (var i = 0; i < candidates.length; i++) {
            var c = candidates[i]
            if (sampleFilter !== "" && c.sample_name !== sampleFilter)
                continue
            if (libraryFilter !== "" && c.library_name !== libraryFilter)
                continue
            result.push(c)
        }
        return result
    }

    onOpened: {
        sampleFilter = ""
        libraryFilter = ""
        selectedCandidate = null
    }

    onSelectedCandidateChanged: {
        if (selectedCandidate && analysis && analysis.analysisDbPath) {
            // A file:// url, not loadHtml() — see AnalysisBridge.getMirrorPlotUrl:
            // loadHtml()/setHtml() silently fail past Qt's ~2MB limit, and a
            // plot with Plotly.js embedded is already ~4.6MB on its own.
            mirrorPlotView.url =
                AnalysisBridge.getMirrorPlotUrl(analysis.analysisDbPath, selectedCandidate.id)
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 12

        ColumnLayout {
            objectName: "candidatesPanel"
            Layout.preferredWidth: 340
            Layout.fillHeight: true
            spacing: 8

            Text {
                text: "Feature " + candidatesPopup.featureId + " — candidates ("
                      + candidatesPopup.filteredCandidates.length + ")"
                font.bold: true
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true

                ComboBox {
                    id: sampleCombo
                    objectName: "sampleFilterCombo"
                    Layout.fillWidth: true
                    model: [""].concat(candidatesPopup.sampleNames)
                    displayText: currentText === "" ? "All samples" : currentText
                    onActivated: (index) => candidatesPopup.sampleFilter = model[index]
                }
                ComboBox {
                    id: libraryCombo
                    objectName: "libraryFilterCombo"
                    Layout.fillWidth: true
                    model: [""].concat(candidatesPopup.libraryNames)
                    displayText: currentText === "" ? "All libraries" : currentText
                    onActivated: (index) => candidatesPopup.libraryFilter = model[index]
                }
            }

            Flickable {
                id: candidatesFlickable
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: width
                contentHeight: candidatesColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                ColumnLayout {
                    id: candidatesColumn
                    width: candidatesFlickable.width
                    spacing: 4

                    Repeater {
                        id: candidatesRepeater
                        objectName: "candidatesRepeater"
                        model: candidatesPopup.filteredCandidates

                        delegate: Rectangle {
                            objectName: "candidateRow_" + modelData.id
                            Layout.fillWidth: true
                            height: 56
                            border.color: (candidatesPopup.selectedCandidate
                                           && candidatesPopup.selectedCandidate.id === modelData.id)
                                          ? "#1f77b4" : "#dddddd"
                            border.width: (candidatesPopup.selectedCandidate
                                           && candidatesPopup.selectedCandidate.id === modelData.id)
                                          ? 2 : 1
                            radius: 4

                            MouseArea {
                                anchors.fill: parent
                                onClicked: candidatesPopup.selectedCandidate = modelData
                            }

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 6
                                spacing: 2

                                Text {
                                    objectName: "candidateRowLabel_" + modelData.id
                                    text: (modelData.compound_name || modelData.inchikey || "?")
                                          + "  ·  score " + Number(modelData.score).toFixed(3)
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    objectName: "candidateRowMeta_" + modelData.id
                                    text: (modelData.sample_name || "?") + "  ·  " + (modelData.library_name || "?")
                                    font.pixelSize: 11
                                    color: "gray"
                                }
                            }
                        }
                    }
                }
            }
        }

        WebEngineView {
            id: mirrorPlotView
            objectName: "mirrorPlotView"
            Layout.fillWidth: true
            Layout.fillHeight: true
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine
import QtWebChannel

Item {
    id: ms1Section
    objectName: "ms1Section"

    property var analysis
    property var samples: (analysis && analysis.analysisDbPath)
                           ? AnalysisBridge.getSamples(analysis.analysisDbPath) : []
    // Stays a live binding to `samples` until the user picks a different
    // sample (ComboBox.onActivated below), matching QML's normal
    // binding-until-imperatively-assigned behaviour.
    property int selectedSampleId: samples.length > 0 ? samples[0].sample_id : -1
    property int topN: 5
    property var featureDetail: null

    WebChannel {
        id: webChannel
    }

    Component.onCompleted: webChannel.registerObject("analysisBridge", AnalysisBridge)

    Connections {
        target: AnalysisBridge
        function onSpectrumPointClicked(mz) {
            if (analysis && analysis.analysisDbPath) {
                ms1Section.featureDetail = AnalysisBridge.getFeatureDetail(
                    analysis.analysisDbPath, mz, ms1Section.topN)
            }
        }
    }

    onSelectedSampleIdChanged: {
        if (selectedSampleId >= 0 && analysis && analysis.analysisDbPath && analysis.runId) {
            spectrumView.loadHtml(
                AnalysisBridge.getSpectrumHtml(analysis.analysisDbPath, analysis.runId, selectedSampleId))
        }
    }

    Text {
        id: emptyStateLabel
        objectName: "ms1EmptyStateLabel"
        visible: ms1Section.samples.length === 0
        anchors.centerIn: parent
        text: "No samples in this analysis."
        color: "gray"
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        visible: ms1Section.samples.length > 0
        spacing: 12

        // Declared BEFORE the spectrum/WebEngineView column: a
        // childItems()-based search (find_visual_child) that doesn't match
        // this branch would otherwise have to recurse fully into the
        // WebEngineView's internal item tree before reaching this sibling —
        // unsafe. Keeping this first means every name this panel contains
        // resolves without ever touching the WebEngineView.
        ColumnLayout {
            id: detailPanel
            objectName: "detailPanel"
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            spacing: 6

            Text { text: "Feature detail"; font.bold: true }

            Text {
                objectName: "detailEmptyLabel"
                visible: !ms1Section.featureDetail
                text: "Click a peak to see details."
                color: "gray"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            ColumnLayout {
                visible: !!ms1Section.featureDetail
                Layout.fillWidth: true
                spacing: 6

                Text {
                    objectName: "detailFeatureId"
                    text: ms1Section.featureDetail
                          ? ("Feature " + ms1Section.featureDetail.feature_id
                             + " (m/z " + Number(ms1Section.featureDetail.mz).toFixed(4) + ")")
                          : ""
                    font.bold: true
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                Text {
                    objectName: "detailNMs2"
                    text: ms1Section.featureDetail ? ("MS2 scans: " + ms1Section.featureDetail.n_ms2) : ""
                }
                Text { text: "Present in:"; font.bold: true }
                Text {
                    objectName: "detailSamplesPresent"
                    text: ms1Section.featureDetail
                          ? (ms1Section.featureDetail.samples_present.join(", ") || "none")
                          : ""
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                Text { text: "Absent / filtered out in:"; font.bold: true }
                Text {
                    objectName: "detailSamplesAbsent"
                    text: ms1Section.featureDetail
                          ? (ms1Section.featureDetail.samples_absent.join(", ") || "none")
                          : ""
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "Top hits:"; font.bold: true }
                    SpinBox {
                        id: topNSpin
                        objectName: "topNSpinBox"
                        from: 1
                        to: 20
                        value: ms1Section.topN
                        onValueModified: {
                            ms1Section.topN = value
                            if (analysis && analysis.analysisDbPath && ms1Section.featureDetail) {
                                ms1Section.featureDetail = AnalysisBridge.getFeatureDetail(
                                    analysis.analysisDbPath, ms1Section.featureDetail.mz, value)
                            }
                        }
                    }
                }

                Text {
                    objectName: "detailNoAnnotationLabel"
                    visible: ms1Section.featureDetail && ms1Section.featureDetail.top_hits.length === 0
                    text: "No annotation for this feature."
                    color: "gray"
                }

                Repeater {
                    id: topHitsRepeater
                    objectName: "topHitsRepeater"
                    model: ms1Section.featureDetail ? ms1Section.featureDetail.top_hits : []

                    delegate: Text {
                        objectName: "topHit_" + index
                        text: (index + 1) + ". " + (modelData.compound_name || modelData.inchikey || "?")
                              + " (score " + Number(modelData.score).toFixed(3) + ")"
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            RowLayout {
                Layout.fillWidth: true

                Text { text: "Sample:" }
                ComboBox {
                    id: sampleCombo
                    objectName: "sampleCombo"
                    Layout.fillWidth: true
                    model: ms1Section.samples
                    textRole: "name"
                    onActivated: (index) => {
                        ms1Section.selectedSampleId = ms1Section.samples[index].sample_id
                        ms1Section.featureDetail = null
                    }
                }
            }

            WebEngineView {
                id: spectrumView
                objectName: "spectrumView"
                Layout.fillWidth: true
                Layout.fillHeight: true
                webChannel: webChannel
            }
        }
    }
}

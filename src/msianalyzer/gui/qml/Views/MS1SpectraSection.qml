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

    // "present in and absent in should have list of files (scrollable,
    // max 10 files height before scrolling)" — one row's height times 10.
    property int detailListRowHeight: 18
    property int detailListMaxHeight: detailListRowHeight * 10
    // Present in / absent in "are less important than the top n hits...
    // with less space given to them" — half of the top-hits cap.
    property int detailSecondaryListMaxHeight: detailListRowHeight * 5

    // AnalysisPage.qml's loading overlay reads this to decide when the
    // MS1 tab is truly ready ("the whole page is ready (plots
    // included)") — `spectrumView.loading` alone would read `false`
    // (nothing loaded yet) in the brief moment before `selectedSampleId`
    // assigns a real URL, so also count "no URL assigned yet, but there
    // are samples to load one for" as still loading.
    readonly property bool plotLoading: ms1Section.samples.length > 0
                                         && (spectrumView.url == "" || spectrumView.loading)

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
            // A file:// url, not loadHtml() — see AnalysisBridge.getSpectrumUrl:
            // loadHtml()/setHtml() silently fail past Qt's ~2MB limit, and a
            // plot with Plotly.js embedded is already ~4.6MB on its own.
            spectrumView.url =
                AnalysisBridge.getSpectrumUrl(analysis.analysisDbPath, analysis.runId, selectedSampleId)
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

    // A 2-row layout, not 2 columns — MS1 spectra want to be wide, so the
    // plot gets the section's full width up top, with the feature-detail
    // panel (itself laid out horizontally, since it now has that same
    // full width to spread into) as a shorter strip underneath.
    //
    // Plain Item + anchors here, not a ColumnLayout: `detailPanel` is
    // declared *before* the spectrum column even though it renders
    // *below* it (anchored to the bottom) — a childItems()-based search
    // (find_visual_child) for anything inside detailPanel must never have
    // to walk through the WebEngineView's own internal item tree first to
    // get there (matches every other section with a WebEngineView; see
    // the sibling comment on AnnotationsSection.qml/that pattern's
    // original introduction). A ColumnLayout lays out children in
    // declaration order, so achieving "detail panel visually last" would
    // otherwise force "detail panel declared last" too — anchors decouple
    // the two.
    Item {
        anchors.fill: parent
        anchors.margins: 12
        visible: ms1Section.samples.length > 0

        ColumnLayout {
            id: detailPanel
            objectName: "detailPanel"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 220
            spacing: 10

            Text {
                objectName: "detailEmptyLabel"
                visible: !ms1Section.featureDetail
                text: "Click a peak to see details."
                color: "gray"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            // Feature id / m/z / MS2 count — one row, not stacked lines.
            RowLayout {
                objectName: "featureDetailRow"
                visible: !!ms1Section.featureDetail
                Layout.fillWidth: true
                spacing: 20

                Text {
                    objectName: "detailFeatureId"
                    text: ms1Section.featureDetail
                          ? ("Feature " + ms1Section.featureDetail.feature_id
                             + " (m/z " + Number(ms1Section.featureDetail.mz).toFixed(4) + ")")
                          : ""
                    font.bold: true
                    wrapMode: Text.Wrap
                }
                Text {
                    objectName: "detailNMs2"
                    text: ms1Section.featureDetail ? ("MS2 scans: " + ms1Section.featureDetail.n_ms2) : ""
                }
                Item { Layout.fillWidth: true }
            }

            // Present in / absent in / top hits — 3 equal-width columns.
            // Present/absent are secondary info next to the annotation
            // hits, so their own lists get a smaller height cap
            // (detailSecondaryListMaxHeight) than top hits'.
            RowLayout {
                visible: !!ms1Section.featureDetail
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 20

                ColumnLayout {
                    objectName: "samplesPresentColumnContainer"
                    Layout.preferredWidth: 0
                    Layout.minimumWidth: 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 4

                    Text {
                        text: "Present in:"
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Text {
                        objectName: "samplesPresentEmptyLabel"
                        visible: ms1Section.featureDetail
                                 && ms1Section.featureDetail.samples_present.length === 0
                        text: "none"
                        color: "gray"
                    }

                    Flickable {
                        id: samplesPresentFlickable
                        objectName: "samplesPresentFlickable"
                        visible: ms1Section.featureDetail
                                 && ms1Section.featureDetail.samples_present.length > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: ms1Section.detailSecondaryListMaxHeight
                        clip: true
                        contentWidth: width
                        contentHeight: samplesPresentColumn.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds

                        ColumnLayout {
                            id: samplesPresentColumn
                            width: samplesPresentFlickable.width
                            spacing: 2

                            Repeater {
                                id: samplesPresentRepeater
                                objectName: "samplesPresentRepeater"
                                model: ms1Section.featureDetail ? ms1Section.featureDetail.samples_present : []

                                delegate: Text {
                                    objectName: "samplesPresentItem_" + index
                                    text: modelData
                                    elide: Text.ElideMiddle
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }

                // A thin vertical divider between the 3 columns — same
                // style as the horizontal one below the page header
                // (AnalysisPage.qml).
                Rectangle {
                    objectName: "presentAbsentDivider"
                    Layout.fillHeight: true
                    width: 1
                    color: palette.mid
                }

                ColumnLayout {
                    objectName: "samplesAbsentColumnContainer"
                    Layout.preferredWidth: 0
                    Layout.minimumWidth: 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 4

                    Text {
                        text: "Absent / filtered out in:"
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Text {
                        objectName: "samplesAbsentEmptyLabel"
                        visible: ms1Section.featureDetail
                                 && ms1Section.featureDetail.samples_absent.length === 0
                        text: "none"
                        color: "gray"
                    }

                    Flickable {
                        id: samplesAbsentFlickable
                        objectName: "samplesAbsentFlickable"
                        visible: ms1Section.featureDetail
                                 && ms1Section.featureDetail.samples_absent.length > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: ms1Section.detailSecondaryListMaxHeight
                        clip: true
                        contentWidth: width
                        contentHeight: samplesAbsentColumn.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds

                        ColumnLayout {
                            id: samplesAbsentColumn
                            width: samplesAbsentFlickable.width
                            spacing: 2

                            Repeater {
                                id: samplesAbsentRepeater
                                objectName: "samplesAbsentRepeater"
                                model: ms1Section.featureDetail ? ms1Section.featureDetail.samples_absent : []

                                delegate: Text {
                                    objectName: "samplesAbsentItem_" + index
                                    text: modelData
                                    elide: Text.ElideMiddle
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    objectName: "absentTopHitsDivider"
                    Layout.fillHeight: true
                    width: 1
                    color: palette.mid
                }

                ColumnLayout {
                    objectName: "topHitsColumnContainer"
                    Layout.preferredWidth: 0
                    Layout.minimumWidth: 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Top hits:"
                            font.bold: true
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        SpinBox {
                            id: topNSpin
                            objectName: "topNSpinBox"
                            editable: true
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
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }

                    Flickable {
                        id: topHitsFlickable
                        objectName: "topHitsFlickable"
                        visible: ms1Section.featureDetail && ms1Section.featureDetail.top_hits.length > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: ms1Section.detailListMaxHeight
                        clip: true
                        contentWidth: width
                        contentHeight: topHitsColumn.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds

                        ColumnLayout {
                            id: topHitsColumn
                            width: topHitsFlickable.width
                            spacing: 2

                            Repeater {
                                id: topHitsRepeater
                                objectName: "topHitsRepeater"
                                model: ms1Section.featureDetail ? ms1Section.featureDetail.top_hits : []

                                delegate: Text {
                                    objectName: "topHit_" + index
                                    // Score bolded ("so easy to see") via
                                    // StyledText — compound_name/inchikey/
                                    // library_name are chemistry-database
                                    // strings (names, formulas, InChIKeys),
                                    // never containing markup in practice.
                                    text: (index + 1) + ". " + (modelData.compound_name || modelData.inchikey || "?")
                                          + " (score <b>" + Number(modelData.score).toFixed(3) + "</b>)"
                                          + (modelData.library_name ? " · " + modelData.library_name : "")
                                    textFormat: Text.StyledText
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }
            }
        }

        ColumnLayout {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: detailPanel.top
            anchors.bottomMargin: 12
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

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"
import "qrc:/Utils/SearchQuery.js" as SearchQuery

// Feature Correlation (ADR 66): pick a feature on the left, see which
// features rise and fall with it across each sample's pixels (most
// correlated) and which avoid it (most anti-correlated). Each pick reads
// one row of every sample's stored matrix, on a background thread
// (AnalysisBridge.requestFeatureCorrelation).
Item {
    id: featureCorrelationSection
    objectName: "featureCorrelationSection"

    // Right-click a row -> Visual Inspection on that feature (ADR 32
    // handoff, see AnalysisPage.qml's featureCorrLoader).
    signal showSpatialDistributionRequested(real mz)

    property var analysis
    readonly property string dbPath: (analysis && analysis.analysisDbPath) ? analysis.analysisDbPath : ""

    // --- Feature picker -------------------------------------------------
    property var features: []
    property bool featuresLoading: false
    property string nameQuery: ""
    property string mzQuery: ""
    readonly property var filteredFeatures: featureCorrelationSection.features.filter(function (f) {
        return SearchQuery.matchesName(featureCorrelationSection.nameQuery, f.compound_name)
            && SearchQuery.matchesMz(featureCorrelationSection.mzQuery, f.mz)
    })
    property real selectedMz: NaN
    readonly property var selectedFeature: {
        var mz = featureCorrelationSection.selectedMz
        for (var i = 0; i < featureCorrelationSection.features.length; i++)
            if (Math.abs(featureCorrelationSection.features[i].mz - mz) < 1e-6)
                return featureCorrelationSection.features[i]
        return null
    }

    function featureLabel(f) {
        return f.compound_name ? (Number(f.mz).toFixed(4) + ": " + f.compound_name)
                               : ("m/z " + Number(f.mz).toFixed(4))
    }

    // --- Correlations of the selected feature ----------------------------
    // `samples` lists the samples with a stored matrix even before a
    // feature is picked (a NaN request), for the empty state and the
    // overwrite question.
    property var result: ({ "samples": [], "rows": [] })
    property bool resultLoading: false
    property int topN: 25
    readonly property var sampleNames: featureCorrelationSection.result.samples || []
    readonly property var allRows: featureCorrelationSection.result.rows || []
    readonly property var topRows: featureCorrelationSection.allRows.slice(0, featureCorrelationSection.topN)
    readonly property var bottomRows: featureCorrelationSection.allRows
                                          .slice(Math.max(0, featureCorrelationSection.allRows.length - featureCorrelationSection.topN))
                                          .reverse()
    // AnalysisPage's loading overlay: the first feature list and sample
    // check, not each later pick (the right panel shows its own spinner).
    property bool infoLoading: true
    readonly property bool rowsLoading: featureCorrelationSection.featuresLoading
                                        || featureCorrelationSection.infoLoading

    function refreshFeatures() {
        if (!featureCorrelationSection.dbPath) return
        featureCorrelationSection.featuresLoading = true
        AnalysisBridge.requestFeatureList(featureCorrelationSection.dbPath)
    }
    function refreshCorrelation() {
        if (!featureCorrelationSection.dbPath) return
        featureCorrelationSection.resultLoading = true
        AnalysisBridge.requestFeatureCorrelation(featureCorrelationSection.dbPath,
                                                 featureCorrelationSection.selectedMz)
    }
    function selectFeature(mz) {
        featureCorrelationSection.selectedMz = mz
        featureCorrelationSection.refreshCorrelation()
    }

    // Remembered across tab switches, like Visual Inspection's.
    signal stateSaved(var state)
    Component.onDestruction: featureCorrelationSection.stateSaved({
        "datasetId": AnalysisBridge.activeDatasetId,
        "selectedMz": featureCorrelationSection.selectedMz,
        "topN": featureCorrelationSection.topN,
        "nameQuery": featureCorrelationSection.nameQuery,
        "mzQuery": featureCorrelationSection.mzQuery
    })
    function restoreState(s) {
        if (!s || s.datasetId !== AnalysisBridge.activeDatasetId) return
        featureCorrelationSection.topN = s.topN
        featureCorrelationSection.nameQuery = s.nameQuery
        featureCorrelationSection.mzQuery = s.mzQuery
        featureCorrelationSection.selectedMz = s.selectedMz
        featureCorrelationSection.refreshCorrelation()
    }

    onDbPathChanged: {
        featureCorrelationSection.refreshFeatures()
        featureCorrelationSection.refreshCorrelation()
    }
    Component.onCompleted: {
        featureCorrelationSection.refreshDatasetInfo()
        featureCorrelationSection.refreshFeatures()
        featureCorrelationSection.refreshCorrelation()
    }

    // --- Compute after the run --------------------------------------------
    // The pipeline computes it during Run when `feature_correlation` is
    // on. This bar computes it afterwards on whichever dataset is
    // selected (same shape as Spatial Correlation's Moran's I bar).
    readonly property bool datasetActive: (AnalysisBridge ? AnalysisBridge.activeDatasetId : -1) >= 0
    property var datasetInfo: ({})
    property bool computing: false
    property int computeDone: 0
    property int computeTotal: 0
    property string computeMessage: ""
    property bool computeFailed: false

    function refreshDatasetInfo() {
        featureCorrelationSection.datasetInfo = featureCorrelationSection.datasetActive
            ? AnalysisBridge.getActiveDatasetInfo() : ({})
        featureCorrelationSection.computeMessage = ""
    }
    readonly property var layerOptions: featureCorrelationSection.datasetActive
        ? ["X"].concat((featureCorrelationSection.datasetInfo.layers || [])
                       .filter(function (l) { return l !== "localMoranI" }))
        : [(featureCorrelationSection.dbPath ? AnalysisBridge.getNormalizedLayerName(featureCorrelationSection.dbPath) : "TIC"), "X"]

    function requestCompute() {
        if (featureCorrelationSection.sampleNames.length > 0)
            overwriteDialog.open()
        else
            featureCorrelationSection.compute()
    }
    function compute() {
        featureCorrelationSection.computing = true
        featureCorrelationSection.computeFailed = false
        featureCorrelationSection.computeMessage = ""
        featureCorrelationSection.computeDone = 0
        featureCorrelationSection.computeTotal = featureCorrelationSection.datasetActive
            ? (featureCorrelationSection.datasetInfo.samples || []).length
            : AnalysisBridge.getInspectionSamples(featureCorrelationSection.dbPath).length
        AnalysisBridge.computeDatasetFeatureCorrelation(featureCorrelationSection.dbPath,
                                                        layerCombo.currentText, methodCombo.currentText)
    }

    Connections {
        target: AnalysisBridge
        function onFeatureListReady(rows) {
            featureCorrelationSection.features = rows
            featureCorrelationSection.featuresLoading = false
        }
        function onFeatureCorrelationReady(payload) {
            featureCorrelationSection.result = payload
            featureCorrelationSection.resultLoading = false
            featureCorrelationSection.infoLoading = false
        }
        function onActiveDatasetChanged() {
            featureCorrelationSection.refreshDatasetInfo()
            featureCorrelationSection.refreshFeatures()
            featureCorrelationSection.refreshCorrelation()
        }
        // Feature names changed (post-run target list, ADR 60).
        function onTargetListMatchesChanged(dbPath) { featureCorrelationSection.refreshCorrelation() }
        function onDatasetTaskProgress(done, total, sampleName) {
            if (!featureCorrelationSection.computing) return
            featureCorrelationSection.computeDone = done
            featureCorrelationSection.computeTotal = total
        }
        function onDatasetFeatureCorrelationFinished(message) {
            if (!featureCorrelationSection.computing) return
            featureCorrelationSection.computing = false
            featureCorrelationSection.computeMessage = message
            featureCorrelationSection.refreshCorrelation()
        }
        function onDatasetFeatureCorrelationFailed(message) {
            if (!featureCorrelationSection.computing) return
            featureCorrelationSection.computing = false
            featureCorrelationSection.computeFailed = true
            featureCorrelationSection.computeMessage = message
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 16

        RowLayout {
            objectName: "featureCorrelationComputeBar"
            Layout.fillWidth: true
            spacing: 8

            Label {
                text: (featureCorrelationSection.datasetActive
                       ? "Dataset \"" + (featureCorrelationSection.datasetInfo.name || "") + "\""
                       : "Pipeline output") + " — layer:"
                font.bold: true
            }
            ComboBox {
                id: layerCombo
                objectName: "featureCorrelationLayerCombo"
                enabled: !featureCorrelationSection.computing
                model: featureCorrelationSection.layerOptions
                currentIndex: Math.max(0, model.indexOf("TIC"))
                ToolTip.visible: hovered
                ToolTip.text: "Which intensities to correlate: the normalized layer, or \"X\" (the file's main matrix)."
            }
            Label { text: "Method:" }
            ComboBox {
                id: methodCombo
                objectName: "featureCorrelationMethodCombo"
                enabled: !featureCorrelationSection.computing
                model: ["pearson", "spearman"]
                ToolTip.visible: hovered
                ToolTip.text: "Pearson: on the intensities. Spearman: on their ranks, so a few very bright pixels can't dominate."
            }
            Button {
                objectName: "featureCorrelationComputeButton"
                text: "Compute feature correlation"
                enabled: !featureCorrelationSection.computing && featureCorrelationSection.dbPath !== ""
                onClicked: featureCorrelationSection.requestCompute()
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            Dialog {
                id: overwriteDialog
                objectName: "overwriteFeatureCorrelationDialog"
                title: "Recompute feature correlation?"
                modal: true
                anchors.centerIn: Overlay.overlay
                standardButtons: Dialog.Yes | Dialog.Cancel
                Label {
                    text: "Some samples already have a feature correlation. Recompute and overwrite it?"
                    wrapMode: Text.Wrap
                }
                onAccepted: featureCorrelationSection.compute()
            }
            ProgressBar {
                objectName: "featureCorrelationProgressBar"
                visible: featureCorrelationSection.computing
                Layout.preferredWidth: 160
                from: 0
                to: Math.max(1, featureCorrelationSection.computeTotal)
                value: featureCorrelationSection.computeDone
                indeterminate: featureCorrelationSection.computeDone === 0
            }
            Label {
                objectName: "featureCorrelationMessageLabel"
                visible: featureCorrelationSection.computeMessage !== ""
                text: featureCorrelationSection.computeMessage
                color: featureCorrelationSection.computeFailed ? Theme.errorColor : Theme.mutedTextColor
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Item { Layout.fillWidth: true; visible: featureCorrelationSection.computeMessage === "" }
        }

        Label {
            objectName: "featureCorrelationEmptyStateLabel"
            visible: !featureCorrelationSection.rowsLoading && featureCorrelationSection.sampleNames.length === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            text: featureCorrelationSection.datasetActive
                  ? "No feature correlation for this imported dataset yet — use \"Compute feature correlation\" above."
                  : "No feature correlation yet — use \"Compute feature correlation\" above."
            color: Theme.mutedTextColor
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        SplitView {
            objectName: "featureCorrelationSplit"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: featureCorrelationSection.sampleNames.length > 0
            orientation: Qt.Horizontal

            // --- Left: pick a feature ---
            ColumnLayout {
                SplitView.preferredWidth: 320
                SplitView.minimumWidth: 220
                spacing: 6

                Label { text: "Feature"; font.bold: true }
                TextField {
                    objectName: "featureCorrelationNameSearchField"
                    Layout.fillWidth: true
                    placeholderText: "Search name…"
                    text: featureCorrelationSection.nameQuery
                    onTextChanged: featureCorrelationSection.nameQuery = text
                }
                TextField {
                    objectName: "featureCorrelationMzSearchField"
                    Layout.fillWidth: true
                    placeholderText: "Search m/z…"
                    text: featureCorrelationSection.mzQuery
                    onTextChanged: featureCorrelationSection.mzQuery = text
                }
                Label {
                    objectName: "featureCorrelationSearchEmptyLabel"
                    visible: featureCorrelationSection.features.length > 0
                             && featureCorrelationSection.filteredFeatures.length === 0
                    text: "No features match your search."
                    color: Theme.mutedTextColor
                }
                ListView {
                    id: featureList
                    objectName: "featureCorrelationFeatureList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: featureCorrelationSection.filteredFeatures
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    delegate: ItemDelegate {
                        required property var modelData
                        required property int index
                        objectName: "featureCorrelationFeature_" + index
                        width: featureList.width
                        text: featureCorrelationSection.featureLabel(modelData)
                        highlighted: Math.abs(modelData.mz - featureCorrelationSection.selectedMz) < 1e-6
                        onClicked: featureCorrelationSection.selectFeature(modelData.mz)
                    }
                }
            }

            // --- Right: its most / least correlated features ---
            ColumnLayout {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 300
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        objectName: "featureCorrelationSelectedLabel"
                        Layout.fillWidth: true
                        text: featureCorrelationSection.selectedFeature
                              ? featureCorrelationSection.featureLabel(featureCorrelationSection.selectedFeature)
                              : "Pick a feature on the left."
                        font.bold: featureCorrelationSection.selectedFeature !== null
                        color: featureCorrelationSection.selectedFeature ? palette.windowText : Theme.mutedTextColor
                        elide: Text.ElideRight
                    }
                    BusyIndicator {
                        visible: featureCorrelationSection.resultLoading
                        running: visible
                        Layout.preferredHeight: 24
                        Layout.preferredWidth: 24
                    }
                    Label { text: "Show" }
                    SpinBox {
                        objectName: "featureCorrelationTopNSpin"
                        from: 1
                        to: 500
                        editable: true
                        value: featureCorrelationSection.topN
                        onValueModified: featureCorrelationSection.topN = value
                    }
                    Label { text: "per table" }
                }
                Label {
                    objectName: "featureCorrelationInfoLabel"
                    Layout.fillWidth: true
                    text: featureCorrelationSection.result.method
                          ? (featureCorrelationSection.result.method.charAt(0).toUpperCase()
                             + featureCorrelationSection.result.method.slice(1)
                             + " r on layer \"" + featureCorrelationSection.result.layer + "\"; mean r over "
                             + featureCorrelationSection.sampleNames.length + " sample(s), each sample scored on its own pixels.")
                          : ""
                    color: Theme.mutedTextColor
                    wrapMode: Text.Wrap
                }
                Label {
                    objectName: "featureCorrelationNoScoresLabel"
                    visible: !isNaN(featureCorrelationSection.selectedMz)
                             && !featureCorrelationSection.resultLoading
                             && featureCorrelationSection.allRows.length === 0
                    text: "No correlation scores for this feature (e.g. never detected in these samples)."
                    color: Theme.mutedTextColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                FeatureCorrelationTable {
                    objectName: "mostCorrelatedTable"
                    tableName: "mostCorrelated"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: featureCorrelationSection.allRows.length > 0
                    title: "Most correlated"
                    rows: featureCorrelationSection.topRows
                    samples: featureCorrelationSection.sampleNames
                    onShowSpatialDistributionRequested: (mz) => featureCorrelationSection.showSpatialDistributionRequested(mz)
                }
                FeatureCorrelationTable {
                    objectName: "mostAntiCorrelatedTable"
                    tableName: "mostAntiCorrelated"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: featureCorrelationSection.allRows.length > 0
                    title: "Most anti-correlated"
                    rows: featureCorrelationSection.bottomRows
                    samples: featureCorrelationSection.sampleNames
                    onShowSpatialDistributionRequested: (mz) => featureCorrelationSection.showSpatialDistributionRequested(mz)
                }
            }
        }
    }
}

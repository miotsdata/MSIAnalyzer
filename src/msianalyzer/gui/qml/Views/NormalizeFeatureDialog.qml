import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"
import "qrc:/Utils/SearchQuery.js" as SearchQuery

// "Analyses > Normalize to feature…" (ADR 61): normalize a finished
// analysis to one feature without re-running it. Same name/m/z search as
// Visual Inspection's feature picker; "Normalize" adds layer
// "feature_<id>" to every sample file and merged.h5ad on a background
// thread, then Visual Inspection lists it under "Normalized to feature".
//
// The list is a ListView over a plain *integer* count indexing `order`
// into `features` (see ImportH5adDialog's comment on the test harness).
Dialog {
    id: normalizeDialog
    objectName: "normalizeFeatureDialog"
    title: "Normalize to feature"
    modal: true
    width: Math.min(640, (parent ? parent.width : 700) - 60)
    height: Math.min(620, (parent ? parent.height : 700) - 60)
    anchors.centerIn: parent
    standardButtons: Dialog.Close

    property var analysis: null
    property var features: []           // {feature_id, mz, compound_name}
    property var order: []              // indices into `features` matching the search
    property int selectedIndex: -1      // into `features`
    property var layerRows: []          // getFeatureLayers: {layer, feature_id, mz}
    readonly property var existingLayers: layerRows.map(function (l) { return l.layer })
    property int pendingRemoval: -1     // feature id awaiting "Remove" confirmation
    property bool running: false
    property int progressDone: 0
    property int progressTotal: 0
    property string statusText: ""
    property bool statusError: false

    readonly property var selectedFeature: selectedIndex >= 0 ? features[selectedIndex] : null
    readonly property bool selectedHasLayer: selectedFeature !== null
        && existingLayers.indexOf("feature_" + selectedFeature.feature_id) >= 0

    function openFor(analysisModel) {
        analysis = analysisModel
        features = []
        order = []
        selectedIndex = -1
        nameField.text = ""
        mzField.text = ""
        statusText = ""
        statusError = false
        pendingRemoval = -1
        refreshExistingLayers()
        open()
        AnalysisBridge.requestFeatureList(analysisModel.analysisDbPath)
    }

    function refreshExistingLayers() {
        layerRows = analysis ? AnalysisBridge.getFeatureLayers(analysis.analysisDbPath) : []
    }

    function removeLayer(featureId) {
        pendingRemoval = -1
        var error = AnalysisBridge.removeFeatureLayer(analysis.analysisDbPath, analysis.runId, featureId)
        statusText = error ? "Could not remove: " + error : "Layer feature_" + featureId + " removed."
        statusError = !!error
        refreshExistingLayers()
    }

    function applySearch() {
        var idx = []
        for (var i = 0; i < features.length; i++) {
            if (SearchQuery.matchesName(nameField.text, features[i].compound_name)
                    && SearchQuery.matchesMz(mzField.text, features[i].mz))
                idx.push(i)
        }
        order = idx
        if (idx.indexOf(selectedIndex) < 0) selectedIndex = -1
    }

    function label(f) {
        return "m/z " + Number(f.mz).toFixed(4) + "  ·  " + (f.compound_name || "unannotated")
               + "  ·  feature " + f.feature_id
    }

    function normalize() {
        var f = selectedFeature
        running = true
        progressDone = 0
        progressTotal = 0
        statusText = ""
        AnalysisBridge.normalizeToFeature(analysis.analysisDbPath, analysis.runId, f.feature_id, f.mz)
    }

    Connections {
        target: AnalysisBridge
        enabled: normalizeDialog.visible
        function onFeatureListReady(received) {
            if (normalizeDialog.running) return
            normalizeDialog.features = received
            normalizeDialog.applySearch()
        }
        function onDatasetTaskProgress(done, total, sampleName) {
            if (!normalizeDialog.running) return
            normalizeDialog.progressDone = done
            normalizeDialog.progressTotal = total
        }
        function onFeatureNormalizationFinished(layer, message) {
            if (!normalizeDialog.running) return
            normalizeDialog.running = false
            normalizeDialog.statusText = message
            normalizeDialog.statusError = false
            normalizeDialog.refreshExistingLayers()
        }
        function onFeatureNormalizationFailed(message) {
            if (!normalizeDialog.running) return
            normalizeDialog.running = false
            normalizeDialog.statusText = message
            normalizeDialog.statusError = true
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        Label {
            text: "Every pixel is divided by this feature's intensity (relative to its "
                  + "median over all samples), then log1p, as in a targeted analysis. "
                  + "The result is a new layer; the existing layers are kept."
            color: Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        // Existing layers, each removable after a confirmation.
        Flow {
            objectName: "normalizeLayersRow"
            visible: normalizeDialog.layerRows.length > 0
            Layout.fillWidth: true
            spacing: 4
            Label { text: "Normalized layers:"; font.bold: true; height: 28; verticalAlignment: Text.AlignVCenter }
            Repeater {
                model: normalizeDialog.layerRows.length
                delegate: Row {
                    property var row: normalizeDialog.layerRows[index]
                    spacing: 2
                    Label {
                        text: "m/z " + Number(row.mz).toFixed(4) + " (feature " + row.feature_id + ")"
                        height: 28
                        verticalAlignment: Text.AlignVCenter
                    }
                    ToolButton {
                        objectName: "normalizeRemoveLayer_" + index
                        text: "✕"
                        implicitWidth: 22
                        implicitHeight: 22
                        enabled: !normalizeDialog.running
                        ToolTip.visible: hovered
                        ToolTip.text: "Remove this layer"
                        onClicked: normalizeDialog.pendingRemoval = row.feature_id
                    }
                }
            }
        }
        RowLayout {
            visible: normalizeDialog.pendingRemoval >= 0
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.errorColor
                text: "Remove layer feature_" + normalizeDialog.pendingRemoval
                      + " from every sample file and merged.h5ad?"
            }
            Button {
                objectName: "normalizeConfirmRemoveButton"
                text: "Remove"
                onClicked: normalizeDialog.removeLayer(normalizeDialog.pendingRemoval)
            }
            Button {
                objectName: "normalizeCancelRemoveButton"
                text: "Cancel"
                flat: true
                onClicked: normalizeDialog.pendingRemoval = -1
            }
        }

        RowLayout {
            spacing: 8
            TextField {
                id: nameField
                objectName: "normalizeNameSearchField"
                placeholderText: "Search name…"
                Layout.fillWidth: true
                enabled: !normalizeDialog.running
                onAccepted: normalizeDialog.applySearch()
            }
            TextField {
                id: mzField
                objectName: "normalizeMzSearchField"
                placeholderText: "Search m/z…"
                Layout.fillWidth: true
                enabled: !normalizeDialog.running
                onAccepted: normalizeDialog.applySearch()
            }
            Button {
                objectName: "normalizeSearchButton"
                text: "Search"
                enabled: !normalizeDialog.running
                onClicked: normalizeDialog.applySearch()
            }
        }

        Label {
            objectName: "normalizeEmptyLabel"
            visible: normalizeDialog.order.length === 0
            text: normalizeDialog.features.length === 0 ? "Loading features…" : "No features match your search."
            color: Theme.mutedTextColor
        }

        ListView {
            id: featureList
            objectName: "normalizeFeatureList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: normalizeDialog.order.length
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: ItemDelegate {
                property int featureIndex: normalizeDialog.order[index]
                objectName: "normalizeFeatureRow_" + index
                width: featureList.width
                text: normalizeDialog.label(normalizeDialog.features[featureIndex])
                highlighted: featureIndex === normalizeDialog.selectedIndex
                enabled: !normalizeDialog.running
                onClicked: normalizeDialog.selectedIndex = featureIndex
            }
        }

        Label {
            objectName: "normalizeReplaceLabel"
            visible: normalizeDialog.selectedHasLayer && !normalizeDialog.running
            text: "This feature already has a normalized layer. Normalizing again replaces it."
            color: Theme.errorColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        ProgressBar {
            objectName: "normalizeProgressBar"
            visible: normalizeDialog.running
            Layout.fillWidth: true
            from: 0
            to: Math.max(1, normalizeDialog.progressTotal)
            value: normalizeDialog.progressDone
            indeterminate: normalizeDialog.progressDone === 0
        }
        Label {
            visible: normalizeDialog.running
            text: "Normalizing… " + normalizeDialog.progressDone + " / " + normalizeDialog.progressTotal + " samples"
        }
        Label {
            objectName: "normalizeStatusLabel"
            visible: normalizeDialog.statusText !== ""
            text: normalizeDialog.statusText
            color: normalizeDialog.statusError ? Theme.errorColor : Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        RowLayout {
            Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: normalizeDialog.selectedFeature
                      ? "Reference: " + normalizeDialog.label(normalizeDialog.selectedFeature)
                      : "Pick the reference feature."
            }
            Button {
                objectName: "normalizeRunButton"
                text: normalizeDialog.selectedHasLayer ? "Replace layer" : "Normalize"
                highlighted: true
                enabled: normalizeDialog.selectedFeature !== null && !normalizeDialog.running
                onClicked: normalizeDialog.normalize()
            }
        }
    }
}

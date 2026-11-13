import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

// "Analyses > Fill blanks…" (ADR 70): add a copy of one layer in which a
// feature's isolated 0 pixels are filled with the mean of their detected
// neighbors, without re-running. Writes "<layer>_filled_r<radius>" to every
// sample file and merged.h5ad on a background thread; Visual Inspection
// then shows it under "Filled". Same layout as NormalizeFeatureDialog.
Dialog {
    id: fillDialog
    objectName: "fillBlanksDialog"
    title: "Fill blanks"
    modal: true
    width: Math.min(560, (parent ? parent.width : 600) - 60)
    anchors.centerIn: parent
    standardButtons: Dialog.Close

    property var analysis: null
    property var sourceLayers: []       // layers that can be filled
    property var filledRows: []         // getFilledLayers: {layer, source, radius}
    property string pendingRemoval: ""  // layer awaiting "Remove" confirmation
    property bool running: false
    property int progressDone: 0
    property int progressTotal: 0
    property string statusText: ""
    property bool statusError: false

    readonly property string sourceLayer: sourceCombo.currentIndex >= 0
                                          ? sourceLayers[sourceCombo.currentIndex] : ""
    readonly property string outputLayer: sourceLayer + "_filled_r" + radiusSpin.value
    readonly property bool outputExists: filledRows.some(function (r) { return r.layer === fillDialog.outputLayer })
    readonly property int neighborCount: Math.pow(2 * radiusSpin.value + 1, 2) - 1

    function openFor(analysisModel) {
        analysis = analysisModel
        statusText = ""
        statusError = false
        pendingRemoval = ""
        refreshLayers()
        // Default: the run's own normalized layer (TIC, or mz_<value>).
        var normalized = AnalysisBridge.getNormalizedLayerName(analysisModel.analysisDbPath)
        sourceCombo.currentIndex = Math.max(0, sourceLayers.indexOf(normalized))
        open()
    }

    function refreshLayers() {
        var db = analysis ? analysis.analysisDbPath : ""
        filledRows = db ? AnalysisBridge.getFilledLayers(db) : []
        var filled = filledRows.map(function (r) { return r.layer })
        sourceLayers = (db ? AnalysisBridge.getSampleLayers(db) : []).filter(function (l) {
            return filled.indexOf(l) < 0 && l !== "localMoranI"
        })
    }

    function removeLayer(layerName) {
        pendingRemoval = ""
        var error = AnalysisBridge.removeFilledLayer(analysis.analysisDbPath, analysis.runId, layerName)
        statusText = error ? "Could not remove: " + error : "Layer " + layerName + " removed."
        statusError = !!error
        refreshLayers()
    }

    function fill() {
        running = true
        progressDone = 0
        progressTotal = 0
        statusText = ""
        AnalysisBridge.fillBlanks(analysis.analysisDbPath, analysis.runId, sourceLayer,
                                  radiusSpin.value, fractionSpin.value / 100)
    }

    Connections {
        target: AnalysisBridge
        enabled: fillDialog.visible
        function onDatasetTaskProgress(done, total, sampleName) {
            if (!fillDialog.running) return
            fillDialog.progressDone = done
            fillDialog.progressTotal = total
        }
        function onFillBlanksFinished(layer, message) {
            if (!fillDialog.running) return
            fillDialog.running = false
            fillDialog.statusText = message
            fillDialog.statusError = false
            fillDialog.refreshLayers()
        }
        function onFillBlanksFailed(message) {
            if (!fillDialog.running) return
            fillDialog.running = false
            fillDialog.statusText = message
            fillDialog.statusError = true
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        Label {
            text: "Where a feature is 0 in a pixel but most pixels around it have a value, "
                  + "the 0 is replaced by the mean of those values. The result is a new layer; "
                  + "the existing layers are kept. Filled values are estimates, not measurements."
            color: Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        // Existing filled layers, each removable after a confirmation.
        Flow {
            objectName: "filledLayersRow"
            visible: fillDialog.filledRows.length > 0
            Layout.fillWidth: true
            spacing: 4
            Label { text: "Filled layers:"; font.bold: true; height: 28; verticalAlignment: Text.AlignVCenter }
            Repeater {
                model: fillDialog.filledRows.length
                delegate: Row {
                    property var row: fillDialog.filledRows[index]
                    spacing: 2
                    Label { text: row.layer; height: 28; verticalAlignment: Text.AlignVCenter }
                    ToolButton {
                        objectName: "fillRemoveLayer_" + index
                        text: "✕"
                        implicitWidth: 22
                        implicitHeight: 22
                        enabled: !fillDialog.running
                        ToolTip.visible: hovered
                        ToolTip.text: "Remove this layer"
                        onClicked: fillDialog.pendingRemoval = row.layer
                    }
                }
            }
        }
        RowLayout {
            visible: fillDialog.pendingRemoval !== ""
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.errorColor
                text: "Remove layer " + fillDialog.pendingRemoval + " from every sample file and merged.h5ad?"
            }
            Button {
                objectName: "fillConfirmRemoveButton"
                text: "Remove"
                onClicked: fillDialog.removeLayer(fillDialog.pendingRemoval)
            }
            Button {
                text: "Cancel"
                flat: true
                onClicked: fillDialog.pendingRemoval = ""
            }
        }

        GridLayout {
            columns: 2
            columnSpacing: 12
            enabled: !fillDialog.running
            Label { text: "Layer to fill:" }
            ComboBox {
                id: sourceCombo
                objectName: "fillSourceCombo"
                Layout.fillWidth: true
                model: fillDialog.sourceLayers
            }
            Label { text: "Radius (pixels):" }
            SpinBox {
                id: radiusSpin
                objectName: "fillRadiusSpin"
                from: 1
                to: 3
                value: 1
            }
            Label { text: "Neighbors with a value:" }
            RowLayout {
                SpinBox {
                    id: fractionSpin
                    objectName: "fillFractionSpin"
                    from: 50
                    to: 100
                    stepSize: 5
                    value: 60
                    textFromValue: function (value) { return value + " %" }
                    valueFromText: function (text) { return parseInt(text) }
                }
                Label {
                    objectName: "fillNeighborsHint"
                    text: "at least " + AnalysisBridge.fillBlanksRequiredNeighbors(radiusSpin.value, fractionSpin.value / 100)
                          + " of " + fillDialog.neighborCount
                    color: Theme.mutedTextColor
                }
            }
        }

        Label {
            visible: fillDialog.outputExists && !fillDialog.running
            text: fillDialog.outputLayer + " already exists. Filling again replaces it."
            color: Theme.errorColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        ProgressBar {
            objectName: "fillProgressBar"
            visible: fillDialog.running
            Layout.fillWidth: true
            from: 0
            to: Math.max(1, fillDialog.progressTotal)
            value: fillDialog.progressDone
            indeterminate: fillDialog.progressDone === 0
        }
        Label {
            visible: fillDialog.running
            text: "Filling… " + fillDialog.progressDone + " / " + fillDialog.progressTotal + " samples"
        }
        Label {
            objectName: "fillStatusLabel"
            visible: fillDialog.statusText !== ""
            text: fillDialog.statusText
            color: fillDialog.statusError ? Theme.errorColor : Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        RowLayout {
            Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: fillDialog.sourceLayer ? "New layer: " + fillDialog.outputLayer : "No layer to fill."
            }
            Button {
                objectName: "fillRunButton"
                text: fillDialog.outputExists ? "Replace layer" : "Fill"
                highlighted: true
                enabled: fillDialog.sourceLayer !== "" && !fillDialog.running
                onClicked: fillDialog.fill()
            }
        }
    }
}

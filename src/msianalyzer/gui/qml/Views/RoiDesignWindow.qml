import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Dialogs
import "qrc:/Style"

// The ROI-drawing tool: one sample at a time (picked via the combobox),
// rendered with whatever feature/obs/colormap/vmin-vmax Visual Inspection
// already has selected — `controls` below is that *same* HeatmapControlsPanel
// instance, passed in by VisualInspectionSection.qml when it opens this
// window, not a duplicate. Flow: "Add ROI" -> name + color -> "Start
// Drawing" -> click-per-vertex on the (zoomable) canvas -> tapping the
// highlighted first vertex closes the shape and saves it immediately (no
// separate Save step). A separate top-level Window, Loader-spawned from
// Visual Inspection's "Draw ROI" button — same pattern as
// MirrorPlotDetailWindow.qml.
Window {
    id: roiWindow
    objectName: "roiDesignWindow"
    title: "ROI Design" + (roiWindow.selectedSample ? " — " + roiWindow.selectedSample.name : "")
    // Window defaults to visible: true — without this, one popped up
    // immediately whenever the Loader that owns it gets constructed
    // (see VisualInspectionSection.qml's roiDesignWindowLoader), which
    // also throws off the *main* window's own layout measurements under
    // the offscreen QPA platform used for tests (MirrorPlotDetailWindow's
    // same fix).
    visible: false
    width: 1000
    height: 700
    minimumWidth: 700
    minimumHeight: 500

    // A separate top-level Window doesn't inherit Main.qml's palette
    // override — see MirrorPlotDetailWindow.qml's identical block/comment
    // for why. Mirrors it exactly.
    color: palette.window
    palette {
        window: Theme.backgroundColor
        windowText: Theme.textColor
        base: Theme.fieldColor
        alternateBase: Theme.secondaryColor
        text: Theme.textColor
        button: Theme.controlColor
        buttonText: Theme.textColor
        toolTipBase: Theme.controlColor
        toolTipText: Theme.textColor
        placeholderText: Theme.placeholderTextColor
        highlight: Theme.primaryColor
        highlightedText: Theme.highlightedTextColor
        light: Theme.lightColor
        midlight: Theme.midlightColor
        mid: Theme.borderColor
        dark: Theme.darkColor
        shadow: Theme.shadowColor
        disabled.windowText: Theme.disabledTextColor
        disabled.buttonText: Theme.disabledTextColor
        disabled.text: Theme.disabledTextColor
    }

    property var analysis: null
    property var samples: []
    // The HeatmapControlsPanel instance Visual Inspection is already
    // showing — set by VisualInspectionSection.qml's "Draw ROI" handler.
    // Reused as-is (not duplicated) so this window renders with exactly
    // whatever feature/obs column/colormap/vmin-vmax is already selected
    // there; this window never needs its own copy of those controls.
    property var controls: null

    property int selectedSampleIndex: 0
    readonly property var selectedSample: (roiWindow.samples.length > roiWindow.selectedSampleIndex)
                                           ? roiWindow.samples[roiWindow.selectedSampleIndex] : null

    // "idle" (just the "Add ROI" button) -> "naming" (name + color, not
    // drawing yet) -> "drawing" (canvas accepts vertex taps). Closing the
    // polygon saves immediately and returns to "idle" — there is no
    // separate "Save" step once drawing has started.
    property string draftState: "idle"
    property var draftVertices: []
    property string draftName: ""
    property color draftColor: "white"
    readonly property bool canClose: roiWindow.draftVertices.length >= 3
    property string saveError: ""

    property var savedRois: []
    function refreshSavedRois() {
        if (roiWindow.analysis && roiWindow.analysis.analysisDbPath && roiWindow.selectedSample) {
            roiWindow.savedRois = AnalysisBridge.getSampleRois(
                roiWindow.analysis.analysisDbPath, roiWindow.selectedSample.name)
        } else {
            roiWindow.savedRois = []
        }
    }

    function resetDraft() {
        roiWindow.draftState = "idle"
        roiWindow.draftVertices = []
        roiWindow.draftName = ""
        roiWindow.saveError = ""
    }

    function _seedDraftColor() {
        if (roiWindow.analysis && roiWindow.analysis.analysisDbPath) {
            roiWindow.draftColor = AnalysisBridge.nextRoiColor(roiWindow.analysis.analysisDbPath)
        }
    }

    onSelectedSampleIndexChanged: {
        roiWindow.resetDraft()
        roiWindow.refreshSavedRois()
    }
    onVisibleChanged: {
        if (roiWindow.visible) {
            // Grounded in the actual h5ad data every time, not in
            // whatever sample happened to be selected before — resetting
            // to a fixed index (0) unconditionally on every reopen made a
            // just-saved ROI on any *other* sample look "missing" (the
            // window silently switched to a different, likely empty,
            // sample); remembering the previous index instead only
            // worked for as long as this exact window instance survived,
            // which doesn't hold across e.g. navigating away from Visual
            // Inspection and back (that destroys and recreates the whole
            // section, this window included). Scanning every sample's
            // h5ad fresh on each open and defaulting to the first one
            // that actually has a saved ROI works regardless of any of
            // that — it's a fact read off disk, not a remembered value.
            roiWindow.selectedSampleIndex = roiWindow._pickSampleIndexWithRois()
            roiWindow.resetDraft()
            roiWindow.refreshSavedRois()
        }
    }

    // Reads every sample's own `.h5ad` (via the same AnalysisBridge.
    // getSampleRois `refreshSavedRois` uses) to find one that actually
    // carries at least one saved ROI, so opening the window reliably
    // lands on a sample with something to show instead of always
    // defaulting to the first sample in the list regardless of content.
    function _pickSampleIndexWithRois() {
        if (!roiWindow.analysis || !roiWindow.analysis.analysisDbPath) return 0
        for (var i = 0; i < roiWindow.samples.length; i++) {
            var rois = AnalysisBridge.getSampleRois(
                roiWindow.analysis.analysisDbPath, roiWindow.samples[i].name)
            if (rois.length > 0) return i
        }
        return 0
    }

    // "Add ROI" — reveals the name/color step, before any drawing starts.
    function startAddRoi() {
        roiWindow.draftState = "naming"
        roiWindow.draftName = ""
        roiWindow.saveError = ""
        roiWindow._seedDraftColor()
    }
    // "Start Drawing" — commits the name/color and switches the canvas
    // into vertex-placing mode.
    function beginDrawing() {
        if (!roiWindow.draftName) return
        roiWindow.draftState = "drawing"
        roiWindow.draftVertices = []
    }
    function addVertex(col, row) {
        roiWindow.draftVertices = roiWindow.draftVertices.concat([[col, row]])
    }
    function undoLastVertex() {
        roiWindow.draftVertices = roiWindow.draftVertices.slice(0, -1)
    }
    function cancelDraft() {
        roiWindow.resetDraft()
    }
    // Tapping the highlighted first vertex — saves immediately, no
    // separate confirmation step (name/color were already chosen before
    // drawing began).
    function closePolygon() {
        if (!roiWindow.canClose || !roiWindow.draftName || !roiWindow.analysis
                || !roiWindow.selectedSample) {
            return
        }
        var result = AnalysisBridge.saveRoi(
            roiWindow.analysis.analysisDbPath, roiWindow.selectedSample.name,
            roiWindow.draftName, roiWindow.draftColor.toString(), roiWindow.draftVertices)
        if (result.ok) {
            roiWindow.resetDraft()
            roiWindow.refreshSavedRois()
        } else {
            roiWindow.saveError = result.error
        }
    }
    function deleteSavedRoi(name) {
        if (!roiWindow.analysis) return
        AnalysisBridge.deleteRoiEverywhere(
            roiWindow.analysis.analysisDbPath,
            roiWindow.samples.map(function (s) { return s.name }), name)
        roiWindow.refreshSavedRois()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Label { text: "Sample:" }
            ComboBox {
                id: sampleCombo
                objectName: "roiSampleCombo"
                Layout.fillWidth: true
                model: roiWindow.samples.map(function (s) { return s.name })
                currentIndex: roiWindow.selectedSampleIndex
                onActivated: (index) => roiWindow.selectedSampleIndex = index
                HoverHandler {
                    objectName: "roiSampleComboHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Item { Layout.fillWidth: true }
            Label { text: "Zoom:" }
            Button {
                objectName: "roiZoomOutButton"
                text: "−"
                implicitWidth: 32
                onClicked: drawingCanvas.zoom /= 1.3
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            Button {
                objectName: "roiZoomResetButton"
                text: "Reset"
                onClicked: drawingCanvas.zoom = 1.0
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            Button {
                objectName: "roiZoomInButton"
                text: "+"
                implicitWidth: 32
                onClicked: drawingCanvas.zoom *= 1.3
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            RoiDrawingCanvas {
                id: drawingCanvas
                objectName: "roiDrawingCanvas"
                Layout.fillWidth: true
                Layout.fillHeight: true
                source: (roiWindow.selectedSample && roiWindow.controls)
                        ? roiWindow.controls.tileSource(roiWindow.selectedSample.name) : ""
                drawingEnabled: roiWindow.draftState === "drawing"
                savedRois: roiWindow.savedRois
                draftVertices: roiWindow.draftVertices
                draftColor: roiWindow.draftColor
                onVertexRequested: (col, row) => roiWindow.addVertex(col, row)
                onClosePolygonRequested: roiWindow.closePolygon()
            }

            ColumnLayout {
                id: roiListPanel
                objectName: "roiListPanel"
                Layout.preferredWidth: 240
                Layout.minimumWidth: 240
                // A hard ceiling, not just preferredWidth: some content
                // inside this panel otherwise dragged its effective width
                // to ~650px regardless of preferredWidth/minimumWidth
                // hints (observed directly), starving the drawing canvas
                // next to it down to a couple of pixels.
                Layout.maximumWidth: 240
                Layout.fillHeight: true

                Label { text: "ROIs on this sample"; font.bold: true }
                Repeater {
                    id: roiListRepeater
                    objectName: "roiListRepeater"
                    model: roiWindow.savedRois

                    delegate: RowLayout {
                        objectName: "roiListRow_" + modelData.name
                        Rectangle {
                            width: 14; height: 14; radius: 2
                            color: modelData.color
                            border.color: palette.mid
                        }
                        Label { text: modelData.name; Layout.fillWidth: true }
                        ToolButton {
                            objectName: "roiDeleteButton_" + modelData.name
                            text: "✕"
                            onClicked: roiWindow.deleteSavedRoi(modelData.name)
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                }

                Item { Layout.preferredHeight: 8 }

                // --- "idle": just the entry point ---
                Button {
                    objectName: "roiAddButton"
                    text: "Add ROI"
                    Layout.fillWidth: true
                    visible: roiWindow.draftState === "idle"
                    onClicked: roiWindow.startAddRoi()
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }

                // --- "naming": name + color, before any drawing ---
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: roiWindow.draftState === "naming"

                    Label { text: "New ROI"; font.bold: true }
                    TextField {
                        id: roiNameField
                        objectName: "roiNameField"
                        Layout.fillWidth: true
                        placeholderText: "Name"
                        text: roiWindow.draftName
                        onTextEdited: roiWindow.draftName = text
                    }
                    RowLayout {
                        Label { text: "Color:" }
                        Rectangle {
                            objectName: "roiColorSwatch"
                            width: 20; height: 20; radius: 4
                            color: roiWindow.draftColor
                            border.color: palette.mid
                            TapHandler {
                                onTapped: colorDialog.open()
                            }
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                    ColorDialog {
                        id: colorDialog
                        objectName: "roiColorDialog"
                        selectedColor: roiWindow.draftColor
                        onAccepted: roiWindow.draftColor = selectedColor
                    }
                    RowLayout {
                        Button {
                            objectName: "roiStartDrawingButton"
                            text: "Start Drawing"
                            enabled: roiWindow.draftName.length > 0
                            onClicked: roiWindow.beginDrawing()
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                        Button {
                            objectName: "roiCancelNamingButton"
                            text: "Cancel"
                            onClicked: roiWindow.cancelDraft()
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                }

                // --- "drawing": vertex count + undo/cancel ---
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: roiWindow.draftState === "drawing"

                    Label {
                        text: "Drawing “" + roiWindow.draftName + "” ("
                              + roiWindow.draftVertices.length + " vertices)"
                        font.bold: true
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                    }
                    Label {
                        text: "Click on the image to add a vertex; click the "
                              + "highlighted first vertex to close the shape "
                              + "and save it."
                        color: Theme.mutedTextColor
                        font.pixelSize: Theme.captionPixelSize
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                        // A wrapping Label's own implicitWidth is its
                        // *unwrapped* one-line width — capped so it can't
                        // dictate this panel's own width (see
                        // roiListPanel's Layout.maximumWidth comment
                        // above); Layout.fillWidth still stretches it to
                        // whatever width the panel resolves to.
                        Layout.preferredWidth: 1
                    }
                    RowLayout {
                        Button {
                            objectName: "roiUndoButton"
                            text: "Undo"
                            enabled: roiWindow.draftVertices.length > 0
                            onClicked: roiWindow.undoLastVertex()
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                        Button {
                            objectName: "roiCancelButton"
                            text: "Cancel"
                            onClicked: roiWindow.cancelDraft()
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                }

                Label {
                    objectName: "roiSaveErrorLabel"
                    visible: roiWindow.saveError.length > 0
                    text: roiWindow.saveError
                    color: Theme.errorColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                }

                Item { Layout.fillHeight: true }
            }
        }
    }
}

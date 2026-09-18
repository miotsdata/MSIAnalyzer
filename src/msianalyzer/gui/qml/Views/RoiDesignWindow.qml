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

    // Which surface vertices are placed against — "grid" (the MSI
    // heatmap raster, the original/default behavior) or "he" (that
    // sample's H&E image, see ADR 47 / core/registration/). Reset to
    // "grid" on every sample switch: a different sample's registration
    // (or lack of one) shouldn't silently carry over.
    property string drawSurface: "grid"
    property var roiRegistrationInfo: ({hasFit: false, matrix: null})
    // Same overlay show/opacity controls as CoregistrationWindow, for
    // the same reason: how much H&E tissue morphology vs. warped
    // metabolite signal to see while drawing on the H&E surface.
    property bool showOverlay: true
    property real overlayOpacity: 0.6
    function refreshRoiRegistrationInfo() {
        var sample = (roiWindow.samples.length > roiWindow.selectedSampleIndex)
                     ? roiWindow.samples[roiWindow.selectedSampleIndex] : null
        if (roiWindow.analysis && roiWindow.analysis.analysisDbPath && sample) {
            roiWindow.roiRegistrationInfo = AnalysisBridge.getRegistrationInfo(
                roiWindow.analysis.analysisDbPath, sample.name)
        } else {
            roiWindow.roiRegistrationInfo = {hasFit: false, matrix: null}
        }
        roiWindow.drawSurface = "grid"
    }
    // The he_overlay target tail for the selected sample, or "" if
    // nothing to show yet — same guard as CoregistrationWindow.overlaySource.
    function heOverlaySource() {
        if (!roiWindow.selectedSample || !roiWindow.controls
                || !roiWindow.roiRegistrationInfo.hasFit) return ""
        var target = roiWindow.controls.tileTarget(roiWindow.selectedSample.name)
        return target ? "image://he_overlay/" + target : ""
    }

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
        // Reads samples[selectedSampleIndex] directly rather than going
        // through the `selectedSample` binding above — when this runs from
        // onSelectedSampleIndexChanged, selectedSampleIndex is guaranteed
        // to already hold the new value (it's the property that just
        // changed), whereas relying on `selectedSample` here previously
        // raced against that binding's own re-evaluation and could still
        // read the *previous* sample's rows (confirmed: switching samples
        // kept showing the old sample's ROI list).
        var sample = (roiWindow.samples.length > roiWindow.selectedSampleIndex)
                     ? roiWindow.samples[roiWindow.selectedSampleIndex] : null
        if (roiWindow.analysis && roiWindow.analysis.analysisDbPath && sample) {
            roiWindow.savedRois = AnalysisBridge.getSampleRois(
                roiWindow.analysis.analysisDbPath, sample.name)
        } else {
            roiWindow.savedRois = []
        }
    }

    // The analysis-wide name/color catalog (AnalysisBridge.getRois) —
    // every ROI ever registered, on any sample. Used only to compute
    // `otherRois` below; `savedRois` above (read from this sample's own
    // h5ad, not the catalog) remains the source of truth for what's
    // actually drawn *on this sample*.
    property var catalogRois: []
    // Every call site calls this *after* refreshSavedRois(), so
    // recomputing otherRois here (rather than via its own reactive
    // binding — see otherRois' own comment for why that didn't work)
    // always sees both freshly updated.
    function refreshCatalogRois() {
        if (roiWindow.analysis && roiWindow.analysis.analysisDbPath) {
            roiWindow.catalogRois = AnalysisBridge.getRois(roiWindow.analysis.analysisDbPath)
        } else {
            roiWindow.catalogRois = []
        }
        roiWindow._recomputeOtherRois()
    }
    // Catalog entries that this sample doesn't have yet — "Other ROIs in
    // analyses" below lists these with a one-click "Draw for this
    // sample" action (same name/color, straight into drawing mode) so a
    // named ROI drawn on one sample is easy to add to another instead of
    // looking like it's simply missing here.
    // A plain property, explicitly recomputed by `_recomputeOtherRois()`
    // after every `refreshSavedRois()`/`refreshCatalogRois()` pair, rather
    // than a computed `{...}`-block binding — that reactive form did not
    // reliably re-evaluate here when `savedRois`/`catalogRois` changed
    // (confirmed directly: instrumented and saw it evaluate exactly once,
    // before either had real data). Matches this file's existing style
    // for savedRois/catalogRois themselves, which are also plain
    // properties updated by explicit function calls, not bindings.
    property var otherRois: []
    function _recomputeOtherRois() {
        var savedNames = roiWindow.savedRois.map(function (r) { return r.name })
        roiWindow.otherRois = roiWindow.catalogRois.filter(function (r) {
            return savedNames.indexOf(r.name) === -1
        })
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
        roiWindow.refreshCatalogRois()
        roiWindow.refreshRoiRegistrationInfo()
    }
    onVisibleChanged: {
        if (roiWindow.visible) {
            // Always the first sample — which named ROIs exist elsewhere
            // in the analysis but not on the sample currently shown is
            // now surfaced directly ("Other ROIs in analyses" below,
            // with a one-click way to add one here), so there's no need
            // to guess which sample to open on.
            roiWindow.selectedSampleIndex = 0
            roiWindow.resetDraft()
            roiWindow.refreshSavedRois()
            roiWindow.refreshCatalogRois()
            roiWindow.refreshRoiRegistrationInfo()
        }
    }

    // Single entry point for "Draw ROI" — one window reused across
    // clicks rather than a new one per open. `onVisibleChanged` above
    // already covers going from closed to open, but a *second* click
    // while the window is already open never flips `visible`, so it
    // would otherwise sit on whatever sample/draft state was left over
    // from before — this covers that case explicitly instead.
    function openFor(analysisModel, sampleList, controlsPanel) {
        var wasVisible = roiWindow.visible
        roiWindow.analysis = analysisModel
        roiWindow.samples = sampleList
        roiWindow.controls = controlsPanel
        roiWindow.visible = true
        roiWindow.raise()
        roiWindow.requestActivate()
        if (wasVisible) {
            roiWindow.selectedSampleIndex = 0
            roiWindow.resetDraft()
            roiWindow.refreshSavedRois()
            roiWindow.refreshCatalogRois()
            roiWindow.refreshRoiRegistrationInfo()
        }
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
    // "Draw for this sample" on an "Other ROIs in analyses" row — reuses
    // an existing catalog name/color and jumps straight into drawing,
    // skipping the naming step entirely (the name/color are already
    // fixed; saveRoi's own "existing name keeps its registered color"
    // rule means passing this color back is just for display consistency
    // while drawing, not a re-registration).
    function startDrawingExisting(name, colorHex) {
        roiWindow.draftState = "drawing"
        roiWindow.draftName = name
        roiWindow.draftColor = colorHex
        roiWindow.draftVertices = []
        roiWindow.saveError = ""
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
            roiWindow.refreshCatalogRois()
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
        roiWindow.refreshCatalogRois()
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
            CheckBox {
                id: drawOnHeCheckBox
                objectName: "drawOnHeCheckBox"
                text: "Draw on H&E image"
                visible: roiWindow.roiRegistrationInfo.hasFit
                checked: roiWindow.drawSurface === "he"
                onToggled: roiWindow.drawSurface = checked ? "he" : "grid"
                HoverHandler { cursorShape: Qt.PointingHandCursor }
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
            visible: roiWindow.drawSurface === "he"
            CheckBox {
                id: roiShowOverlayCheckBox
                objectName: "roiShowOverlayCheckBox"
                text: "Overlay"
                checked: roiWindow.showOverlay
                onToggled: roiWindow.showOverlay = checked
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            Slider {
                id: roiOverlayOpacitySlider
                objectName: "roiOverlayOpacitySlider"
                Layout.fillWidth: true
                enabled: roiWindow.showOverlay
                from: 0.0
                to: 1.0
                value: roiWindow.overlayOpacity
                onMoved: roiWindow.overlayOpacity = value
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
                        ? (roiWindow.drawSurface === "he"
                           ? "image://he_image/" + roiWindow.selectedSample.name
                           : roiWindow.controls.tileSource(roiWindow.selectedSample.name))
                        : ""
                heMode: roiWindow.drawSurface === "he"
                heToGridMatrix: roiWindow.roiRegistrationInfo.matrix
                heOverlaySource: roiWindow.heOverlaySource()
                showOverlay: roiWindow.showOverlay
                overlayOpacity: roiWindow.overlayOpacity
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

                // Catalog entries (name + color, from the analysis-wide
                // `rois` table) not yet drawn on *this* sample — a named
                // ROI drawn elsewhere in the analysis shows up here
                // instead of just looking absent, with a one-click way
                // to add it to this sample under the same name/color.
                // Hidden once naming/drawing starts, same as "Add ROI".
                ColumnLayout {
                    id: otherRoisSection
                    objectName: "otherRoisSection"
                    Layout.fillWidth: true
                    visible: roiWindow.draftState === "idle" && roiWindow.otherRois.length > 0

                    Label { text: "Other ROIs in analyses"; font.bold: true }
                    Repeater {
                        id: otherRoisRepeater
                        objectName: "otherRoisRepeater"
                        model: roiWindow.otherRois

                        delegate: ColumnLayout {
                            objectName: "otherRoiRow_" + modelData.name
                            Layout.fillWidth: true
                            spacing: 2

                            RowLayout {
                                Layout.fillWidth: true
                                Rectangle {
                                    width: 14; height: 14; radius: 2
                                    color: modelData.color
                                    border.color: palette.mid
                                }
                                Label { text: modelData.name; Layout.fillWidth: true }
                            }
                            Button {
                                objectName: "drawForSampleButton_" + modelData.name
                                text: "Draw for this sample"
                                Layout.fillWidth: true
                                onClicked: roiWindow.startDrawingExisting(modelData.name, modelData.color)
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                            }
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

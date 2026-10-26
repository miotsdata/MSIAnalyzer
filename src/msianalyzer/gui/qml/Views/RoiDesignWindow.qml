import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Dialogs
import "qrc:/Style"
import "qrc:/Utils/ScaleBar.js" as ScaleBar

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
    // Which samples are coregistered ("· H&E ✓" in the sample combo).
    property var registrationMarks: ({})
    // Scale bar settings (ADR 53), bound by VisualInspectionSection.
    property var scaleBar: ({})
    function refreshRegistrationMarks() {
        roiWindow.registrationMarks = (roiWindow.analysis && roiWindow.analysis.analysisDbPath)
            ? AnalysisBridge.getRegistrationMarks(roiWindow.analysis.analysisDbPath,
                  roiWindow.samples.map(function (s) { return s.name }))
            : {}
    }
    onSamplesChanged: roiWindow.refreshRegistrationMarks()
    Connections {
        target: AnalysisBridge
        function onRegistrationChanged() { roiWindow.refreshRegistrationMarks() }
    }
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

    // Fired right after a successful save (below, in closePolygon()) —
    // lets an owner that needs to react to a *specific* new ROI do so
    // (e.g. CalibrationSection.qml's "immediately ask for this standard's
    // amount" popup) without polling `roiWindow.savedRois`. Every opener
    // still gets this signal regardless of why it opened the window; one
    // that doesn't care (e.g. Visual Inspection's plain "Draw ROI") just
    // never connects a handler to it.
    signal roiSaved(string sampleName, string roiName)

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
    // Set when an ROI saved fine but couldn't be written back into an
    // imported dataset's source .h5ad (ADR 49), e.g. because the file was
    // moved or is locked by a notebook. Unlike saveError, the save
    // itself succeeded, so this survives resetDraft() until the next
    // save attempt.
    property string saveWarning: ""

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
        roiWindow.pasteMode = false
    }

    // --- Select / move / copy-paste saved ROIs (ADR 58) -------------------
    // Only in "idle". Every change is an ordinary saveRoi of the same or a
    // new name (saveRoi overwrites a name already on this sample), so
    // masks, merged.h5ad and imported-dataset write-back all follow, and
    // `roiSaved` fires as for a drawn ROI (calibration re-measures it).
    property string selectedRoiName: ""
    // {sample, name, color, vertices}, window-local, survives sample
    // switches so an ROI can be copied onto another sample.
    property var roiClipboard: null
    // Geometry before each move on this sample, newest last: every Ctrl+Z
    // restores one. Cleared on a sample switch.
    property var moveHistory: []
    // The naming step is reused for paste: "Paste" saves the clipboard
    // geometry under the typed name instead of starting a drawing.
    property bool pasteMode: false

    function _savedRoi(name) {
        for (var i = 0; i < roiWindow.savedRois.length; i++) {
            if (roiWindow.savedRois[i].name === name) return roiWindow.savedRois[i]
        }
        return null
    }
    function _nameTakenHere(name) {
        if (!roiWindow._savedRoi(name)) return false
        roiWindow.saveError = "“" + name + "” is already on this sample."
        return true
    }
    function _saveGeometry(name, color, vertices) {
        var result = AnalysisBridge.saveRoi(
            roiWindow.analysis.analysisDbPath, roiWindow.selectedSample.name,
            name, color, vertices)
        roiWindow.saveWarning = result.warning ? result.warning : ""
        roiWindow.saveError = result.ok ? "" : result.error
        if (result.ok) {
            roiWindow.refreshSavedRois()
            roiWindow.refreshCatalogRois()
            roiWindow.roiSaved(roiWindow.selectedSample.name, name)
        }
        return result.ok
    }
    function selectRoi(name) {
        roiWindow.selectedRoiName = name
    }
    function moveSelectedRoi(dCol, dRow) {
        var roi = roiWindow._savedRoi(roiWindow.selectedRoiName)
        if (!roi || (dCol === 0 && dRow === 0)) return
        var moved = roi.vertices.map(function (v) { return [v[0] + dCol, v[1] + dRow] })
        if (roiWindow._saveGeometry(roi.name, roi.color, moved)) {
            roiWindow.moveHistory = roiWindow.moveHistory.concat(
                [{name: roi.name, color: roi.color, vertices: roi.vertices}])
        }
    }
    function undoLastMove() {
        var history = roiWindow.moveHistory
        if (history.length === 0) return
        var m = history[history.length - 1]
        roiWindow.moveHistory = history.slice(0, -1)
        // An ROI deleted since then has nothing left to restore.
        if (!roiWindow._savedRoi(m.name)) return
        if (roiWindow._saveGeometry(m.name, m.color, m.vertices)) {
            roiWindow.selectedRoiName = m.name
        }
    }
    function copySelectedRoi() {
        var roi = roiWindow._savedRoi(roiWindow.selectedRoiName)
        if (!roi) return
        roiWindow.roiClipboard = {
            sample: roiWindow.selectedSample.name,
            name: roi.name, color: roi.color, vertices: roi.vertices,
        }
    }
    // Opens the naming step for the clipboard ROI: the same name when
    // this sample doesn't have it yet (the same ROI on another sample),
    // else "<name>_copy" as a new ROI with a fresh color.
    function startPaste() {
        if (!roiWindow.roiClipboard) return
        var clip = roiWindow.roiClipboard
        roiWindow.pasteMode = true
        roiWindow.draftState = "naming"
        roiWindow.saveError = ""
        if (roiWindow._savedRoi(clip.name)) {
            roiWindow.draftName = clip.name + "_copy"
            roiWindow._seedDraftColor()
        } else {
            roiWindow.draftName = clip.name
            roiWindow.draftColor = clip.color
        }
    }
    function commitPaste() {
        var clip = roiWindow.roiClipboard
        var name = roiWindow.draftName
        if (!clip || !name || roiWindow._nameTakenHere(name)) return
        // Offset on the sample it was copied from, so the copy isn't
        // hidden exactly under the original; same place elsewhere.
        var offset = clip.sample === roiWindow.selectedSample.name ? 5 : 0
        var vertices = clip.vertices.map(function (v) { return [v[0] + offset, v[1] + offset] })
        if (roiWindow._saveGeometry(name, roiWindow.draftColor.toString(), vertices)) {
            roiWindow.resetDraft()
            roiWindow.selectedRoiName = name
        }
    }

    readonly property bool _canEditSelection: roiWindow.draftState === "idle" && roiWindow.selectedRoiName !== ""
    Shortcut { sequence: "Left"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(-1, 0) }
    Shortcut { sequence: "Right"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(1, 0) }
    Shortcut { sequence: "Up"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(0, -1) }
    Shortcut { sequence: "Down"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(0, 1) }
    Shortcut { sequence: "Shift+Left"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(-10, 0) }
    Shortcut { sequence: "Shift+Right"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(10, 0) }
    Shortcut { sequence: "Shift+Up"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(0, -10) }
    Shortcut { sequence: "Shift+Down"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.moveSelectedRoi(0, 10) }
    Shortcut { sequences: [StandardKey.Copy]; enabled: roiWindow._canEditSelection; onActivated: roiWindow.copySelectedRoi() }
    Shortcut {
        sequences: [StandardKey.Paste]
        enabled: roiWindow.draftState === "idle" && roiWindow.roiClipboard !== null
        onActivated: roiWindow.startPaste()
    }
    Shortcut {
        sequences: [StandardKey.Undo]
        enabled: roiWindow.draftState === "idle" && roiWindow.moveHistory.length > 0
        onActivated: roiWindow.undoLastMove()
    }
    Shortcut { sequence: "Escape"; enabled: roiWindow._canEditSelection; onActivated: roiWindow.selectedRoiName = "" }

    function _seedDraftColor() {
        if (roiWindow.analysis && roiWindow.analysis.analysisDbPath) {
            roiWindow.draftColor = AnalysisBridge.nextRoiColor(roiWindow.analysis.analysisDbPath)
        }
    }

    onSelectedSampleIndexChanged: {
        // Registration first: a bridge call below that throws must not
        // also hide "Draw on H&E image".
        roiWindow.refreshRoiRegistrationInfo()
        roiWindow.resetDraft()
        roiWindow.selectedRoiName = ""
        roiWindow.moveHistory = []
        roiWindow.refreshSavedRois()
        roiWindow.refreshCatalogRois()
    }
    onVisibleChanged: {
        if (roiWindow.visible) {
            // Always the first sample — which named ROIs exist elsewhere
            // in the analysis but not on the sample currently shown is
            // now surfaced directly ("Other ROIs in analyses" below,
            // with a one-click way to add one here), so there's no need
            // to guess which sample to open on.
            roiWindow.selectedSampleIndex = 0
            roiWindow.refreshRoiRegistrationInfo()
            roiWindow.resetDraft()
            roiWindow.refreshSavedRois()
            roiWindow.refreshCatalogRois()
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
            roiWindow.refreshRoiRegistrationInfo()
            roiWindow.resetDraft()
            roiWindow.refreshSavedRois()
            roiWindow.refreshCatalogRois()
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
        // saveRoi would silently overwrite an ROI of the same name here.
        if (!roiWindow.draftName || roiWindow._nameTakenHere(roiWindow.draftName)) return
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
        roiWindow.saveWarning = result.warning ? result.warning : ""
        if (result.ok) {
            var savedSampleName = roiWindow.selectedSample.name
            var savedRoiName = roiWindow.draftName
            roiWindow.resetDraft()
            roiWindow.refreshSavedRois()
            roiWindow.refreshCatalogRois()
            roiWindow.roiSaved(savedSampleName, savedRoiName)
        } else {
            roiWindow.saveError = result.error
        }
    }
    function deleteSavedRoi(name) {
        if (!roiWindow.analysis) return
        AnalysisBridge.deleteRoiEverywhere(
            roiWindow.analysis.analysisDbPath,
            roiWindow.samples.map(function (s) { return s.name }), name)
        if (roiWindow.selectedRoiName === name) roiWindow.selectedRoiName = ""
        roiWindow.moveHistory = roiWindow.moveHistory.filter(function (m) { return m.name !== name })
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
                // "· H&E ✓" marks the coregistered samples.
                model: roiWindow.samples.map(function (s) { return s.name + (roiWindow.registrationMarks[s.name] || "") })
                currentIndex: roiWindow.selectedSampleIndex
                onActivated: (index) => roiWindow.selectedSampleIndex = index
                HoverHandler {
                    objectName: "roiSampleComboHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Label {
                objectName: "roiSampleSizeLabel"
                // The H&E surface shows the H&E image's size, not the sample's.
                visible: roiWindow.drawSurface === "grid"
                text: ScaleBar.sizeText(drawingCanvas.imageSize)
                color: Theme.mutedTextColor
            }
            AppCheckBox {
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
            AppCheckBox {
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
                // H&E pixels aren't MSI pixels: no bar on that surface.
                scaleBarPixelUm: (roiWindow.drawSurface === "grid" && roiWindow.selectedSample)
                                 ? ScaleBar.pixelUm(roiWindow.scaleBar, roiWindow.selectedSample.name) : 0
                scaleBarUm: roiWindow.scaleBar.bar_um || 0
                scaleBarColor: roiWindow.scaleBar.color || "white"
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
                selectionEnabled: roiWindow.draftState === "idle"
                selectedRoiName: roiWindow.selectedRoiName
                onRoiTapped: (name) => roiWindow.selectRoi(name)
                onMoveRequested: (dCol, dRow) => roiWindow.moveSelectedRoi(dCol, dRow)
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
                        Label {
                            text: modelData.name
                            Layout.fillWidth: true
                            font.bold: modelData.name === roiWindow.selectedRoiName
                            color: modelData.name === roiWindow.selectedRoiName ? Theme.primaryColor : palette.windowText
                            TapHandler {
                                enabled: roiWindow.draftState === "idle"
                                onTapped: roiWindow.selectRoi(modelData.name)
                            }
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                        ToolButton {
                            objectName: "roiDeleteButton_" + modelData.name
                            text: "✕"
                            onClicked: roiWindow.deleteSavedRoi(modelData.name)
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: roiWindow.draftState === "idle"
                             && (roiWindow.savedRois.length > 0 || roiWindow.roiClipboard !== null)
                    Button {
                        objectName: "roiCopyButton"
                        text: "Copy"
                        Layout.fillWidth: true
                        enabled: roiWindow._canEditSelection
                        onClicked: roiWindow.copySelectedRoi()
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    Button {
                        objectName: "roiPasteButton"
                        text: "Paste"
                        Layout.fillWidth: true
                        enabled: roiWindow.roiClipboard !== null
                        onClicked: roiWindow.startPaste()
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
                Label {
                    visible: roiWindow._canEditSelection
                    text: "Drag the selected ROI or use the arrow keys (Shift = 10 px) "
                          + "to move it; Ctrl+Z undoes moves one at a time."
                    color: Theme.mutedTextColor
                    font.pixelSize: Theme.captionPixelSize
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
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

                    Label { text: roiWindow.pasteMode ? "Paste ROI" : "New ROI"; font.bold: true }
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
                            text: roiWindow.pasteMode ? "Paste" : "Start Drawing"
                            enabled: roiWindow.draftName.length > 0
                            onClicked: roiWindow.pasteMode ? roiWindow.commitPaste() : roiWindow.beginDrawing()
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
                    objectName: "roiSaveWarningLabel"
                    visible: roiWindow.saveWarning.length > 0
                    text: roiWindow.saveWarning
                    color: Theme.warningColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
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

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

// The targeted-analysis Calibration workspace — AnalysisPage.qml's sole
// page for a `targeted` analysis (see that file's own `isTargeted`
// comment: no other tab applies to this analysis type at all).
//
// Three-step interactive workflow, entirely GUI-driven (no config file):
//   1. Draw calibration standards ("Add Standard"), entering each spot's
//      known amount right away via a popup.
//   2. Fit the curve, review its stats/chart.
//   3. Draw ROIs to quantify ("Add Sample to Quantify"), interpolated live
//      once a curve exists.
// Both "Add ROI" actions reuse the existing RoiDesignWindow — the same
// window Visual Inspection's own "Draw ROI" button opens — rendered
// against whichever analyte `controls` (this section's own, hidden
// HeatmapControlsPanel) currently has selected. Every analyte the run was
// configured to quantify (`NormalizationConfig.analyte_mzs`) was already
// baked into every sample's `.h5ad` at run time and registered as a
// browsable feature — so this is `controls`' ordinary feature picker, the
// exact same mechanism Visual Inspection uses, not a special on-demand
// extraction (that used to make picking an analyte painfully slow).
Item {
    id: calibrationSection
    objectName: "calibrationSection"

    property var analysis
    property var samples: (analysis && analysis.analysisDbPath)
                           ? AnalysisBridge.getSamples(analysis.analysisDbPath) : []

    // Every calibration-specific read (see AnalysisBridge's own
    // calibration slots) is small and synchronous — the one real
    // background fetch this section waits on is `controls`' own feature
    // list (the analyte picker's contents, requestFeatureList/
    // featureListReady). Read by AnalysisPage.qml's currentSectionReady,
    // same convention as every other section's own
    // `rowsLoading`/`plotLoading`.
    property bool rowsLoading: controls.featuresLoading

    // The analyte m/z this whole calibration is built around — a plain
    // alias of `controls.selectedFeature.mz` (that panel's ordinary
    // feature picker, populated from this run's configured
    // `analyte_mzs`), not a separately-tracked value: v1 is
    // single-analyte-per-analysis, so there's only ever one meaningful
    // "currently selected" m/z here even if the run quantified several.
    readonly property real analyteMz: controls.selectedFeature ? controls.selectedFeature.mz : NaN
    readonly property bool hasAnalyte: !isNaN(calibrationSection.analyteMz)

    property var standardsRows: []
    property var unknownsRows: []
    property var currentCurve: ({})
    readonly property bool hasCurve: Object.keys(calibrationSection.currentCurve).length > 0
    property var plotData: ({})

    // Pixel size/unit + the standards' amount unit — entered once (top of
    // Step 1) and reused for every "Add Standard"/"Add Sample to
    // Quantify" call in this session; not itself synced to the DB until
    // the first standard/unknown is actually saved (each saved row keeps
    // its own copy regardless — see analysis_db's calibration_points/
    // calibration_unknowns schema).
    property real pixelSize: 0.0
    property string pixelSizeUnit: "mm"
    property string amountUnit: "pmol"
    property string fitType: "linear"
    property string weighting: "1/x2"

    // Independent from `pixelSizeUnit` on purpose — a realistic MSI raster
    // pitch is naturally described in µm (e.g. "100 µm"), but that same
    // µm² area denominator makes concentration values tiny and awkward to
    // read (reported directly: "why is the concentration so low"). This
    // lets the pixel size stay entered the natural way while area/
    // concentration are computed and shown in whichever square unit is
    // more readable — converted client-side (`_pixelSizeInAreaUnit`)
    // before ever reaching AnalysisBridge, so the stored `pixel_size`/
    // `pixel_size_unit` on each standard/unknown already reflect the
    // chosen area unit (no separate "input unit" column needed there).
    property string concentrationAreaUnit: "mm"
    readonly property string concentrationUnitLabel: calibrationSection.amountUnit + "/"
                                                      + calibrationSection.concentrationAreaUnit + "²"

    // `pixelSize`, converted from `pixelSizeUnit` into
    // `concentrationAreaUnit`'s own length scale (1 mm = 1000 µm) — what
    // every "Add Standard"/"Add Sample to Quantify" call actually sends
    // as `pixel_size`/`pixel_size_unit`.
    function _pixelSizeInAreaUnit() {
        if (calibrationSection.pixelSizeUnit === calibrationSection.concentrationAreaUnit) {
            return calibrationSection.pixelSize
        }
        return calibrationSection.pixelSizeUnit === "um"
            ? calibrationSection.pixelSize / 1000
            : calibrationSection.pixelSize * 1000
    }

    // How many `toUnit`-squared fit in one `fromUnit`-squared — mirrors
    // `core.calibration.units.area_conversion_factor` exactly (see that
    // function's own docstring for the full reasoning), duplicated here
    // in JS since QML can't call a Python function for a per-row table
    // label. Used to REDISPLAY an already-computed area/concentration/
    // curve in whichever unit `concentrationAreaUnitCombo` currently has
    // selected, without touching what's actually stored (each row keeps
    // its own `pixel_size_unit`, the unit it was originally computed in).
    function _areaConversionFactor(fromUnit, toUnit) {
        if (fromUnit === toUnit) return 1.0
        return fromUnit === "mm" ? 1e6 : 1e-6
    }

    // "pmol/mm2" -> "mm" — the area unit a fitted curve's own
    // slope/intercept/LOD/LOQ are actually expressed in, parsed back out
    // of currentCurve.concentration_unit (there's no separate column for
    // it). Falls back to concentrationAreaUnit itself (a no-op factor)
    // if there's no curve yet.
    function _curveAreaUnit() {
        var unit = calibrationSection.currentCurve.concentration_unit
        return unit ? unit.split("/")[1].replace("2", "") : calibrationSection.concentrationAreaUnit
    }

    // A FIFO of standard ROIs drawn but not yet assigned an amount —
    // `pendingStandardSample`/`pendingStandardRoi` is only ever "the one
    // currently shown in the dialog"; anything drawn *while* that dialog
    // is still open (drawing several ROIs back to back before answering
    // any of them, a completely normal way to use this window) queues up
    // here instead of silently overwriting the in-flight one. Fixed a
    // real reported bug: drawing several standards in one RoiDesignWindow
    // session only ever asked for one amount — every `roiSaved` before
    // this fix unconditionally reassigned `pendingStandardRoi` and
    // reopened the (already-open, so effectively a no-op) dialog, so only
    // the *last*-drawn ROI's name/sample survived to actually be saved
    // when the user finally answered the popup.
    property var pendingStandardsQueue: []
    property string pendingStandardSample: ""
    property string pendingStandardRoi: ""

    // Pops the next queued ROI into the dialog, if one is waiting and the
    // dialog isn't already showing one — called right after a new ROI is
    // queued, and again from the dialog's own `onClosed` (covers both
    // "Save" and "Cancel"/closing it: a skipped standard shouldn't block
    // the rest of the queue).
    function _showNextPendingStandard() {
        if (standardConcentrationDialog.visible) return
        if (calibrationSection.pendingStandardsQueue.length === 0) return
        var next = calibrationSection.pendingStandardsQueue[0]
        calibrationSection.pendingStandardsQueue = calibrationSection.pendingStandardsQueue.slice(1)
        calibrationSection.pendingStandardSample = next.sampleName
        calibrationSection.pendingStandardRoi = next.roiName
        standardAmountField.text = ""
        standardConcentrationDialog.errorText = ""
        standardConcentrationDialog.open()
    }

    function refreshStandards() {
        calibrationSection.standardsRows = (analysis && analysis.analysisDbPath)
            ? AnalysisBridge.getCalibrationPoints(analysis.analysisDbPath) : []
    }
    function refreshUnknowns() {
        calibrationSection.unknownsRows = (analysis && analysis.analysisDbPath)
            ? AnalysisBridge.getCalibrationUnknowns(analysis.analysisDbPath) : []
    }
    function refreshCurve() {
        calibrationSection.currentCurve = (analysis && analysis.analysisDbPath)
            ? AnalysisBridge.getCalibrationCurve(analysis.analysisDbPath) : ({})
        // `concentrationAreaUnit` redisplays an already-fitted curve in
        // whichever unit the combo currently has selected — a live,
        // non-destructive choice (see AnalysisBridge.getCalibrationPlotData's
        // own docstring), so this needs re-running whenever that changes,
        // not just when the underlying data does (see onConcentrationAreaUnitChanged).
        calibrationSection.plotData = (analysis && analysis.analysisDbPath && calibrationSection.hasCurve)
            ? AnalysisBridge.getCalibrationPlotData(
                  analysis.analysisDbPath, calibrationSection.concentrationAreaUnit)
            : ({})
    }
    onConcentrationAreaUnitChanged: calibrationSection.refreshCurve()
    function refreshAll() {
        calibrationSection.refreshStandards()
        calibrationSection.refreshUnknowns()
        calibrationSection.refreshCurve()
    }

    // Restores the analyte selection on reopening an analysis that already
    // has standards on record (AnalysisBridge.getCalibrationAnalyteMz), so
    // the user doesn't have to reselect it from the picker every visit.
    // `-1` means "never recorded yet" (a brand new calibration). `controls`
    // fetches its feature list asynchronously (featureListReady), so this
    // is also retried from the onFeaturesChanged handler below — the very
    // first call here usually runs before that list has actually arrived.
    function restoreAnalyteMz() {
        if (!analysis || !analysis.analysisDbPath) return
        var mz = AnalysisBridge.getCalibrationAnalyteMz(analysis.analysisDbPath)
        if (mz <= 0) return
        for (var i = 0; i < controls.sortedFeatures.length; i++) {
            if (Math.abs(controls.sortedFeatures[i].mz - mz) < 1e-6) {
                controls.selectedFeatureIndex = i
                return
            }
        }
    }

    Connections {
        target: controls
        function onFeaturesChanged() { calibrationSection.restoreAnalyteMz() }
    }

    // Defaults pixelSize/pixelSizeUnit from the raster XML's own
    // <rasterConfig> ("the size of the pixels are in the xml file") when
    // the acquisition recorded one (AnalysisBridge.getSamplePixelSize) —
    // still a plain, freely-editable default, never a locked-in value;
    // an XML with no <rasterConfig> at all leaves the field exactly as
    // user-defined as it always was (an empty dict back from the bridge).
    // Only the *first* sample is checked — pixel size is one session-wide
    // field here (matching pixelSize's own single-value design), not
    // per-sample, so a project mixing different rasters across samples
    // still needs the user to confirm/override this by hand.
    function restorePixelSizeDefault() {
        if (calibrationSection.pixelSize > 0) return  // already set — don't clobber
        if (!analysis || !analysis.analysisDbPath) return
        // Reads samples straight from the bridge rather than
        // calibrationSection.samples — that property's own binding
        // reacts to the *same* analysisChanged notification this
        // function is usually called from, and binding-vs-handler
        // evaluation order for the same signal isn't guaranteed, so it
        // could still hold the previous analysis' stale value at this
        // exact point.
        var sampleList = AnalysisBridge.getSamples(analysis.analysisDbPath)
        if (sampleList.length === 0) return
        var result = AnalysisBridge.getSamplePixelSize(analysis.analysisDbPath, sampleList[0].name)
        if (result && result.pixel_size) {
            calibrationSection.pixelSize = result.pixel_size
            calibrationSection.pixelSizeUnit = result.pixel_size_unit
            // pixelSizeField.text has no live binding back to pixelSize
            // (only the other direction, onTextChanged) — same "seed
            // once, not a two-way binding" reasoning as the sample-
            // visibility checkboxes elsewhere in this codebase, to avoid
            // fighting the user's own typing with a reactive loop. Set
            // once, here, imperatively.
            pixelSizeField.text = String(result.pixel_size)
        }
    }

    onAnalysisChanged: {
        if (analysis && analysis.analysisDbPath) {
            AnalysisBridge.setHeatmapAnalysis(analysis.analysisDbPath)
        }
        calibrationSection.pixelSize = 0.0  // a fresh analysis' own default, not the previous one's
        calibrationSection.refreshAll()
        calibrationSection.restoreAnalyteMz()
        calibrationSection.restorePixelSizeDefault()
    }

    Component.onCompleted: {
        calibrationSection.refreshAll()
        calibrationSection.restoreAnalyteMz()
        calibrationSection.restorePixelSizeDefault()
    }

    // Drives RoiDesignWindow's rendering (which m/z the drawn ROI is shown
    // against) — never itself shown in this section's own layout; its
    // feature/obs picker UI is untouched but simply not part of the
    // visible tree here (width/height 0, visible false).
    HeatmapControlsPanel {
        id: controls
        objectName: "calibrationControlsPanel"
        analysis: calibrationSection.analysis
        samples: calibrationSection.samples
        showSampleVisibility: false
        width: 0
        height: 0
        visible: false
    }

    // Same lazy-Loader-plus-openFor pattern as VisualInspectionSection's
    // own roiDesignWindowLoader — one window per trigger, reused across
    // clicks. Two separate windows (not one generic dialog + a save-time
    // type choice) since "Add Standard" and "Add Sample to Quantify" are
    // contextually distinct actions, per the feature's own design.
    Loader {
        id: standardRoiWindowLoader
        objectName: "standardRoiWindowLoader"
        active: false
        sourceComponent: RoiDesignWindow {
            objectName: "standardRoiDesignWindow"
        }
        onLoaded: {
            item.roiSaved.connect(function (sampleName, roiName) {
                calibrationSection.pendingStandardsQueue = calibrationSection.pendingStandardsQueue.concat(
                    [{sampleName: sampleName, roiName: roiName}])
                calibrationSection._showNextPendingStandard()
            })
        }
    }

    Loader {
        id: unknownRoiWindowLoader
        objectName: "unknownRoiWindowLoader"
        active: false
        sourceComponent: RoiDesignWindow {
            objectName: "unknownRoiDesignWindow"
        }
        onLoaded: {
            item.roiSaved.connect(function (sampleName, roiName) {
                var result = AnalysisBridge.addCalibrationUnknown(
                    calibrationSection.analysis.analysisDbPath, sampleName, roiName,
                    calibrationSection.analyteMz, calibrationSection._pixelSizeInAreaUnit(),
                    calibrationSection.concentrationAreaUnit)
                if (result.ok) {
                    calibrationSection.refreshUnknowns()
                } else {
                    calibrationSection.unknownError = result.error
                }
            })
        }
    }
    property string unknownError: ""

    // A standard's amount popup — appears immediately once its ROI is
    // saved (while the spot is still fresh), per the feature's own design.
    // Anything drawn while this is already open queues instead of being
    // lost (see pendingStandardsQueue/_showNextPendingStandard above);
    // `onClosed` (fires for Save *and* Cancel, or closing it any other
    // way) advances to the next queued one, if any, so a skipped/cancelled
    // standard doesn't block the rest.
    // `standardButtons: Dialog.Cancel` only (no Ok) — "Save" below
    // validates against AnalysisBridge first and keeps the dialog open
    // with an inline error on failure, which Dialog.Ok's own
    // accept-and-close behavior can't do.
    Dialog {
        id: standardConcentrationDialog
        objectName: "standardConcentrationDialog"
        title: "Standard amount — " + calibrationSection.pendingStandardRoi
              + (calibrationSection.pendingStandardsQueue.length > 0
                 ? " (" + calibrationSection.pendingStandardsQueue.length + " more pending)" : "")
        modal: true
        standardButtons: Dialog.Cancel
        anchors.centerIn: Overlay.overlay
        property string errorText: ""
        onClosed: calibrationSection._showNextPendingStandard()

        ColumnLayout {
            spacing: 8

            Label { text: "Amount of analyte in this spot:" }
            RowLayout {
                TextField {
                    id: standardAmountField
                    objectName: "standardAmountField"
                    placeholderText: "e.g. 10"
                    validator: DoubleValidator { bottom: 0 }
                    Layout.preferredWidth: 120
                }
                ComboBox {
                    id: standardAmountUnitCombo
                    objectName: "standardAmountUnitCombo"
                    model: AnalysisBridge.amountUnits
                    currentIndex: Math.max(0, model.indexOf(calibrationSection.amountUnit))
                }
            }
            Label {
                objectName: "standardConcentrationError"
                visible: standardConcentrationDialog.errorText !== ""
                text: standardConcentrationDialog.errorText
                color: Theme.errorColor
                wrapMode: Text.Wrap
                Layout.preferredWidth: 260
            }
            Button {
                objectName: "saveStandardButton"
                text: "Save"
                enabled: standardAmountField.text !== ""
                onClicked: {
                    var result = AnalysisBridge.addCalibrationPoint(
                        calibrationSection.analysis.analysisDbPath,
                        calibrationSection.pendingStandardSample,
                        calibrationSection.pendingStandardRoi,
                        calibrationSection.analyteMz,
                        parseFloat(standardAmountField.text),
                        standardAmountUnitCombo.currentText,
                        calibrationSection._pixelSizeInAreaUnit(),
                        calibrationSection.concentrationAreaUnit)
                    if (result.ok) {
                        calibrationSection.amountUnit = standardAmountUnitCombo.currentText
                        calibrationSection.refreshStandards()
                        standardConcentrationDialog.close()
                    } else {
                        standardConcentrationDialog.errorText = result.error
                    }
                }
                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }
    }

    Flickable {
        id: pageFlickable
        objectName: "calibrationFlickable"
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: pageColumn.implicitHeight
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: pageColumn
            width: pageFlickable.width
            spacing: 16

            Label {
                objectName: "calibrationEmptyStateLabel"
                visible: calibrationSection.samples.length === 0
                text: "No samples in this analysis."
                color: Theme.mutedTextColor
            }

            // --- Prerequisites: analyte m/z + pixel size (shared by every
            // standard/unknown added in this session) ---
            GroupBox {
                title: "Setup"
                Layout.fillWidth: true
                visible: calibrationSection.samples.length > 0

                RowLayout {
                    spacing: 12

                    Label { text: "Analyte:" }
                    ComboBox {
                        id: analyteCombo
                        objectName: "analyteCombo"
                        Layout.preferredWidth: 200
                        visible: controls.featureLabels.length > 0
                        model: controls.featureLabels
                        currentIndex: controls.selectedFeatureIndex
                        onActivated: (index) => controls.selectedFeatureIndex = index
                        HoverHandler {
                            cursorShape: Qt.PointingHandCursor
                        }
                    }
                    Label {
                        objectName: "noAnalytesLabel"
                        visible: controls.featureLabels.length === 0
                        text: "No analytes configured for this run."
                        color: Theme.mutedTextColor
                    }

                    Item { Layout.preferredWidth: 16 }

                    Label { text: "Pixel size:" }
                    TextField {
                        id: pixelSizeField
                        objectName: "pixelSizeField"
                        placeholderText: "e.g. 50"
                        validator: DoubleValidator { bottom: 0 }
                        Layout.preferredWidth: 80
                        onTextChanged: calibrationSection.pixelSize = parseFloat(text) || 0.0
                    }
                    ComboBox {
                        id: pixelSizeUnitCombo
                        objectName: "pixelSizeUnitCombo"
                        model: AnalysisBridge.pixelSizeUnits
                        currentIndex: Math.max(0, model.indexOf(calibrationSection.pixelSizeUnit))
                        onActivated: calibrationSection.pixelSizeUnit = currentText
                    }

                    Item { Layout.preferredWidth: 16 }

                    Label { text: "Report area/concentration per:" }
                    ComboBox {
                        id: concentrationAreaUnitCombo
                        objectName: "concentrationAreaUnitCombo"
                        // Independent from pixelSizeUnitCombo above — see
                        // concentrationAreaUnit's own comment. Same model
                        // (um/mm) since a concentration's area denominator
                        // uses the same two length units pixel size does,
                        // just possibly a different one of the two.
                        model: AnalysisBridge.pixelSizeUnits
                        currentIndex: Math.max(0, model.indexOf(calibrationSection.concentrationAreaUnit))
                        onActivated: calibrationSection.concentrationAreaUnit = currentText
                        HoverHandler {
                            cursorShape: Qt.PointingHandCursor
                        }
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            // --- Step 1: Calibration standards ---
            GroupBox {
                title: "Step 1 — Calibration standards"
                Layout.fillWidth: true
                visible: calibrationSection.samples.length > 0

                ColumnLayout {
                    width: parent.width
                    spacing: 8

                    RowLayout {
                        Button {
                            objectName: "addStandardButton"
                            text: "Add Standard"
                            enabled: calibrationSection.hasAnalyte && calibrationSection.pixelSize > 0
                            onClicked: {
                                standardRoiWindowLoader.active = true
                                standardRoiWindowLoader.item.openFor(
                                    calibrationSection.analysis, calibrationSection.samples, controls)
                            }
                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        Label {
                            visible: !calibrationSection.hasAnalyte
                            text: "Select an analyte above first."
                            color: Theme.mutedTextColor
                        }
                        Label {
                            visible: calibrationSection.hasAnalyte && calibrationSection.pixelSize <= 0
                            text: "Enter a pixel size above first."
                            color: Theme.mutedTextColor
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Label {
                        objectName: "standardsEmptyLabel"
                        visible: calibrationSection.standardsRows.length === 0
                        text: "No standards yet."
                        color: Theme.mutedTextColor
                    }

                    RowLayout {
                        visible: calibrationSection.standardsRows.length > 0
                        Layout.fillWidth: true
                        Label { text: "ROI"; Layout.preferredWidth: 100; font.bold: true }
                        Label { text: "Sample"; Layout.preferredWidth: 90; font.bold: true }
                        Label { text: "Amount"; Layout.preferredWidth: 110; font.bold: true }
                        Label { text: "Area"; Layout.preferredWidth: 110; font.bold: true }
                        Label {
                            objectName: "standardsConcentrationHeader"
                            // The unit in a distinct color, right in the
                            // header — reported directly: easy to lose
                            // track of which square-length unit a
                            // concentration column is actually in,
                            // especially now that it's independently
                            // choosable from the pixel-size input unit
                            // (concentrationAreaUnitCombo).
                            text: "Concentration <font color=\"" + Theme.primaryColor + "\">("
                                  + calibrationSection.concentrationUnitLabel + ")</font>"
                            textFormat: Text.RichText
                            Layout.preferredWidth: 160
                            font.bold: true
                        }
                        Label { text: "Signal"; Layout.preferredWidth: 90; font.bold: true }
                        Label { text: "N Px"; Layout.preferredWidth: 50; font.bold: true }
                        Item { Layout.fillWidth: true }
                    }

                    Column {
                        id: standardsColumn
                        Layout.fillWidth: true
                        spacing: 4

                        Repeater {
                            id: standardsRepeater
                            objectName: "standardsRepeater"
                            model: calibrationSection.standardsRows

                            delegate: Rectangle {
                                objectName: "standardRow_" + modelData.roi_name
                                width: standardsColumn.width
                                height: 32
                                radius: 4
                                color: "transparent"
                                border.color: palette.mid
                                border.width: 1

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 6

                                    Label {
                                        objectName: "standardRoi_" + modelData.roi_name
                                        text: modelData.roi_name
                                        Layout.preferredWidth: 100
                                    }
                                    Label { text: modelData.sample_name; Layout.preferredWidth: 90 }
                                    Label {
                                        text: Number(modelData.amount).toLocaleString() + " " + modelData.amount_unit
                                        Layout.preferredWidth: 110
                                    }
                                    Label {
                                        objectName: "standardArea_" + modelData.roi_name
                                        // Redisplayed in whichever unit
                                        // concentrationAreaUnitCombo
                                        // currently has selected, not
                                        // necessarily the unit this row
                                        // was originally computed in (see
                                        // _areaConversionFactor).
                                        text: Number(modelData.area * calibrationSection._areaConversionFactor(
                                                  modelData.pixel_size_unit, calibrationSection.concentrationAreaUnit))
                                              .toPrecision(4) + " " + calibrationSection.concentrationAreaUnit + "²"
                                        Layout.preferredWidth: 110
                                    }
                                    Label {
                                        objectName: "standardConcentration_" + modelData.roi_name
                                        text: Number(modelData.concentration / calibrationSection._areaConversionFactor(
                                                  modelData.pixel_size_unit, calibrationSection.concentrationAreaUnit))
                                              .toPrecision(4)
                                        Layout.preferredWidth: 160
                                    }
                                    Label {
                                        text: Number(modelData.mean_intensity).toPrecision(4)
                                        Layout.preferredWidth: 90
                                    }
                                    Label { text: String(modelData.n_pixels); Layout.preferredWidth: 50 }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        objectName: "deleteStandardButton_" + modelData.roi_name
                                        text: "Delete"
                                        onClicked: {
                                            AnalysisBridge.deleteCalibrationPoint(
                                                calibrationSection.analysis.analysisDbPath, modelData.roi_name)
                                            calibrationSection.refreshStandards()
                                        }
                                        HoverHandler {
                                            cursorShape: Qt.PointingHandCursor
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // --- Step 2: Fit & review the curve ---
            GroupBox {
                title: "Step 2 — Fit & review the curve"
                Layout.fillWidth: true
                visible: calibrationSection.samples.length > 0

                ColumnLayout {
                    width: parent.width
                    spacing: 8

                    RowLayout {
                        Label { text: "Fit type:" }
                        ComboBox {
                            id: fitTypeCombo
                            objectName: "fitTypeCombo"
                            model: ["linear", "weighted_linear"]
                            currentIndex: model.indexOf(calibrationSection.fitType)
                            onActivated: calibrationSection.fitType = currentText
                        }
                        Label { text: "Weighting:" }
                        ComboBox {
                            id: weightingCombo
                            objectName: "weightingCombo"
                            model: ["1/x2", "1/x"]
                            enabled: fitTypeCombo.currentText === "weighted_linear"
                            currentIndex: model.indexOf(calibrationSection.weighting)
                            onActivated: calibrationSection.weighting = currentText
                        }
                        Button {
                            objectName: "fitCurveButton"
                            text: "Fit Curve"
                            enabled: calibrationSection.standardsRows.length >= 2
                                     && calibrationSection.hasAnalyte
                            onClicked: {
                                var result = AnalysisBridge.fitCalibrationCurve(
                                    calibrationSection.analysis.analysisDbPath,
                                    calibrationSection.analyteMz, fitTypeCombo.currentText,
                                    fitTypeCombo.currentText === "weighted_linear"
                                        ? weightingCombo.currentText : "")
                                if (result.ok) {
                                    calibrationSection.refreshCurve()
                                    calibrationSection.refreshUnknowns()
                                    fitCurveError.text = ""
                                } else {
                                    fitCurveError.text = result.error
                                }
                            }
                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Label {
                        id: fitCurveError
                        objectName: "fitCurveError"
                        visible: text !== ""
                        color: Theme.errorColor
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }

                    Label {
                        objectName: "curveStatsLabel"
                        visible: calibrationSection.hasCurve
                        // Redisplayed in concentrationAreaUnitCombo's
                        // current selection, matching the plot below
                        // (see _areaConversionFactor/_curveAreaUnit) —
                        // slope/LOD/LOQ are concentration-scaled, so they
                        // need converting from the curve's own fit unit;
                        // intercept (the y-value at concentration=0) and
                        // R² don't depend on the concentration axis's
                        // unit at all.
                        text: calibrationSection.hasCurve
                              ? ("slope = " + Number(calibrationSection.currentCurve.slope
                                     * calibrationSection._areaConversionFactor(
                                           calibrationSection._curveAreaUnit(), calibrationSection.concentrationAreaUnit)
                                     ).toPrecision(4)
                                 + "   intercept = " + Number(calibrationSection.currentCurve.intercept).toPrecision(4)
                                 + "   R² = " + Number(calibrationSection.currentCurve.r_squared).toFixed(4)
                                 // `!= null` (loose), not `!== null` — a
                                 // Python `None` nested inside a dict
                                 // inside a `Slot(result=...)` list arrives
                                 // in QML/JS as `undefined`, not `null`
                                 // (confirmed directly: a strict `!==
                                 // null` check let it through into
                                 // `Number(undefined).toPrecision()`,
                                 // rendering "NaN"); `!= null` catches
                                 // both.
                                 + "   LOD = " + (calibrationSection.currentCurve.lod != null
                                                  ? Number(calibrationSection.currentCurve.lod
                                                        / calibrationSection._areaConversionFactor(
                                                              calibrationSection._curveAreaUnit(), calibrationSection.concentrationAreaUnit)
                                                        ).toPrecision(3) : "-")
                                 + "   LOQ = " + (calibrationSection.currentCurve.loq != null
                                                  ? Number(calibrationSection.currentCurve.loq
                                                        / calibrationSection._areaConversionFactor(
                                                              calibrationSection._curveAreaUnit(), calibrationSection.concentrationAreaUnit)
                                                        ).toPrecision(3) : "-")
                                 + "   unit = " + calibrationSection.concentrationUnitLabel)
                              : ""
                    }

                    CalibrationPlot {
                        objectName: "calibrationPlot"
                        visible: calibrationSection.hasCurve
                        plotData: calibrationSection.plotData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 450
                    }
                }
            }

            // --- Step 3: Quantify unknowns ---
            GroupBox {
                title: "Step 3 — Quantify unknowns"
                Layout.fillWidth: true
                visible: calibrationSection.samples.length > 0

                ColumnLayout {
                    width: parent.width
                    spacing: 8

                    RowLayout {
                        Button {
                            objectName: "addUnknownButton"
                            text: "Add Sample to Quantify"
                            enabled: calibrationSection.hasAnalyte && calibrationSection.pixelSize > 0
                            onClicked: {
                                unknownRoiWindowLoader.active = true
                                unknownRoiWindowLoader.item.openFor(
                                    calibrationSection.analysis, calibrationSection.samples, controls)
                            }
                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        Label {
                            objectName: "unknownErrorLabel"
                            visible: calibrationSection.unknownError !== ""
                            text: calibrationSection.unknownError
                            color: Theme.errorColor
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Label {
                        objectName: "unknownsEmptyLabel"
                        visible: calibrationSection.unknownsRows.length === 0
                        text: "No samples quantified yet."
                        color: Theme.mutedTextColor
                    }

                    RowLayout {
                        visible: calibrationSection.unknownsRows.length > 0
                        Layout.fillWidth: true
                        Label { text: "ROI"; Layout.preferredWidth: 100; font.bold: true }
                        Label { text: "Sample"; Layout.preferredWidth: 90; font.bold: true }
                        Label { text: "Signal"; Layout.preferredWidth: 90; font.bold: true }
                        Label { text: "N Px"; Layout.preferredWidth: 50; font.bold: true }
                        Label { text: "Area"; Layout.preferredWidth: 110; font.bold: true }
                        Label {
                            objectName: "unknownsConcentrationHeader"
                            text: "Concentration <font color=\"" + Theme.primaryColor + "\">("
                                  + calibrationSection.concentrationUnitLabel + ")</font>"
                            textFormat: Text.RichText
                            Layout.preferredWidth: 160
                            font.bold: true
                        }
                        Label { text: "Amount"; Layout.preferredWidth: 110; font.bold: true }
                        Item { Layout.fillWidth: true }
                    }

                    Column {
                        id: unknownsColumn
                        Layout.fillWidth: true
                        spacing: 4

                        Repeater {
                            id: unknownsRepeater
                            objectName: "unknownsRepeater"
                            model: calibrationSection.unknownsRows

                            delegate: Rectangle {
                                objectName: "unknownRow_" + modelData.roi_name
                                width: unknownsColumn.width
                                height: 32
                                radius: 4
                                color: "transparent"
                                border.color: palette.mid
                                border.width: 1

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 6

                                    Label { text: modelData.roi_name; Layout.preferredWidth: 100 }
                                    Label { text: modelData.sample_name; Layout.preferredWidth: 90 }
                                    Label {
                                        text: Number(modelData.mean_intensity).toPrecision(4)
                                        Layout.preferredWidth: 90
                                    }
                                    Label { text: String(modelData.n_pixels); Layout.preferredWidth: 50 }
                                    Label {
                                        objectName: "unknownArea_" + modelData.roi_name
                                        text: Number(modelData.area * calibrationSection._areaConversionFactor(
                                                  modelData.pixel_size_unit, calibrationSection.concentrationAreaUnit))
                                              .toPrecision(4) + " " + calibrationSection.concentrationAreaUnit + "²"
                                        Layout.preferredWidth: 110
                                    }
                                    Label {
                                        objectName: "unknownConcentration_" + modelData.roi_name
                                        // `!= null`, not `!== null` — see
                                        // curveStatsLabel's own comment
                                        // above for why.
                                        text: modelData.interpolated_conc != null
                                              ? Number(modelData.interpolated_conc / calibrationSection._areaConversionFactor(
                                                    modelData.pixel_size_unit, calibrationSection.concentrationAreaUnit))
                                                    .toPrecision(4)
                                              : "no curve yet"
                                        Layout.preferredWidth: 160
                                    }
                                    Label {
                                        text: modelData.amount != null
                                              ? Number(modelData.amount).toPrecision(4) : "-"
                                        Layout.preferredWidth: 110
                                    }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        objectName: "deleteUnknownButton_" + modelData.roi_name
                                        text: "Delete"
                                        onClicked: {
                                            AnalysisBridge.deleteCalibrationUnknown(
                                                calibrationSection.analysis.analysisDbPath, modelData.id)
                                            calibrationSection.refreshUnknowns()
                                        }
                                        HoverHandler {
                                            cursorShape: Qt.PointingHandCursor
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

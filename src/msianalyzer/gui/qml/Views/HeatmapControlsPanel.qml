import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"
import "qrc:/Utils/SearchQuery.js" as SearchQuery

// Visual Inspection's feature/obs-column/layer/colormap/vmin-vmax/sample-
// visibility control panel — extracted out of VisualInspectionSection.qml
// so ROI Design's drawing window (which needs the exact same
// feature/obs/colormap/vmin-vmax selection, just for one sample instead of
// a multi-sample grid) can embed it instead of duplicating this QML.
//
// Every objectName below is unchanged from its original inline
// declaration in VisualInspectionSection.qml, and the root is a Flickable
// (not a wrapper Item around one) — this is a like-for-like relocation of
// the exact same tree, just declared in its own file, so this codebase's
// test harness (`find_visual_child`, a visual-tree walk rather than a
// scoped `findChild`) needs no changes to keep finding every control.
Flickable {
    id: controlsFlickable
    objectName: "controlsFlickable"
    // See VisualInspectionSection.qml's own history: widened from 260 —
    // the vmin/vmax slider+field rows were fitting with ~0px to spare.
    Layout.preferredWidth: 300
    Layout.minimumWidth: 300
    Layout.maximumWidth: 300
    Layout.fillHeight: true
    clip: true
    contentWidth: width
    contentHeight: controlsPanel.implicitHeight
    boundsBehavior: Flickable.StopAtBounds

    property var analysis
    property var samples: []
    // false for ROI Design's single-sample mode — that window's own
    // sample combobox picks the one sample being drawn on, so a
    // show/hide-per-sample checklist (meant for a multi-tile grid) has
    // nothing to do there.
    property bool showSampleVisibility: true

    property var features: (controlsFlickable.analysis && controlsFlickable.analysis.analysisDbPath)
                            ? AnalysisBridge.getFeatureList(controlsFlickable.analysis.analysisDbPath) : []

    // "feature" (an m/z column, the original mode) or "obs" (a per-pixel
    // `adata.obs` column, e.g. `tic`/`rt` — not tied to any one feature).
    // Mutually exclusive with the feature selector below.
    property string inspectionMode: "feature"
    property var obsColumns: (controlsFlickable.analysis && controlsFlickable.analysis.analysisDbPath
                               && controlsFlickable.samples.length > 0)
                              ? AnalysisBridge.getObsColumns(
                                    controlsFlickable.samples.map(function (s) { return s.name }))
                              : []
    property var obsColumnLabels: controlsFlickable.obsColumns.map(function (c) {
        return c.numeric ? c.name : (c.name + " (categories)")
    })
    property int selectedObsIndex: 0
    // A fresh obsColumns list (new analysis) defaults the selection to
    // "tic" rather than whatever happens to sort first, falling back to
    // the first column if "tic" isn't present.
    onObsColumnsChanged: {
        var ticIndex = 0
        for (var i = 0; i < controlsFlickable.obsColumns.length; i++) {
            if (controlsFlickable.obsColumns[i].name === "tic") {
                ticIndex = i
                break
            }
        }
        controlsFlickable.selectedObsIndex = ticIndex
    }
    property var selectedObsColumnInfo: (controlsFlickable.obsColumns.length > controlsFlickable.selectedObsIndex)
                                         ? controlsFlickable.obsColumns[controlsFlickable.selectedObsIndex] : null
    property string selectedObsColumn: controlsFlickable.selectedObsColumnInfo
                                        ? controlsFlickable.selectedObsColumnInfo.name : ""
    property bool isSelectedObsNumeric: controlsFlickable.selectedObsColumnInfo
                                         ? !!controlsFlickable.selectedObsColumnInfo.numeric : true

    // The color-scale controls (autoscale/vmin/vmax/colormap) apply to
    // the feature mode and to a numeric obs column; a discrete obs column
    // has no color scale, just a fixed-palette legend.
    property bool showsColorScale: controlsFlickable.inspectionMode === "feature"
                                    || (controlsFlickable.inspectionMode === "obs" && controlsFlickable.isSelectedObsNumeric)

    property var obsCategoryLegend: []
    function refreshObsCategoryLegend() {
        if (controlsFlickable.inspectionMode !== "obs" || !controlsFlickable.selectedObsColumn
                || controlsFlickable.isSelectedObsNumeric) {
            controlsFlickable.obsCategoryLegend = []
            return
        }
        // Every sample, not just the visible ones — a category's color
        // must not depend on which samples happen to be shown, or
        // toggling visibility would reshuffle colors underneath tiles
        // still on screen.
        var allNames = controlsFlickable.samples.map(function (s) { return s.name })
        controlsFlickable.obsCategoryLegend = AnalysisBridge.getObsCategories(
            allNames, controlsFlickable.selectedObsColumn)
    }

    // "mz" (the bridge's own order — already ascending) or "name" (every
    // annotated feature alphabetically by compound, then every
    // unannotated feature by m/z, at the end).
    property string sortMode: "mz"

    // Search by compound name (contains) or m/z (a single value within a
    // small tolerance, or an explicit "min-max" range) — same syntax as
    // Annotate's search box, see SearchQuery.matches. Applied before
    // sorting; feature-mode only, an obs column has no name/mz to search.
    property string searchQuery: ""
    onSearchQueryChanged: controlsFlickable.selectedFeatureIndex = 0

    readonly property var filteredFeatures: controlsFlickable.features.filter(function (f) {
        return SearchQuery.matches(controlsFlickable.searchQuery, f.compound_name, f.mz)
    })

    property var sortedFeatures: {
        var feats = controlsFlickable.filteredFeatures.slice()
        if (controlsFlickable.sortMode === "name") {
            var annotated = feats.filter(function (f) { return !!f.compound_name })
            var unannotated = feats.filter(function (f) { return !f.compound_name })
            annotated.sort(function (a, b) { return a.compound_name.localeCompare(b.compound_name) })
            unannotated.sort(function (a, b) { return a.mz - b.mz })
            return annotated.concat(unannotated)
        }
        feats.sort(function (a, b) { return a.mz - b.mz })
        return feats
    }

    // Annotated: "150.1234: Caffeine". Unannotated: "m/z 150.1234" (no
    // name to show).
    property var featureLabels: controlsFlickable.sortedFeatures.map(function (f) {
        return f.compound_name
               ? (Number(f.mz).toFixed(4) + ": " + f.compound_name)
               : ("m/z " + Number(f.mz).toFixed(4))
    })
    property int selectedFeatureIndex: 0
    property var selectedFeature: (controlsFlickable.sortedFeatures.length > controlsFlickable.selectedFeatureIndex)
                                   ? controlsFlickable.sortedFeatures[controlsFlickable.selectedFeatureIndex] : null

    property string colormap: "viridis"
    // vmin/vmax as actually *applied* to rendering (what vminToken/
    // vmaxToken below read) — separate from the slider/field's draft
    // values so dragging the slider doesn't re-render every tile on
    // every intermediate tick. draftVmin/Max start out tracking vmin/vmax
    // declaratively; the first slider drag or text edit breaks that
    // binding for the rest of the session (normal QML "last assignment
    // wins"), which is fine here since nothing else ever changes vmin/
    // vmax except applyColorRange() copying the draft into it.
    property real vmin: 0
    property real vmax: 10
    property real draftVmin: vmin
    property real draftVmax: vmax
    property bool autoScale: true
    property string dataLayer: "TIC"
    property var hiddenSamples: ({})

    // What autoscale is *actually* using right now — the real per-pixel
    // min/max for the selected feature+layer across the visible samples
    // (AnalysisBridge.getFeatureValueRange, backed by the same h5ad data
    // the tiles render from). Shown as the vmin/vmax labels whenever
    // autoscale is on, and what turning autoscale off seeds the manual
    // range from.
    property var autoRange: ({"vmin": 0, "vmax": 1})

    function visibleSampleNames() {
        return controlsFlickable.samples
            .filter(function (s) { return !controlsFlickable.isHidden(s.name) })
            .map(function (s) { return s.name })
    }

    function refreshAutoRange() {
        // Reads inspectionMode/isSelectedObsNumeric directly rather than
        // the cached showsColorScale property: this function runs
        // *synchronously* from onInspectionModeChanged/
        // onSelectedObsColumnChanged, i.e. within the very same change
        // notification whose dependency showsColorScale's own binding
        // also listens on — and a derived property's binding isn't
        // guaranteed to have re-evaluated yet by the time a sibling
        // handler for that same underlying signal runs. Everywhere else
        // showsColorScale is used declaratively (`visible:
        // controlsFlickable.showsColorScale`) it's fine; it's only
        // unsafe to read from code executing inline with the very change
        // that invalidates it.
        var showsColorScale = controlsFlickable.inspectionMode === "feature"
            || (controlsFlickable.inspectionMode === "obs" && controlsFlickable.isSelectedObsNumeric)
        if (!controlsFlickable.analysis || !controlsFlickable.analysis.analysisDbPath || !showsColorScale) {
            controlsFlickable.autoRange = {"vmin": 0, "vmax": 1}
            return
        }
        var names = controlsFlickable.visibleSampleNames()
        if (names.length === 0) {
            controlsFlickable.autoRange = {"vmin": 0, "vmax": 1}
            return
        }
        if (controlsFlickable.inspectionMode === "obs") {
            if (!controlsFlickable.selectedObsColumn) {
                controlsFlickable.autoRange = {"vmin": 0, "vmax": 1}
                return
            }
            controlsFlickable.autoRange = AnalysisBridge.getObsValueRange(
                names, controlsFlickable.selectedObsColumn)
            return
        }
        if (!controlsFlickable.selectedFeature) {
            controlsFlickable.autoRange = {"vmin": 0, "vmax": 1}
            return
        }
        controlsFlickable.autoRange = AnalysisBridge.getFeatureValueRange(
            names, controlsFlickable.selectedFeature.mz, controlsFlickable.dataLayer)
    }

    // Switching layers/mode/obs-column changes the data's whole scale —
    // falling back to autoscale re-renders correctly immediately; turning
    // autoscale back off afterward starts from that new selection's own
    // real range (autoRange) rather than carrying over a stale one.
    onDataLayerChanged: {
        controlsFlickable.autoScale = true
        controlsFlickable.refreshAutoRange()
    }
    onInspectionModeChanged: {
        controlsFlickable.autoScale = true
        controlsFlickable.refreshAutoRange()
        controlsFlickable.refreshObsCategoryLegend()
        // obsColumnCombo only becomes visible on switching into "obs" mode
        // — `obsColumns` itself was already fetched (and selectedObsIndex
        // set to "tic") back when the analysis first loaded, well before
        // the combo was ever shown, so the earlier resync in
        // onObsColumnsChanged below doesn't help here: a ComboBox that
        // was never visible doesn't reliably keep an imperative
        // currentIndex write made while hidden once its popup/delegate is
        // actually realized. Re-assert on the transition the user
        // actually sees (reported: switching to Obs column mode rendered
        // "tic" correctly but the combo visually showed "rt" selected).
        if (controlsFlickable.inspectionMode === "obs") {
            Qt.callLater(function () {
                obsColumnCombo.currentIndex = controlsFlickable.selectedObsIndex
            })
        }
    }
    onSelectedObsColumnChanged: {
        controlsFlickable.autoScale = true
        controlsFlickable.refreshAutoRange()
        controlsFlickable.refreshObsCategoryLegend()
    }
    onSelectedFeatureChanged: controlsFlickable.refreshAutoRange()
    onHiddenSamplesChanged: controlsFlickable.refreshAutoRange()

    function isHidden(name) {
        return !!controlsFlickable.hiddenSamples[name]
    }

    function toggleSample(name) {
        var copy = Object.assign({}, controlsFlickable.hiddenSamples)
        copy[name] = !copy[name]
        controlsFlickable.hiddenSamples = copy
    }

    // Commits the slider/field's draft vmin/vmax to the applied values
    // every tile actually renders with — a deliberate action rather than
    // every drag tick re-rendering every visible tile.
    function applyColorRange() {
        controlsFlickable.vmin = controlsFlickable.draftVmin
        controlsFlickable.vmax = controlsFlickable.draftVmax
    }

    // Every tile shares this vmin/vmax unless autoscale is on, in which
    // case each tile scales independently to its own data.
    function vminToken() {
        return controlsFlickable.autoScale ? "auto" : String(controlsFlickable.vmin)
    }
    function vmaxToken() {
        return controlsFlickable.autoScale ? "auto" : String(controlsFlickable.vmax)
    }

    // Called by the owning page (VisualInspectionSection.qml /
    // RoiDesignWindow.qml) right after AnalysisBridge.setHeatmapAnalysis,
    // synchronously in the same handler — not wired to this panel's own
    // onAnalysisChanged, since ordering between two different objects'
    // change handlers for the same underlying signal isn't guaranteed,
    // and refreshAutoRange's bridge calls need setHeatmapAnalysis to have
    // already run.
    function refresh() {
        controlsFlickable.refreshAutoRange()
        controlsFlickable.refreshObsCategoryLegend()
    }

    // One sample's image://heatmap/... source, for either mode — shared by
    // every owner of this panel (Visual Inspection's multi-tile grid, ROI
    // Design's single-sample canvas) so the URL-building logic exists in
    // exactly one place.
    function tileSource(sampleName) {
        if (!controlsFlickable.analysis) return ""
        if (controlsFlickable.inspectionMode === "obs") {
            if (!controlsFlickable.selectedObsColumn) return ""
            if (controlsFlickable.isSelectedObsNumeric) {
                return "image://heatmap/" + sampleName + "|obs:" + controlsFlickable.selectedObsColumn
                       + "|" + controlsFlickable.colormap
                       + "|" + controlsFlickable.vminToken()
                       + "|" + controlsFlickable.vmaxToken()
            }
            var cats = controlsFlickable.obsCategoryLegend.map(function (c) { return c.category }).join(",")
            return "image://heatmap/" + sampleName + "|obs:" + controlsFlickable.selectedObsColumn
                   + "|" + cats
        }
        if (!controlsFlickable.selectedFeature) return ""
        return "image://heatmap/" + sampleName + "|" + controlsFlickable.selectedFeature.mz + "|"
               + controlsFlickable.dataLayer + "|" + controlsFlickable.colormap + "|"
               + controlsFlickable.vminToken() + "|" + controlsFlickable.vmaxToken()
    }

    ColumnLayout {
        id: controlsPanel
        objectName: "controlsPanel"
        width: controlsFlickable.width
        spacing: 10

        Label { text: "Show"; font.bold: true }
        RowLayout {
            // Same plain-Button pattern as the Raw/TIC layer toggle
            // below (not RadioButton+ButtonGroup) — see that block's
            // comment for why: a checked-binding + write-back-onToggled
            // cycle on grouped checkable controls hung the QML engine.
            Button {
                id: featureModeButton
                objectName: "featureModeButton"
                text: "Feature"
                highlighted: controlsFlickable.inspectionMode === "feature"
                onClicked: controlsFlickable.inspectionMode = "feature"
                background: Rectangle {
                    radius: 4
                    color: featureModeButton.highlighted ? palette.highlight : palette.button
                    border.color: palette.mid
                }
                contentItem: Label {
                    text: featureModeButton.text
                    color: featureModeButton.highlighted ? palette.highlightedText : palette.buttonText
                    font.bold: featureModeButton.highlighted
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                HoverHandler {
                    objectName: "featureModeButtonHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Button {
                id: obsModeButton
                objectName: "obsModeButton"
                text: "Obs column"
                highlighted: controlsFlickable.inspectionMode === "obs"
                onClicked: controlsFlickable.inspectionMode = "obs"
                background: Rectangle {
                    radius: 4
                    color: obsModeButton.highlighted ? palette.highlight : palette.button
                    border.color: palette.mid
                }
                contentItem: Label {
                    text: obsModeButton.text
                    color: obsModeButton.highlighted ? palette.highlightedText : palette.buttonText
                    font.bold: obsModeButton.highlighted
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                HoverHandler {
                    objectName: "obsModeButtonHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        Label {
            text: "Feature"
            font.bold: true
            visible: controlsFlickable.inspectionMode === "feature"
        }
        TextField {
            id: featureSearchField
            objectName: "featureSearchField"
            Layout.fillWidth: true
            visible: controlsFlickable.inspectionMode === "feature"
            placeholderText: "Search name or m/z…"
            text: controlsFlickable.searchQuery
            onTextChanged: controlsFlickable.searchQuery = text
        }
        RowLayout {
            visible: controlsFlickable.inspectionMode === "feature"
            Label { text: "Sort by" }
            ComboBox {
                id: sortModeCombo
                objectName: "sortModeCombo"
                Layout.fillWidth: true
                model: ["m/z", "Name"]
                currentIndex: controlsFlickable.sortMode === "name" ? 1 : 0
                onActivated: (index) => {
                    controlsFlickable.sortMode = index === 1 ? "name" : "mz"
                    // The old index likely points at a different
                    // feature once the order changes.
                    controlsFlickable.selectedFeatureIndex = 0
                }
                HoverHandler {
                    objectName: "sortModeComboHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }
        Label {
            objectName: "featureSearchEmptyLabel"
            visible: controlsFlickable.inspectionMode === "feature"
                     && controlsFlickable.features.length > 0
                     && controlsFlickable.sortedFeatures.length === 0
            text: "No features match your search."
            color: Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        ComboBox {
            id: featureCombo
            objectName: "featureCombo"
            Layout.fillWidth: true
            visible: controlsFlickable.inspectionMode === "feature"
                     && controlsFlickable.sortedFeatures.length > 0
            model: controlsFlickable.featureLabels
            currentIndex: controlsFlickable.selectedFeatureIndex
            onActivated: (index) => controlsFlickable.selectedFeatureIndex = index
            HoverHandler {
                objectName: "featureComboHover"
                cursorShape: Qt.PointingHandCursor
            }
        }

        Label {
            text: "Obs column"
            font.bold: true
            visible: controlsFlickable.inspectionMode === "obs"
        }
        ComboBox {
            id: obsColumnCombo
            objectName: "obsColumnCombo"
            Layout.fillWidth: true
            visible: controlsFlickable.inspectionMode === "obs"
            model: controlsFlickable.obsColumnLabels
            currentIndex: controlsFlickable.selectedObsIndex
            onActivated: (index) => controlsFlickable.selectedObsIndex = index
            HoverHandler {
                objectName: "obsColumnComboHover"
                cursorShape: Qt.PointingHandCursor
            }
        }

        Label {
            text: "Layer"
            font.bold: true
            visible: controlsFlickable.inspectionMode === "feature"
        }
        RowLayout {
            visible: controlsFlickable.inspectionMode === "feature"
            // Plain (non-checkable) Buttons rather than RadioButton +
            // ButtonGroup — see VisualInspectionSection.qml's git history
            // for why (a checked-binding + write-back-onToggled cycle on
            // grouped checkable controls intermittently hung the QML
            // engine). `highlighted` here is a one-directional binding
            // and `onClicked` a discrete user action, so there's no
            // property/signal cycle to race.
            //
            // Selected state is drawn explicitly (background/text
            // color) rather than relying on `highlighted`'s own
            // rendering — reported as "not so clear which one is
            // selected" under Fusion's fairly subtle highlighted look.
            Button {
                id: rawLayerButton
                objectName: "rawLayerButton"
                text: "Raw"
                highlighted: controlsFlickable.dataLayer === "raw"
                onClicked: controlsFlickable.dataLayer = "raw"
                background: Rectangle {
                    radius: 4
                    color: rawLayerButton.highlighted ? palette.highlight : palette.button
                    border.color: palette.mid
                }
                contentItem: Label {
                    text: rawLayerButton.text
                    color: rawLayerButton.highlighted ? palette.highlightedText : palette.buttonText
                    font.bold: rawLayerButton.highlighted
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                HoverHandler {
                    objectName: "rawLayerButtonHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Button {
                id: ticLayerButton
                objectName: "ticLayerButton"
                text: "TIC"
                highlighted: controlsFlickable.dataLayer === "TIC"
                onClicked: controlsFlickable.dataLayer = "TIC"
                background: Rectangle {
                    radius: 4
                    color: ticLayerButton.highlighted ? palette.highlight : palette.button
                    border.color: palette.mid
                }
                contentItem: Label {
                    text: ticLayerButton.text
                    color: ticLayerButton.highlighted ? palette.highlightedText : palette.buttonText
                    font.bold: ticLayerButton.highlighted
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                HoverHandler {
                    objectName: "ticLayerButtonHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        Label {
            text: "Colormap"
            font.bold: true
            visible: controlsFlickable.showsColorScale
        }
        ComboBox {
            id: colormapCombo
            objectName: "colormapCombo"
            Layout.fillWidth: true
            visible: controlsFlickable.showsColorScale
            model: ["viridis", "magma", "inferno", "plasma", "cividis", "gray"]
            currentIndex: model.indexOf(controlsFlickable.colormap)
            onActivated: (index) => controlsFlickable.colormap = model[index]
            HoverHandler {
                objectName: "colormapComboHover"
                cursorShape: Qt.PointingHandCursor
            }
        }

        Label {
            text: "Color scale"
            font.bold: true
            visible: controlsFlickable.showsColorScale
        }
        CheckBox {
            id: autoScaleCheckBox
            objectName: "autoScaleCheckBox"
            text: "Autoscale"
            visible: controlsFlickable.showsColorScale
            checked: controlsFlickable.autoScale
            onToggled: {
                controlsFlickable.autoScale = checked
                if (!checked) {
                    // Just turned off — start the manual range from
                    // what autoscale was actually using for this
                    // feature+layer, not a stale/arbitrary default.
                    controlsFlickable.vmin = controlsFlickable.autoRange.vmin
                    controlsFlickable.vmax = controlsFlickable.autoRange.vmax
                    controlsFlickable.draftVmin = controlsFlickable.autoRange.vmin
                    controlsFlickable.draftVmax = controlsFlickable.autoRange.vmax
                }
            }
            HoverHandler {
                objectName: "autoScaleCheckBoxHover"
                cursorShape: Qt.PointingHandCursor
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            visible: controlsFlickable.showsColorScale
            Label {
                objectName: "vminValueLabel"
                // Shows what autoscale is using while it's on (so
                // it's not just a mystery), the manual draft while off.
                text: "vmin: " + (controlsFlickable.autoScale
                                   ? controlsFlickable.autoRange.vmin.toFixed(3)
                                   : controlsFlickable.draftVmin.toFixed(3))
            }
            RowLayout {
                enabled: !controlsFlickable.autoScale
                // Disabled controls default to a subtle/style-dependent
                // dimming that wasn't obviously "disabled" against the
                // new light palette — an explicit opacity makes it
                // unambiguous regardless of style.
                opacity: enabled ? 1.0 : 0.4
                Slider {
                    id: vminSlider
                    objectName: "vminSlider"
                    Layout.fillWidth: true
                    from: 0
                    // Autoscale's own vmax for this feature+layer — a
                    // real, data-driven ceiling instead of a fixed guess.
                    to: Math.max(1, controlsFlickable.autoRange.vmax)
                    value: controlsFlickable.draftVmin
                    onMoved: controlsFlickable.draftVmin = value
                }
                TextField {
                    id: vminField
                    objectName: "vminField"
                    Layout.preferredWidth: 80
                    text: controlsFlickable.draftVmin.toFixed(3)
                    onEditingFinished: {
                        var v = parseFloat(text)
                        if (!isNaN(v)) controlsFlickable.draftVmin = v
                    }
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            visible: controlsFlickable.showsColorScale
            Label {
                objectName: "vmaxValueLabel"
                text: "vmax: " + (controlsFlickable.autoScale
                                   ? controlsFlickable.autoRange.vmax.toFixed(3)
                                   : controlsFlickable.draftVmax.toFixed(3))
            }
            RowLayout {
                enabled: !controlsFlickable.autoScale
                opacity: enabled ? 1.0 : 0.4
                Slider {
                    id: vmaxSlider
                    objectName: "vmaxSlider"
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, controlsFlickable.autoRange.vmax)
                    value: controlsFlickable.draftVmax
                    onMoved: controlsFlickable.draftVmax = value
                }
                TextField {
                    id: vmaxField
                    objectName: "vmaxField"
                    Layout.preferredWidth: 80
                    text: controlsFlickable.draftVmax.toFixed(3)
                    onEditingFinished: {
                        var v = parseFloat(text)
                        if (!isNaN(v)) controlsFlickable.draftVmax = v
                    }
                }
            }
        }
        Button {
            id: applyColorRangeButton
            objectName: "applyColorRangeButton"
            text: "Apply color range"
            Layout.fillWidth: true
            visible: controlsFlickable.showsColorScale
            enabled: !controlsFlickable.autoScale
                   && (controlsFlickable.draftVmin !== controlsFlickable.vmin
                       || controlsFlickable.draftVmax !== controlsFlickable.vmax)
            onClicked: controlsFlickable.applyColorRange()
            HoverHandler {
                objectName: "applyColorRangeButtonHover"
                cursorShape: Qt.PointingHandCursor
            }
        }

        // Discrete obs column (e.g. `polarity`) — no color scale, just
        // a fixed-palette legend. Every tile colors by this same
        // ordered category list (controlsFlickable.obsCategoryLegend),
        // see the owning page's tileSource().
        Label {
            text: "Categories"
            font.bold: true
            visible: controlsFlickable.inspectionMode === "obs" && !controlsFlickable.isSelectedObsNumeric
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: controlsFlickable.inspectionMode === "obs" && !controlsFlickable.isSelectedObsNumeric
            Repeater {
                id: obsCategoryLegendRepeater
                objectName: "obsCategoryLegendRepeater"
                model: controlsFlickable.obsCategoryLegend

                delegate: RowLayout {
                    objectName: "obsCategoryLegendRow_" + modelData.category
                    Rectangle {
                        width: 14
                        height: 14
                        radius: 2
                        color: modelData.color
                        border.color: palette.mid
                    }
                    Label { text: modelData.category }
                }
            }
        }

        // Rows/cols picking is disabled for now — see
        // VisualInspectionSection.qml's own history: the grid was too
        // crowded with multiple columns of full-size tiles, so tiles
        // are laid out one per row (fixed) at a smaller, fixed height
        // instead, with the list itself scrolling for more samples.

        ColumnLayout {
            id: sampleVisibilitySection
            objectName: "sampleVisibilitySection"
            visible: controlsFlickable.showSampleVisibility

            Label { text: "Samples"; font.bold: true }
            // No nested Flickable here (unlike the equivalent samples list
            // elsewhere in this codebase) — this whole panel already
            // scrolls via `controlsFlickable`, and a Flickable inside
            // another Flickable's scrolling content fights it for drag
            // gestures.
            Repeater {
                id: sampleVisibilityRepeater
                objectName: "sampleVisibilityRepeater"
                model: controlsFlickable.samples

                delegate: CheckBox {
                    objectName: "sampleVisibility_" + modelData.name
                    // Truncated — real sample names (from the mzML
                    // filename) can be long enough to overflow the panel
                    // on their own; CheckBox doesn't elide its own label.
                    // Full name still available via the tooltip.
                    text: modelData.name.length > 24
                          ? modelData.name.substring(0, 22) + "…" : modelData.name
                    Layout.fillWidth: true
                    // Attached properties (ToolTip.*) aren't readable via
                    // QObject.property() from Python, so the full name is
                    // also a plain property here for tests to read.
                    property string fullName: modelData.name
                    ToolTip.visible: hovered
                    ToolTip.text: fullName
                    // `checked` is seeded once, NOT bound to
                    // `!isHidden(name)` — toggleSample() *inverts*
                    // hiddenSamples, so a live binding here would
                    // re-evaluate after every toggle, flip `checked`
                    // again, fire onToggled again, invert again...
                    // an unbounded oscillation. This checkbox is the
                    // sole mutator of its own sample's hidden state, so
                    // a one-time initial value plus forward-only click
                    // handling is both sufficient and cycle-free.
                    Component.onCompleted: checked = !controlsFlickable.isHidden(modelData.name)
                    onToggled: controlsFlickable.toggleSample(modelData.name)
                    HoverHandler {
                        cursorShape: Qt.PointingHandCursor
                    }
                }
            }
        }
    }
}

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

    // `features` used to be a synchronous property binding straight onto
    // AnalysisBridge.getFeatureList — a direct blocking SQL query on the
    // GUI thread. Fetched on a background thread instead
    // (requestFeatureList / featureListReady, same fire-and-forget
    // pattern as requestMirrorPlot — see ADR 33 for the full reasoning,
    // shared with AnnotationsSection.qml's identical change).
    // `featuresLoading` gates AnalysisPage.qml's LoadingOverlay via
    // currentSectionReady.
    property var features: []
    property bool featuresLoading: false

    function refreshFeatures() {
        if (controlsFlickable.analysis && controlsFlickable.analysis.analysisDbPath) {
            controlsFlickable.featuresLoading = true
            AnalysisBridge.requestFeatureList(controlsFlickable.analysis.analysisDbPath)
        } else {
            controlsFlickable.features = []
            controlsFlickable.featuresLoading = false
        }
    }

    // The `adata.layers` key the normalized layer actually lives under —
    // "TIC" for a TIC-normalized analysis, or e.g. "mz_154.0863" for a
    // reference-m/z-normalized one (see
    // core.utils.tic_normalization.run_normalization's dynamic layer
    // naming). Seeded from the analysis' own recorded normalization mode
    // rather than assumed, so a reference-mz analysis' second dataLayer
    // button points at a layer that actually exists.
    property string normalizedLayerName: "TIC"

    function refreshNormalizedLayerName() {
        if (controlsFlickable.analysis && controlsFlickable.analysis.analysisDbPath) {
            // dataLayer still pointing at the *previous* analysis' own
            // normalized layer name counts as "still at its default" —
            // follow it to the new analysis' real name too. An explicit
            // "raw"/"localMoran" selection is left alone.
            var followDefault = controlsFlickable.dataLayer === controlsFlickable.normalizedLayerName
            controlsFlickable.normalizedLayerName = AnalysisBridge.getNormalizedLayerName(
                controlsFlickable.analysis.analysisDbPath)
            if (followDefault)
                controlsFlickable.dataLayer = controlsFlickable.normalizedLayerName
        } else {
            controlsFlickable.normalizedLayerName = "TIC"
        }
    }

    onAnalysisChanged: {
        controlsFlickable.refreshNormalizedLayerName()
        controlsFlickable.refreshFeatures()
        controlsFlickable.refreshObsColumns()
    }

    Connections {
        target: AnalysisBridge
        function onFeatureListReady(receivedFeatures) {
            controlsFlickable.features = receivedFeatures
            controlsFlickable.featuresLoading = false
        }
    }

    // "feature" (an m/z column, the original mode) or "obs" (a per-pixel
    // `adata.obs` column, e.g. `tic`/`rt` — not tied to any one feature).
    // Mutually exclusive with the feature selector below.
    property string inspectionMode: "feature"

    // Used to be a synchronous property binding straight onto
    // AnalysisBridge.getObsColumns — a direct blocking .h5ad read on the
    // GUI thread every time analysis/samples changed. Fetched on a
    // background thread instead (requestObsColumns/obsColumnsReady, same
    // fire-and-forget pattern as `features`/`featuresLoading` above),
    // part of tracking down Visual Inspection's freeze-on-open persisting
    // even after tile rendering itself was fixed (ADR 48 round 3).
    property var obsColumns: []
    property bool obsColumnsLoading: false

    function refreshObsColumns() {
        if (controlsFlickable.analysis && controlsFlickable.analysis.analysisDbPath
                && controlsFlickable.samples.length > 0) {
            controlsFlickable.obsColumnsLoading = true
            AnalysisBridge.requestObsColumns(
                controlsFlickable.samples.map(function (s) { return s.name }))
        } else {
            controlsFlickable.obsColumns = []
            controlsFlickable.obsColumnsLoading = false
        }
    }

    onSamplesChanged: controlsFlickable.refreshObsColumns()

    Connections {
        target: AnalysisBridge
        function onObsColumnsReady(receivedColumns) {
            controlsFlickable.obsColumns = receivedColumns
            controlsFlickable.obsColumnsLoading = false
        }
    }

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
        // obsColumns now arrives asynchronously (see refreshObsColumns
        // above), so isSelectedObsNumeric can briefly default to `true`
        // (selectedObsColumnInfo still null) if the user switches into
        // obs mode before this list has loaded — refreshAutoRange/
        // refreshObsCategoryLegend may already have run once with that
        // wrong default (one, real: sent a categorical column to
        // getObsValueRange, which errored and logged; the other:
        // silently left obsCategoryLegend empty for a categorical column,
        // never retried). Re-run both now that isSelectedObsNumeric is
        // finally correct — harmless no-ops in feature mode.
        controlsFlickable.refreshAutoRange()
        controlsFlickable.refreshObsCategoryLegend()
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

    // Fetched asynchronously (requestObsCategories/obsCategoriesReady) —
    // same reasoning as `obsColumns` above (ADR 48 round 3): this used to
    // call AnalysisBridge.getObsCategories synchronously, real .h5ad work
    // on the GUI thread.
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
        AnalysisBridge.requestObsCategories(allNames, controlsFlickable.selectedObsColumn)
    }

    Connections {
        target: AnalysisBridge
        function onObsCategoriesReady(categories) {
            // Only apply if still in obs mode with a column selected —
            // guards against a slower, now-superseded request from before
            // a quick mode/column switch landing after a newer one.
            if (controlsFlickable.inspectionMode === "obs" && controlsFlickable.selectedObsColumn
                    && !controlsFlickable.isSelectedObsNumeric) {
                controlsFlickable.obsCategoryLegend = categories
            }
        }
    }

    // "mz" (the bridge's own order — already ascending) or "name" (every
    // annotated feature alphabetically by compound, then every
    // unannotated feature by m/z, at the end).
    property string sortMode: "mz"

    // Two separate fields (ADR 35) — name (contains) and m/z (a single
    // value within a small tolerance, or an explicit "min-max" range),
    // same syntax as Annotate's fields, see SearchQuery.matchesName/
    // matchesMz. Both must match when both are non-empty. Applied before
    // sorting; feature-mode only, an obs column has no name/mz to search.
    property string nameQuery: ""
    property string mzQuery: ""
    onNameQueryChanged: controlsFlickable.selectedFeatureIndex = 0
    onMzQueryChanged: controlsFlickable.selectedFeatureIndex = 0

    readonly property var filteredFeatures: controlsFlickable.features.filter(function (f) {
        return SearchQuery.matchesName(controlsFlickable.nameQuery, f.compound_name)
            && SearchQuery.matchesMz(controlsFlickable.mzQuery, f.mz)
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

    // Dispatches requestObsValueRange/requestFeatureValueRange
    // (fire-and-forget, ADR 48 round 3 — used to call
    // getObsValueRange/getFeatureValueRange synchronously here, real
    // .h5ad work on the GUI thread) rather than assigning `autoRange`
    // directly; the two Connections handlers below apply the result once
    // it lands. Safe to leave `autoRange` briefly stale while a request is
    // in flight — it's purely a UI-display value (the vmin/vmax labels
    // and the manual-range seed), never part of a tile's own render
    // request (see `vminToken()`/`vmaxToken()`, which return the literal
    // string "auto" whenever autoScale is on, independent of this).
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
            AnalysisBridge.requestObsValueRange(names, controlsFlickable.selectedObsColumn)
            return
        }
        if (!controlsFlickable.selectedFeature) {
            controlsFlickable.autoRange = {"vmin": 0, "vmax": 1}
            return
        }
        if (controlsFlickable.dataLayer === "localMoran") {
            // getFeatureValueRange reads a real .h5ad layer's raw
            // intensities — meaningless for a value that's computed
            // on-the-fly (local Moran's I isn't stored as a layer at
            // all), and would silently fall back to raw .X, showing a
            // real-looking but wrong number. No cheap way to get local
            // Moran's I's own min/max without computing the full per-
            // pixel array — exactly what rendering the tile already does
            // — so this mode's vmin/vmax display just reads "auto"
            // literally instead (see the Label bindings below) rather
            // than paying for a second full computation just for a label.
            controlsFlickable.autoRange = {"vmin": 0, "vmax": 1}
            return
        }
        AnalysisBridge.requestFeatureValueRange(
            names, controlsFlickable.selectedFeature.mz, controlsFlickable.dataLayer)
    }

    Connections {
        target: AnalysisBridge
        function onObsValueRangeReady(valueRange) {
            // Guard against a slower obs-mode request landing after the
            // user has since switched to feature mode (each dispatches
            // through a different bridge call/signal, so there's no
            // shared staleness guard between the two on the Python side).
            if (controlsFlickable.inspectionMode === "obs") {
                controlsFlickable.autoRange = valueRange
            }
        }
        function onFeatureValueRangeReady(valueRange) {
            if (controlsFlickable.inspectionMode === "feature") {
                controlsFlickable.autoRange = valueRange
            }
        }
    }

    // Switching layers/mode/obs-column changes the data's whole scale —
    // falling back to autoscale re-renders correctly immediately; turning
    // autoscale back off afterward starts from that new selection's own
    // real range (autoRange) rather than carrying over a stale one.
    onDataLayerChanged: {
        controlsFlickable.autoScale = true
        // Local Moran's I values are signed — the sequential colormaps
        // (viridis/magma/...) that suit a one-sided intensity scale would
        // hide the sign; switch to the one diverging option automatically
        // rather than making the user remember to pick it themselves.
        if (controlsFlickable.dataLayer === "localMoran") {
            controlsFlickable.colormap = "coolwarm"
        }
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

    // The "sampleName|..." tail shared by both image://heatmap/... (this
    // panel's own tiles) and image://he_overlay/... (CoregistrationWindow's
    // warped-onto-H&E overlay, see core/registration/overlay.py) — kept
    // separate from tileSource so a second provider can reuse the exact
    // same target-encoding logic instead of duplicating it.
    function tileTarget(sampleName) {
        if (!controlsFlickable.analysis) return ""
        if (controlsFlickable.inspectionMode === "obs") {
            if (!controlsFlickable.selectedObsColumn) return ""
            if (controlsFlickable.isSelectedObsNumeric) {
                return sampleName + "|obs:" + controlsFlickable.selectedObsColumn
                       + "|" + controlsFlickable.colormap
                       + "|" + controlsFlickable.vminToken()
                       + "|" + controlsFlickable.vmaxToken()
            }
            var cats = controlsFlickable.obsCategoryLegend.map(function (c) { return c.category }).join(",")
            return sampleName + "|obs:" + controlsFlickable.selectedObsColumn + "|" + cats
        }
        if (!controlsFlickable.selectedFeature) return ""
        return sampleName + "|" + controlsFlickable.selectedFeature.mz + "|"
               + controlsFlickable.dataLayer + "|" + controlsFlickable.colormap + "|"
               + controlsFlickable.vminToken() + "|" + controlsFlickable.vmaxToken()
    }

    // One sample's image://heatmap/... source, for either mode — shared by
    // every owner of this panel (Visual Inspection's multi-tile grid, ROI
    // Design's single-sample canvas) so the URL-building logic exists in
    // exactly one place.
    function tileSource(sampleName) {
        var target = controlsFlickable.tileTarget(sampleName)
        return target ? "image://heatmap/" + target : ""
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
            id: featureNameSearchField
            objectName: "featureNameSearchField"
            Layout.fillWidth: true
            visible: controlsFlickable.inspectionMode === "feature"
            placeholderText: "Search name…"
            text: controlsFlickable.nameQuery
            onTextChanged: controlsFlickable.nameQuery = text
        }
        TextField {
            id: featureMzSearchField
            objectName: "featureMzSearchField"
            Layout.fillWidth: true
            visible: controlsFlickable.inspectionMode === "feature"
            placeholderText: "Search m/z…"
            text: controlsFlickable.mzQuery
            onTextChanged: controlsFlickable.mzQuery = text
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
        // Same always-reserve-the-space wrapper as AnnotationsSection.qml's
        // panels use (see its own comment on the same pattern) — an
        // invisible item claims zero space in QtQuick.Layouts regardless
        // of fill properties, so swapping which sibling is visible here
        // used to reflow whatever's below. A fixed preferredHeight (not
        // fillHeight) matches how this row already behaved before —
        // neither the label nor the combo ever stretched to fill leftover
        // space, they just took their own natural height.
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: featureCombo.implicitHeight
            visible: controlsFlickable.inspectionMode === "feature"

            Label {
                objectName: "featureSearchEmptyLabel"
                visible: controlsFlickable.inspectionMode === "feature"
                         && controlsFlickable.features.length > 0
                         && controlsFlickable.sortedFeatures.length === 0
                text: "No features match your search."
                color: Theme.mutedTextColor
                wrapMode: Text.Wrap
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width
            }
            ComboBox {
                id: featureCombo
                objectName: "featureCombo"
                anchors.fill: parent
                // Kept explicit here (not just on the wrapper above) —
                // every other control in this panel manages its own
                // visibility directly rather than delegating to a parent,
                // and this keeps `featureCombo.visible` itself reporting
                // false in obs mode (not just "not actually on screen
                // because its parent is hidden"), which existing callers
                // already read it as meaning.
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
                // Friendlier label than the raw layer key: "TIC" for a
                // TIC-normalized analysis, "m/z 154.0863" for a
                // reference-m/z-normalized one — dataLayer's actual value
                // (normalizedLayerName) still matches the real layer key.
                text: controlsFlickable.normalizedLayerName === "TIC"
                      ? "TIC" : "m/z " + controlsFlickable.normalizedLayerName.replace("mz_", "")
                highlighted: controlsFlickable.dataLayer === controlsFlickable.normalizedLayerName
                onClicked: controlsFlickable.dataLayer = controlsFlickable.normalizedLayerName
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
            Button {
                id: localMoranLayerButton
                objectName: "localMoranLayerButton"
                text: "Local Moran's I"
                // "localMoran" is a reserved dataLayer value recognized by
                // render_heatmap_by_target (core/plotting/heatmap.py) —
                // see that dispatch and core/spatial/local_moran.py for
                // the actual per-pixel computation. Fits here as a third
                // dataLayer option (not a third inspectionMode) since it's
                // still scored against whichever feature is already
                // selected, same as raw/TIC.
                highlighted: controlsFlickable.dataLayer === "localMoran"
                onClicked: controlsFlickable.dataLayer = "localMoran"
                // A per-pixel view of spatial clustering, unlike the
                // single per-sample score shown elsewhere (e.g. next to
                // each sample's name) — see the user guide's "Spatial
                // autocorrelation" section for the full explanation.
                ToolTip.visible: hovered
                ToolTip.text: "Per-pixel spatial autocorrelation: where a "
                    + "feature clusters within the tissue, not just whether "
                    + "it does overall. See the user guide for details."
                background: Rectangle {
                    radius: 4
                    color: localMoranLayerButton.highlighted ? palette.highlight : palette.button
                    border.color: palette.mid
                }
                contentItem: Label {
                    text: localMoranLayerButton.text
                    color: localMoranLayerButton.highlighted ? palette.highlightedText : palette.buttonText
                    font.bold: localMoranLayerButton.highlighted
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                HoverHandler {
                    objectName: "localMoranLayerButtonHover"
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
            // "coolwarm" is the one diverging option — local Moran's I
            // values are signed (positive: clustering; negative: spatial
            // outlier), unlike every other option here, which are all
            // sequential and only make sense for a one-sided intensity
            // scale.
            model: ["viridis", "magma", "inferno", "plasma", "cividis", "gray", "coolwarm"]
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
                // Local Moran's I has no cheap real range to show here
                // (see refreshAutoRange's own comment) — literal "auto"
                // rather than a real-looking but wrong number.
                text: "vmin: " + (controlsFlickable.autoScale
                                   ? (controlsFlickable.dataLayer === "localMoran"
                                      ? "auto" : controlsFlickable.autoRange.vmin.toFixed(3))
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
                                   ? (controlsFlickable.dataLayer === "localMoran"
                                      ? "auto" : controlsFlickable.autoRange.vmax.toFixed(3))
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

        // The colorbar legend — literal "min"/"max" labels, not the
        // numeric bounds (those are already shown above by
        // vminValueLabel/vmaxValueLabel). Same colormap resolution as the
        // tiles themselves (image://heatmap/colorbar|<colormap>, backed by
        // render_colorbar — the exact matplotlib.colormaps[...] call
        // render_feature_heatmap/render_obs_heatmap use for the tiles),
        // so the legend always matches what's actually on screen.
        RowLayout {
            Layout.fillWidth: true
            visible: controlsFlickable.showsColorScale
            spacing: 6

            Label { text: "min"; color: Theme.mutedTextColor }
            Image {
                objectName: "colorbarLegendImage"
                Layout.fillWidth: true
                Layout.preferredHeight: 14
                fillMode: Image.Stretch
                cache: false
                source: "image://heatmap/colorbar|" + controlsFlickable.colormap
            }
            Label { text: "max"; color: Theme.mutedTextColor }
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

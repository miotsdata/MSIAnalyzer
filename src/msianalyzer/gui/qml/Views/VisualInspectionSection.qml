import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: visualSection
    objectName: "visualSection"

    property var analysis
    property var features: (analysis && analysis.analysisDbPath)
                            ? AnalysisBridge.getFeatureList(analysis.analysisDbPath) : []
    property var samples: (analysis && analysis.analysisDbPath)
                           ? AnalysisBridge.getSamples(analysis.analysisDbPath) : []

    // "mz" (the bridge's own order — already ascending) or "name" (every
    // annotated feature alphabetically by compound, then every
    // unannotated feature by m/z, at the end).
    property string sortMode: "mz"

    property var sortedFeatures: {
        var feats = visualSection.features.slice()
        if (visualSection.sortMode === "name") {
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
    property var featureLabels: visualSection.sortedFeatures.map(function (f) {
        return f.compound_name
               ? (Number(f.mz).toFixed(4) + ": " + f.compound_name)
               : ("m/z " + Number(f.mz).toFixed(4))
    })
    property int selectedFeatureIndex: 0
    property var selectedFeature: (visualSection.sortedFeatures.length > visualSection.selectedFeatureIndex)
                                   ? visualSection.sortedFeatures[visualSection.selectedFeatureIndex] : null

    property int gridRows: 1
    property int gridCols: Math.max(1, Math.min(4, visualSection.samples.length))
    property string colormap: "viridis"
    // vmin/vmax as actually *applied* to rendering (what vminToken/
    // vmaxToken below read) — separate from the slider/field's draft
    // values so dragging the slider doesn't re-render every tile on
    // every intermediate tick. draftVmin/Max start out tracking vmin/vmax
    // declaratively; the first slider drag or text edit breaks that
    // binding for the rest of the session (normal QML "last assignment
    // wins"), which is fine here since nothing else ever changes vmin/
    // vmax except applyColorRange() copying the draft into it.
    // 10, not the raw-intensity-scale 1000 the layer toggle's other
    // option (raw) might call for — TIC (the default dataLayer) is
    // log1p-transformed, so even very high raw intensities land well
    // under this. 1000 as a starting *slider range* on log1p-scale data
    // made the whole useful range (roughly 0-15) collapse into a sliver
    // of the slider, effectively uncontrollable ("the numbers are too
    // big" — reported as such).
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
    // range from — TIC (log1p-transformed) and raw live on completely
    // different scales, so a fixed guess (or a value carried over from
    // the other layer) was either useless or, for raw, off by orders of
    // magnitude (reported: vmin/vmax "remain with the value of tic"
    // after switching to raw, and reaching a useful raw-scale value by
    // hand took many Apply clicks to double its way there).
    property var autoRange: ({"vmin": 0, "vmax": 1})

    function visibleSampleNames() {
        return visualSection.samples
            .filter(function (s) { return !visualSection.isHidden(s.name) })
            .map(function (s) { return s.name })
    }

    function refreshAutoRange() {
        if (!analysis || !analysis.analysisDbPath || !visualSection.selectedFeature) {
            visualSection.autoRange = {"vmin": 0, "vmax": 1}
            return
        }
        var names = visualSection.visibleSampleNames()
        if (names.length === 0) {
            visualSection.autoRange = {"vmin": 0, "vmax": 1}
            return
        }
        visualSection.autoRange = AnalysisBridge.getFeatureValueRange(
            names, visualSection.selectedFeature.mz, visualSection.dataLayer)
    }

    // Switching layers changes the data's whole scale — falling back to
    // autoscale re-renders correctly for the new layer immediately;
    // turning autoscale back off afterward starts from that layer's own
    // real range (autoRange) rather than carrying over a stale one.
    onDataLayerChanged: {
        visualSection.autoScale = true
        visualSection.refreshAutoRange()
    }
    onSelectedFeatureChanged: visualSection.refreshAutoRange()
    onHiddenSamplesChanged: visualSection.refreshAutoRange()

    function isHidden(name) {
        return !!visualSection.hiddenSamples[name]
    }

    function toggleSample(name) {
        var copy = Object.assign({}, visualSection.hiddenSamples)
        copy[name] = !copy[name]
        visualSection.hiddenSamples = copy
    }

    // Commits the slider/field's draft vmin/vmax to the applied values
    // every tile actually renders with — a deliberate action rather than
    // every drag tick re-rendering every visible tile.
    function applyColorRange() {
        visualSection.vmin = visualSection.draftVmin
        visualSection.vmax = visualSection.draftVmax
    }

    // Every tile shares this vmin/vmax unless autoscale is on, in which
    // case each tile scales independently to its own data.
    function vminToken() {
        return visualSection.autoScale ? "auto" : String(visualSection.vmin)
    }
    function vmaxToken() {
        return visualSection.autoScale ? "auto" : String(visualSection.vmax)
    }

    onAnalysisChanged: {
        if (analysis && analysis.analysisDbPath) {
            AnalysisBridge.setHeatmapAnalysis(analysis.analysisDbPath)
            visualSection.refreshAutoRange()
        }
    }

    Text {
        objectName: "visualEmptyStateLabel"
        visible: visualSection.features.length === 0
        anchors.centerIn: parent
        text: "No features to display."
        color: "gray"
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        visible: visualSection.features.length > 0
        spacing: 12

        Flickable {
            id: controlsFlickable
            objectName: "controlsFlickable"
            // Widened from 260 — the vmin/vmax slider+field rows were
            // fitting with ~0px to spare, and reportedly clipping in
            // practice (font-metric/rendering differences from this
            // sandbox's offscreen-platform measurements, most likely).
            Layout.preferredWidth: 300
            Layout.minimumWidth: 300
            Layout.maximumWidth: 300
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: controlsPanel.implicitHeight
            boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: controlsPanel
            objectName: "controlsPanel"
            width: controlsFlickable.width
            spacing: 10

            Text { text: "Feature"; font.bold: true }
            RowLayout {
                Text { text: "Sort by" }
                ComboBox {
                    id: sortModeCombo
                    objectName: "sortModeCombo"
                    Layout.fillWidth: true
                    model: ["m/z", "Name"]
                    currentIndex: visualSection.sortMode === "name" ? 1 : 0
                    onActivated: (index) => {
                        visualSection.sortMode = index === 1 ? "name" : "mz"
                        // The old index likely points at a different
                        // feature once the order changes.
                        visualSection.selectedFeatureIndex = 0
                    }
                }
            }
            ComboBox {
                id: featureCombo
                objectName: "featureCombo"
                Layout.fillWidth: true
                model: visualSection.featureLabels
                currentIndex: visualSection.selectedFeatureIndex
                onActivated: (index) => visualSection.selectedFeatureIndex = index
            }

            Text { text: "Layer"; font.bold: true }
            RowLayout {
                // Plain (non-checkable) Buttons rather than RadioButton +
                // ButtonGroup: a `checked: expr` binding on *each* of several
                // grouped buttons, combined with an `onToggled` handler that
                // writes back into the very property the binding reads,
                // raced against ButtonGroup's own exclusivity bookkeeping
                // and intermittently hung the QML engine (reproduced via
                // bisection — the two-radio-button version hung roughly 1
                // run in 3). `highlighted` here is a one-directional
                // binding and `onClicked` a discrete user action, so there's
                // no property/signal cycle to race.
                //
                // Selected state is drawn explicitly (background/text
                // color) rather than relying on `highlighted`'s own
                // rendering — reported as "not so clear which one is
                // selected" under Fusion's fairly subtle highlighted look.
                Button {
                    id: rawLayerButton
                    objectName: "rawLayerButton"
                    text: "Raw"
                    highlighted: visualSection.dataLayer === "raw"
                    onClicked: visualSection.dataLayer = "raw"
                    background: Rectangle {
                        radius: 4
                        color: rawLayerButton.highlighted ? "#0078d4" : "#e6e6e6"
                        border.color: "#a0a0a0"
                    }
                    contentItem: Text {
                        text: rawLayerButton.text
                        color: rawLayerButton.highlighted ? "#ffffff" : "#202020"
                        font.bold: rawLayerButton.highlighted
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                Button {
                    id: ticLayerButton
                    objectName: "ticLayerButton"
                    text: "TIC"
                    highlighted: visualSection.dataLayer === "TIC"
                    onClicked: visualSection.dataLayer = "TIC"
                    background: Rectangle {
                        radius: 4
                        color: ticLayerButton.highlighted ? "#0078d4" : "#e6e6e6"
                        border.color: "#a0a0a0"
                    }
                    contentItem: Text {
                        text: ticLayerButton.text
                        color: ticLayerButton.highlighted ? "#ffffff" : "#202020"
                        font.bold: ticLayerButton.highlighted
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }

            Text { text: "Colormap"; font.bold: true }
            ComboBox {
                id: colormapCombo
                objectName: "colormapCombo"
                Layout.fillWidth: true
                model: ["viridis", "magma", "inferno", "plasma", "cividis", "gray"]
                currentIndex: model.indexOf(visualSection.colormap)
                onActivated: (index) => visualSection.colormap = model[index]
            }

            Text { text: "Color scale"; font.bold: true }
            CheckBox {
                id: autoScaleCheckBox
                objectName: "autoScaleCheckBox"
                text: "Autoscale"
                checked: visualSection.autoScale
                onToggled: {
                    visualSection.autoScale = checked
                    if (!checked) {
                        // Just turned off — start the manual range from
                        // what autoscale was actually using for this
                        // feature+layer, not a stale/arbitrary default.
                        visualSection.vmin = visualSection.autoRange.vmin
                        visualSection.vmax = visualSection.autoRange.vmax
                        visualSection.draftVmin = visualSection.autoRange.vmin
                        visualSection.draftVmax = visualSection.autoRange.vmax
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    objectName: "vminValueLabel"
                    // Shows what autoscale is using while it's on (so
                    // it's not just a mystery), the manual draft while off.
                    text: "vmin: " + (visualSection.autoScale
                                       ? visualSection.autoRange.vmin.toFixed(3)
                                       : visualSection.draftVmin.toFixed(3))
                }
                RowLayout {
                    enabled: !visualSection.autoScale
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
                        // Autoscale's own vmax for this feature+layer —
                        // a real, data-driven ceiling instead of a fixed
                        // guess or a slowly-doubling one (reported: raw
                        // intensities are orders of magnitude past TIC's
                        // log1p scale, and doubling the old fixed default
                        // per Apply click took many rounds to get close).
                        to: Math.max(1, visualSection.autoRange.vmax)
                        value: visualSection.draftVmin
                        onMoved: visualSection.draftVmin = value
                    }
                    TextField {
                        id: vminField
                        objectName: "vminField"
                        Layout.preferredWidth: 80
                        text: visualSection.draftVmin.toFixed(3)
                        onEditingFinished: {
                            var v = parseFloat(text)
                            if (!isNaN(v)) visualSection.draftVmin = v
                        }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    objectName: "vmaxValueLabel"
                    text: "vmax: " + (visualSection.autoScale
                                       ? visualSection.autoRange.vmax.toFixed(3)
                                       : visualSection.draftVmax.toFixed(3))
                }
                RowLayout {
                    enabled: !visualSection.autoScale
                    opacity: enabled ? 1.0 : 0.4
                    Slider {
                        id: vmaxSlider
                        objectName: "vmaxSlider"
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, visualSection.autoRange.vmax)
                        value: visualSection.draftVmax
                        onMoved: visualSection.draftVmax = value
                    }
                    TextField {
                        id: vmaxField
                        objectName: "vmaxField"
                        Layout.preferredWidth: 80
                        text: visualSection.draftVmax.toFixed(3)
                        onEditingFinished: {
                            var v = parseFloat(text)
                            if (!isNaN(v)) visualSection.draftVmax = v
                        }
                    }
                }
            }
            Button {
                id: applyColorRangeButton
                objectName: "applyColorRangeButton"
                text: "Apply color range"
                Layout.fillWidth: true
                enabled: !visualSection.autoScale
                       && (visualSection.draftVmin !== visualSection.vmin
                           || visualSection.draftVmax !== visualSection.vmax)
                onClicked: visualSection.applyColorRange()
            }

            // Rows/cols picking is disabled for now — the grid was too
            // crowded with multiple columns of full-size tiles, so tiles
            // are laid out one per row (fixed) at a smaller, fixed height
            // instead, with the list itself scrolling for more samples.
            // Kept (hidden, not deleted) for when a real "how many
            // columns" control returns alongside per-tile sizing that
            // isn't just "fill whatever space division the grid gives it".

            Text { text: "Samples"; font.bold: true }
            // No nested Flickable here (unlike the equivalent samples list
            // elsewhere in this codebase) — this whole panel already
            // scrolls via `controlsFlickable`, and a Flickable inside
            // another Flickable's scrolling content fights it for drag
            // gestures.
            Repeater {
                id: sampleVisibilityRepeater
                objectName: "sampleVisibilityRepeater"
                model: visualSection.samples

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
                    // an unbounded oscillation (confirmed by
                    // catching a hung run mid-`setSource`, see the
                    // sibling comment on the layer Buttons above).
                    // This checkbox is the sole mutator of its own
                    // sample's hidden state, so a one-time initial
                    // value plus forward-only click handling is
                    // both sufficient and cycle-free.
                    Component.onCompleted: checked = !visualSection.isHidden(modelData.name)
                    onToggled: visualSection.toggleSample(modelData.name)
                }
            }
        }
        }

        Flickable {
            id: gridFlickable
            objectName: "heatmapGridFlickable"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: Math.max(width, heatmapGrid.implicitWidth)
            contentHeight: Math.max(height, heatmapGrid.implicitHeight)
            boundsBehavior: Flickable.StopAtBounds

            GridLayout {
                id: heatmapGrid
                objectName: "heatmapGrid"
                // Fixed at 1 column (rows/cols picking disabled for now,
                // see the controls panel) — one tile per row, fixed size,
                // the Flickable above scrolls for as many samples as
                // there are, rather than every tile stretching to fill
                // whatever gridRows/gridCols divided the space into.
                columns: 1
                columnSpacing: 8
                rowSpacing: 8

                Repeater {
                    id: heatmapTilesRepeater
                    objectName: "heatmapTilesRepeater"
                    model: visualSection.samples.filter(function (s) {
                        return !visualSection.isHidden(s.name)
                    })

                    delegate: ColumnLayout {
                        objectName: "heatmapTile_" + modelData.name
                        // 80% of the available width, not the full
                        // width — tiles don't need to span edge-to-edge.
                        Layout.preferredWidth: gridFlickable.width * 0.8
                        Layout.preferredHeight: 360

                        Text {
                            objectName: "heatmapTileLabel_" + modelData.name
                            text: modelData.name
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                        }

                        ZoomableImage {
                            objectName: "heatmapImage_" + modelData.name
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            source: visualSection.selectedFeature
                                    ? ("image://heatmap/" + modelData.name + "|"
                                       + visualSection.selectedFeature.mz + "|"
                                       + visualSection.dataLayer + "|"
                                       + visualSection.colormap + "|"
                                       + visualSection.vminToken() + "|"
                                       + visualSection.vmaxToken())
                                    : ""
                        }
                    }
                }
            }
        }
    }
}

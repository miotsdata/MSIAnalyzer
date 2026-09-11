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
    property real vmin: 0
    property real vmax: 1000
    property bool autoScale: true
    property bool globalScale: true
    property string dataLayer: "TIC"
    property var hiddenSamples: ({})

    function isHidden(name) {
        return !!visualSection.hiddenSamples[name]
    }

    function toggleSample(name) {
        var copy = Object.assign({}, visualSection.hiddenSamples)
        copy[name] = !copy[name]
        visualSection.hiddenSamples = copy
    }

    // Every tile shares this token pair unless "per-sample" scaling is
    // chosen, in which case each tile autoscales independently to its own
    // data rather than a shared range.
    function vminToken() {
        return (visualSection.globalScale && !visualSection.autoScale) ? String(visualSection.vmin) : "auto"
    }
    function vmaxToken() {
        return (visualSection.globalScale && !visualSection.autoScale) ? String(visualSection.vmax) : "auto"
    }

    onAnalysisChanged: {
        if (analysis && analysis.analysisDbPath) {
            AnalysisBridge.setHeatmapAnalysis(analysis.analysisDbPath)
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
            Layout.preferredWidth: 260
            Layout.minimumWidth: 260
            Layout.maximumWidth: 260
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
                Button {
                    id: rawLayerButton
                    objectName: "rawLayerButton"
                    text: "Raw"
                    highlighted: visualSection.dataLayer === "raw"
                    onClicked: visualSection.dataLayer = "raw"
                }
                Button {
                    id: ticLayerButton
                    objectName: "ticLayerButton"
                    text: "TIC"
                    highlighted: visualSection.dataLayer === "TIC"
                    onClicked: visualSection.dataLayer = "TIC"
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
                onToggled: visualSection.autoScale = checked
            }
            CheckBox {
                id: globalScaleCheckBox
                objectName: "globalScaleCheckBox"
                text: "Global (vs. per-sample) scale"
                checked: visualSection.globalScale
                enabled: !visualSection.autoScale
                onToggled: visualSection.globalScale = checked
            }
            RowLayout {
                enabled: !visualSection.autoScale
                Text { text: "vmin" }
                Slider {
                    id: vminSlider
                    objectName: "vminSlider"
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, visualSection.vmax)
                    value: visualSection.vmin
                    onMoved: visualSection.vmin = value
                }
                TextField {
                    id: vminField
                    objectName: "vminField"
                    Layout.preferredWidth: 70
                    text: visualSection.vmin.toFixed(2)
                    onEditingFinished: {
                        var v = parseFloat(text)
                        if (!isNaN(v)) visualSection.vmin = v
                    }
                }
            }
            RowLayout {
                enabled: !visualSection.autoScale
                Text { text: "vmax" }
                Slider {
                    id: vmaxSlider
                    objectName: "vmaxSlider"
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, visualSection.vmax * 2)
                    value: visualSection.vmax
                    onMoved: visualSection.vmax = value
                }
                TextField {
                    id: vmaxField
                    objectName: "vmaxField"
                    Layout.preferredWidth: 70
                    text: visualSection.vmax.toFixed(2)
                    onEditingFinished: {
                        var v = parseFloat(text)
                        if (!isNaN(v)) visualSection.vmax = v
                    }
                }
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
                    text: modelData.name
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
                        Layout.preferredWidth: gridFlickable.width
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

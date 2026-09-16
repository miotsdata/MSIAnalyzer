import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

Item {
    id: visualSection
    objectName: "visualSection"

    property var analysis
    property var samples: (analysis && analysis.analysisDbPath)
                           ? AnalysisBridge.getSamples(analysis.analysisDbPath) : []

    property int gridRows: 1
    property int gridCols: Math.max(1, Math.min(4, visualSection.samples.length))

    // Every ROI drawn on a sample, only fetched/refreshed while "Show
    // ROIs" is on — { sampleName: [{"name","color","vertices"}, ...] }.
    property bool showRois: false
    property var sampleRoisCache: ({})
    function refreshSampleRois() {
        if (!visualSection.showRois || !analysis || !analysis.analysisDbPath) {
            visualSection.sampleRoisCache = {}
            return
        }
        var cache = {}
        visualSection.samples.forEach(function (s) {
            cache[s.name] = AnalysisBridge.getSampleRois(analysis.analysisDbPath, s.name)
        })
        visualSection.sampleRoisCache = cache
    }
    onShowRoisChanged: visualSection.refreshSampleRois()

    onAnalysisChanged: {
        if (analysis && analysis.analysisDbPath) {
            AnalysisBridge.setHeatmapAnalysis(analysis.analysisDbPath)
            controls.refresh()
            visualSection.refreshSampleRois()
        }
    }

    // Set by AnalysisPage right after (re-)constructing this section for
    // an "Inspect visually" jump from Annotations — see
    // AnalysisPage.qml's visualLoader.onLoaded. NaN (the default) means
    // no pending request. A one-shot handoff, not persistent state:
    // pendingInspectHandled tells the owner to clear it once applied, so
    // a later plain tab switch to Visual Inspection doesn't re-apply it.
    property real pendingInspectMz: NaN
    signal pendingInspectHandled()

    onPendingInspectMzChanged: {
        if (isNaN(visualSection.pendingInspectMz)) return
        controls.inspectionMode = "feature"
        var idx = controls.sortedFeatures.findIndex(function (f) {
            return f.mz === visualSection.pendingInspectMz
        })
        if (idx >= 0) controls.selectedFeatureIndex = idx
        visualSection.pendingInspectHandled()
    }

    Label {
        objectName: "visualEmptyStateLabel"
        visible: controls.features.length === 0 && controls.obsColumns.length === 0
        anchors.centerIn: parent
        text: "No features to display."
        color: Theme.mutedTextColor
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        visible: controls.features.length > 0 || controls.obsColumns.length > 0
        spacing: 8

        RowLayout {
            Layout.fillWidth: true

            Button {
                id: openRoiDesignButton
                objectName: "openRoiDesignButton"
                text: "Draw ROI"
                onClicked: {
                    roiDesignWindowLoader.active = true
                    var win = roiDesignWindowLoader.item
                    // openFor() reuses the same window across clicks (one
                    // ROI design window at a time), resetting to the first
                    // sample even if it was already open. `controls` is
                    // the same HeatmapControlsPanel instance already
                    // driving this page's grid, not a duplicate — the ROI
                    // window has no feature/obs/colormap controls of its
                    // own, it renders with whatever is already selected here.
                    win.openFor(visualSection.analysis, visualSection.samples, controls)
                }
                HoverHandler {
                    objectName: "openRoiDesignButtonHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            CheckBox {
                id: showRoisCheckBox
                objectName: "showRoisCheckBox"
                text: "Show ROIs"
                checked: visualSection.showRois
                onToggled: visualSection.showRois = checked
                HoverHandler {
                    objectName: "showRoisCheckBoxHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Item { Layout.fillWidth: true }
        }

        // Lazy — see mirrorPlotDetailWindowLoader in AnnotationsSection.qml
        // for why: a WebEngineView inside MirrorPlotDetailWindow spins up
        // its Chromium renderer as soon as it's constructed, regardless of
        // the owning Window's own `visible`. RoiDesignWindow has no
        // WebEngineView, but the same lazy pattern is kept for consistency
        // and so opening the Visual Inspection tab never eagerly builds a
        // second top-level window nobody asked for. Not a Layout item
        // itself (Loader renders nothing here) — kept out of the RowLayout
        // below so it can't affect that row's sizing.
        Loader {
            id: roiDesignWindowLoader
            objectName: "roiDesignWindowLoader"
            active: false
            sourceComponent: RoiDesignWindow {
                objectName: "roiDesignWindow"
            }
            onLoaded: {
                // Picks up whatever was saved/deleted during the session
                // once the window closes, and any time "Show ROIs" is
                // toggled on.
                item.visibleChanged.connect(function () {
                    if (!item.visible) visualSection.refreshSampleRois()
                })
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            HeatmapControlsPanel {
                id: controls
                analysis: visualSection.analysis
                samples: visualSection.samples
                showSampleVisibility: true
            }

            Flickable {
                id: gridFlickable
                objectName: "heatmapGridFlickable"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                // No horizontal scrolling wanted — tiles fit within the
                // viewport at 80% width (see the delegate below), never
                // wider — so contentWidth is simply this Flickable's own
                // width, not `Math.max(width, heatmapGrid.implicitWidth)`:
                // that form makes contentWidth (and, transitively, the
                // auto-reparented content item's own width) depend on
                // heatmapGrid's implicitWidth, which in turn depends on
                // its `Layout.fillWidth: true` children wanting to fill
                // *that* width — circular, and in practice never settled
                // to a real value (children stuck at width 0).
                contentWidth: width
                // heatmapGrid.height is explicit now (see its own
                // comment), not implicit — that's the real total content
                // height to scroll across.
                contentHeight: Math.max(height, heatmapGrid.height)
                boundsBehavior: Flickable.StopAtBounds

                // A plain Item, not a `Column`/`ColumnLayout`/
                // `GridLayout` positioner — every positioner/layout type
                // tried here reproducibly failed to actually stack these
                // Repeater-created delegates vertically in practice
                // (Layout.preferredWidth/fillWidth never landed on the
                // real `width`; `Column`'s own automatic y-stacking never
                // moved a single delegate off y=0, independent of anchors,
                // explicit x, or the delegate's root type — all confirmed
                // by direct inspection, not guessed) — likely some
                // interaction specific to this Flickable's own
                // auto-reparenting of its content into an internal
                // contentItem. Each tile's own y is computed explicitly
                // instead, from the Repeater's `index`; nothing here reads
                // heatmapGrid's own size for positioning, so a plain Item
                // (no bottom-up implicit-size computation at all) is fine.
                Item {
                    id: heatmapGrid
                    objectName: "heatmapGrid"
                    // A direct, non-circular reference to the Flickable's
                    // own width (see contentWidth's comment above for why
                    // this must not go through contentWidth/contentItem).
                    width: gridFlickable.width
                    readonly property int tileHeight: 360
                    readonly property int tileSpacing: 8
                    height: heatmapTilesRepeater.count * (tileHeight + tileSpacing)

                    Repeater {
                        id: heatmapTilesRepeater
                        objectName: "heatmapTilesRepeater"
                        model: visualSection.samples.filter(function (s) {
                            return !controls.isHidden(s.name)
                        })

                        delegate: Item {
                            objectName: "heatmapTile_" + modelData.name
                            x: (heatmapGrid.width - width) / 2
                            y: index * (heatmapGrid.tileHeight + heatmapGrid.tileSpacing)
                            // 80% of the available width, not the full
                            // width — tiles don't need to span edge-to-edge.
                            width: heatmapGrid.width * 0.8
                            height: heatmapGrid.tileHeight

                            ColumnLayout {
                                anchors.fill: parent

                                Label {
                                    objectName: "heatmapTileLabel_" + modelData.name
                                    text: modelData.name
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignHCenter
                                }

                                ZoomableImage {
                                    objectName: "heatmapImage_" + modelData.name
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    source: controls.tileSource(modelData.name)

                                    RoiOverlay {
                                        objectName: "roiOverlay_" + modelData.name
                                        anchors.fill: parent
                                        readOnly: true
                                        visible: visualSection.showRois
                                        effectiveScale: parent.effectiveScale
                                        savedRois: visualSection.sampleRoisCache[modelData.name] || []
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

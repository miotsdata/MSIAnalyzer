import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

Page {
    id: analysisPage
    objectName: "analysisPage"

    property var analysis
    // A targeted analysis has no MS2/alignment/feature-discovery output —
    // Summary/MS1 Spectra/Annotations/Visual Inspection/Spatial
    // Correlation all assume that machinery, so this workspace shows only
    // the Calibration page for one (per user feedback: "IT IS A TOTAL
    // DIFFERENT ANALYSES ... the only thing that should be present is the
    // page of the calibration curve"), not a 5th tab alongside the rest.
    readonly property bool isTargeted: analysisPage.analysis
                                        && analysisPage.analysis.analysisType === "targeted"
    // The run's output folder isn't on this machine (moved, deleted, or a
    // copy of the project without its output/). Every section would read
    // missing files, so the page shows only a message instead.
    readonly property bool outputMissing: !!analysisPage.analysis
                                          && analysisPage.analysis.outDirMissing
    // `analysis` can still be unset at Component.onCompleted time for a
    // page reached via StackView.push({"analysis": ...}) — same timing
    // quirk noted on summaryLoader's own Qt.binding below — so this reacts
    // to the property actually landing rather than assuming it's already
    // there. Brings an analysis run before a schema change (e.g. ADR 33's
    // index) up to date the moment its workspace is opened, without
    // needing a full re-run; safe/idempotent, see
    // AnalysisBridge.ensureSchemaCurrent's own docstring.
    // What Annotations/Visual Inspection were showing when last left
    // (their stateSaved), handed back when the tab is rebuilt — each
    // section is a lazy Loader, destroyed on every tab switch.
    property var annotationsState: null
    property var visualState: null

    onAnalysisChanged: {
        analysisPage.annotationsState = null
        analysisPage.visualState = null
        if (analysisPage.analysis && analysisPage.analysis.analysisDbPath
                && !analysisPage.outputMissing) {
            AnalysisBridge.ensureSchemaCurrent(analysisPage.analysis.analysisDbPath)
            // Every workspace opens on the pipeline output, never on an
            // imported dataset (ADR 49) left active from a previous visit.
            AnalysisBridge.setActiveDataset(analysisPage.analysis.analysisDbPath, -1)
            analysisPage.refreshImportedDatasets()
            // Warm up background-thread fetches other tabs will almost
            // certainly need, right now while the user is already looking
            // at a "loading" state (opening the analysis), instead of
            // paying that cost again the moment they switch tabs (each
            // section is a lazy Loader, destroyed/rebuilt every tab switch
            // — see AnalysisBridge.requestFeatureList's and
            // getSpatialCorrelationModel's own docstrings). Both cache by
            // analysis_db_path on the bridge itself, so calling them again
            // later from those sections' own onAnalysisChanged is a cache
            // hit, not a duplicate fetch. Fire-and-forget: no listener
            // needed here, the cache population is a side effect of the
            // bridge methods themselves.
            //
            // requestFeatureList matters for a targeted analysis too now
            // (it's the Calibration workspace's analyte picker); Spatial
            // Correlation never runs for one, so its own model is skipped.
            AnalysisBridge.requestFeatureList(analysisPage.analysis.analysisDbPath)
            if (!analysisPage.isTargeted) {
                AnalysisBridge.getSpatialCorrelationModel(analysisPage.analysis.analysisDbPath)
            }
        }
    }
    // Imported analyzed .h5ad datasets of this analysis (ADR 49) — the
    // header's "Dataset" switcher lists them after "Pipeline output".
    property var importedDatasets: []

    function refreshImportedDatasets() {
        analysisPage.importedDatasets = (analysisPage.analysis && analysisPage.analysis.analysisDbPath)
            ? AnalysisBridge.getImportedDatasets(analysisPage.analysis.analysisDbPath) : []
    }

    Connections {
        target: AnalysisBridge
        function onImportedDatasetsChanged(dbPath) {
            if (analysisPage.analysis && dbPath === analysisPage.analysis.analysisDbPath)
                analysisPage.refreshImportedDatasets()
        }
        // Same eager warm-up as onAnalysisChanged above, for the newly
        // selected dataset. Both bridge caches are keyed per dataset.
        function onActiveDatasetChanged() {
            if (!analysisPage.analysis || !analysisPage.analysis.analysisDbPath
                    || analysisPage.isTargeted)
                return
            AnalysisBridge.requestFeatureList(analysisPage.analysis.analysisDbPath)
            AnalysisBridge.getSpatialCorrelationModel(analysisPage.analysis.analysisDbPath)
            // Switching while Visual Inspection is on screen: go through
            // the same background prefetch (and loading overlay) as
            // opening the tab. Otherwise every tile of the new dataset
            // would load its .h5ad on the GUI thread (see
            // HeatmapPrefetchWorker).
            if (sectionStack.currentIndex === 3)
                analysisPage.goToVisualInspection()
        }
    }

    // Set by Annotations' "Inspect visually" right-click just before
    // switching to the Visual Inspection tab (see annotationsLoader.onLoaded
    // below) — a one-shot handoff, not persistent workspace state: cleared
    // back to NaN via visualLoader's pendingInspectHandled once applied, so
    // a later plain click on the Visual Inspection nav button doesn't
    // re-apply a stale feature selection from a previous "Inspect visually".
    property real pendingInspectMz: NaN

    // Same one-shot handoff, for Spatial Correlation's *local* summary
    // table's "Show spatial distribution" — "" means "leave dataLayer
    // alone" (every other entry point above). Cleared back to "" the same
    // way pendingInspectMz is, via pendingInspectHandled.
    property string pendingInspectLayer: ""

    // Set while waiting on AnalysisBridge.prefetchHeatmapSamples (see
    // goToVisualInspection below) — true from the moment a switch to
    // Visual Inspection is requested until heatmapPrefetchReady fires,
    // during which sectionStack.currentIndex deliberately has NOT been
    // changed yet. Folded into currentSectionReady so the existing
    // sectionLoadingOverlay covers this waiting period too, instead of
    // needing a second, separate spinner.
    property bool switchingToVisual: false

    // Every entry point that lands on Visual Inspection (the plain nav
    // button, Annotations' "Inspect visually", Spatial Correlation's
    // "Show spatial distribution") funnels through here instead of
    // setting sectionStack.currentIndex directly — see
    // HeatmapPrefetchWorker's own docstring for the freeze this avoids.
    // `pendingMz` is optional (the plain nav button passes none).
    // `pendingLayer` is optional too — only the local summary table's
    // handoff passes one ("localMoran"), to also switch the heatmap layer
    // once the feature is selected, not just pick the feature itself.
    function goToVisualInspection(pendingMz, pendingLayer) {
        if (pendingMz !== undefined) analysisPage.pendingInspectMz = pendingMz
        if (pendingLayer !== undefined) analysisPage.pendingInspectLayer = pendingLayer
        if (!analysisPage.analysis || !analysisPage.analysis.analysisDbPath) {
            sectionStack.currentIndex = 3
            return
        }
        // Every sample: the grid is a scrollable column of all of them, and
        // the provider's cache is sized to hold them all (ADR 50).
        var sampleNames = AnalysisBridge.getInspectionSamples(analysisPage.analysis.analysisDbPath)
                                        .map(function (s) { return s.name })
        analysisPage.switchingToVisual = true
        AnalysisBridge.prefetchHeatmapSamples(analysisPage.analysis.analysisDbPath, sampleNames)
    }

    Connections {
        target: AnalysisBridge
        function onHeatmapPrefetchReady() {
            if (!analysisPage.switchingToVisual) return  // stale/unrelated call
            analysisPage.switchingToVisual = false
            sectionStack.currentIndex = 3
        }
    }

    // Export > Image… (Main.qml) needs Visual Inspection's own live
    // control values, but that section is a lazily-active Loader
    // (visualLoader below) — only actually instantiated while its tab is
    // the current one. Forwarded up here (and again from `window` in
    // Main.qml via `stackView.currentItem`) so the menu item can both
    // gate itself on "is there anything live to read right now" and
    // read it, without Main.qml reaching three files deep on its own.
    readonly property bool visualInspectionActive: sectionStack.currentIndex === 3
                                                     && visualLoader.status === Loader.Ready
    property var visualInspectionSection: visualLoader.item

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Title, section nav and "back to project" all on one row — the
        // nav rail used to be a fixed 180px-wide left-hand column, taking
        // width away from every section's own content (which all need the
        // room). Moving it up here next to the title gives every section
        // the page's full width instead.
        RowLayout {
            objectName: "analysisPageHeader"
            Layout.fillWidth: true
            Layout.margins: 16
            spacing: 24

            ColumnLayout {
                id: analysisTitleBlock
                objectName: "analysisTitleBlock"
                spacing: 0

                Label {
                    objectName: "analysisPageTitle"
                    text: "Project: " + (analysis ? analysis.projectName : "")
                    font.pixelSize: 14
                    font.bold: true
                }
                Label {
                    objectName: "analysisPageRunId"
                    text: "Analysis: " + (analysis ? analysis.runId : "")
                    font.pixelSize: 11
                    color: Theme.mutedTextColor
                }
                Label {
                    objectName: "analysisPageDate"
                    text: "Date: " + (analysis ? analysis.startDateDisplay : "")
                    font.pixelSize: 11
                    color: Theme.mutedTextColor
                }
            }

            RowLayout {
                id: navRail
                objectName: "navRail"
                spacing: 4
                // A targeted analysis has exactly one page (Calibration —
                // see `isTargeted`'s own comment) — no tabs to switch
                // between, so the whole nav rail is pointless for it.
                visible: !analysisPage.isTargeted && !analysisPage.outputMissing
                // "I don't want user to run back and forth while things
                // are loading" — also closes off a plausible trigger for
                // the rare crash found while testing the loading overlay
                // (see AnalysisPage.qml's own comment on it): nothing
                // stopped a click from landing on a nav button, or "Back
                // to project", while the *previous* tab's content was
                // still mid-load.
                enabled: analysisPage.currentSectionReady
                opacity: enabled ? 1.0 : 0.5

                Repeater {
                    id: navRepeater
                    objectName: "navRepeater"
                    model: ["Summary", "MS1 Spectra", "Annotations", "Visual Inspection", "Spatial Correlation"]

                    delegate: Button {
                        objectName: "navButton_" + index
                        text: modelData
                        checkable: true
                        checked: sectionStack.currentIndex === index
                        onClicked: {
                            if (index === 3) analysisPage.goToVisualInspection()
                            else sectionStack.currentIndex = index
                        }

                        // "the buttons in analyses (MS1, Annotation
                        // etc)... should make the user understand that
                        // they can be clickable" — Buttons don't get a
                        // pointing-hand cursor by default in Qt Quick
                        // Controls the way HTML buttons do.
                        HoverHandler {
                            objectName: "navButtonHover_" + index
                            cursorShape: Qt.PointingHandCursor
                        }
                    }
                }
            }

            // Which data Visual Inspection / Spatial Correlation show (ADR
            // 49): the pipeline output, or a notebook-analyzed .h5ad
            // imported via Analyses > Import analyzed .h5ad…. Hidden until
            // there's something to switch to.
            RowLayout {
                objectName: "datasetSwitcher"
                visible: !analysisPage.isTargeted && analysisPage.importedDatasets.length > 0
                enabled: analysisPage.currentSectionReady
                opacity: enabled ? 1.0 : 0.5
                spacing: 4

                Label {
                    text: "Dataset:"
                    color: Theme.mutedTextColor
                }
                ComboBox {
                    id: datasetCombo
                    objectName: "datasetCombo"
                    Layout.preferredWidth: 200
                    model: ["Pipeline output"].concat(
                        analysisPage.importedDatasets.map(function (d) { return d.name }))
                    currentIndex: {
                        var id = (AnalysisBridge ? AnalysisBridge.activeDatasetId : -1)
                        for (var i = 0; i < analysisPage.importedDatasets.length; i++)
                            if (analysisPage.importedDatasets[i].id === id) return i + 1
                        return 0
                    }
                    onActivated: (index) => {
                        var id = index === 0 ? -1 : analysisPage.importedDatasets[index - 1].id
                        AnalysisBridge.setActiveDataset(analysisPage.analysis.analysisDbPath, id)
                    }
                    ToolTip.visible: hovered && currentIndex > 0
                    ToolTip.text: currentIndex > 0
                        ? "Imported from " + analysisPage.importedDatasets[currentIndex - 1].source_path
                          + "\nApplies to Visual Inspection and Spatial Correlation."
                        : ""
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
                ToolButton {
                    objectName: "removeDatasetButton"
                    text: "✕"
                    visible: datasetCombo.currentIndex > 0
                    ToolTip.visible: hovered
                    ToolTip.text: "Remove this imported dataset"
                    onClicked: removeDatasetDialog.open()
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
            }

            Item { Layout.fillWidth: true }

            Button {
                objectName: "backToProjectButton"
                text: "Back to project"
                enabled: analysisPage.currentSectionReady
                opacity: enabled ? 1.0 : 0.5
                onClicked: if (analysis) Router.showProjectHomeRequested(analysis.project)

                HoverHandler {
                    objectName: "backToProjectButtonHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: palette.mid
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 8

            StackLayout {
                id: sectionStack
                objectName: "sectionStack"
                currentIndex: 0
                anchors.fill: parent
                visible: !analysisPage.isTargeted && !analysisPage.outputMissing

                // Annotations/Visual Inspection are now lazy too (`active:
                // currentIndex === N`, matching MS1's existing pattern) —
                // opening the workspace used to eagerly construct *all
                // four* sections' full QML trees at once even though only
                // Summary was visible; now only the active tab's section
                // (plus Summary, the default) is ever constructed, cutting
                // the one-time cost of opening the workspace down to just
                // Summary's.
                //
                // Deliberately NOT `asynchronous: true` here, tempting as
                // it looks for smoothing over construction cost further:
                // tried it, and it reproducibly (~1 in 6 full-suite runs)
                // crashed with `Fatal Python error: Aborted`. Reverting it
                // made that SPECIFIC signature stop, but a low-frequency
                // crash (roughly 1 in 15-30 full-suite runs, vs. zero in
                // 16+ runs on the code before this change) still turned
                // up even with every Loader synchronous — its exact
                // trigger wasn't pinned down (didn't reproduce in any
                // standalone, non-pytest repro attempt; the one full
                // traceback caught landed in an unrelated later test,
                // pointing at a *deferred* effect rather than something
                // failing immediately). Left `asynchronous` off regardless
                // since it's one clearly-implicated variable removed at
                // no real cost — construction is small and
                // precompiled-QML-cache-backed already. See
                // [[gui-real-device-testing-workflow]]-style memory notes
                // for the fuller investigation if this resurfaces.
                Loader {
                    id: summaryLoader
                    objectName: "summarySectionLoader"
                    // Never constructed for a targeted analysis — see
                    // `isTargeted`'s own comment; this is the one Loader in
                    // this StackLayout with no `active:` gate otherwise (it
                    // used to be eager regardless of analysis type, which
                    // would build SummarySection.qml — untargeted-shaped
                    // data throughout — for no reason on every targeted run).
                    active: !analysisPage.isTargeted && !analysisPage.outputMissing
                    source: "qrc:/Views/SummarySection.qml"
                    // Qt.binding, not a one-time assignment: a plain
                    // `item.analysis = analysisPage.analysis` only runs once,
                    // right when this Loader finishes — for a page reached
                    // via StackView.push({"analysis": ...}), analysisPage's
                    // own `analysis` property can still be unset at that
                    // exact moment, permanently leaving the section's copy
                    // null.
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }

                Loader {
                    id: ms1Loader
                    objectName: "ms1SectionLoader"
                    // Lazy (unlike Summary): StackLayout keeps every page
                    // as a permanent sibling rather than destroying hidden
                    // ones, so an eager section would be built (and its
                    // spectrum fetched) on every workspace open.
                    active: sectionStack.currentIndex === 1 && !analysisPage.outputMissing
                    source: "qrc:/Views/MS1SpectraSection.qml"
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }
                Loader {
                    id: annotationsLoader
                    objectName: "annotationsSectionLoader"
                    active: sectionStack.currentIndex === 2 && !analysisPage.outputMissing
                    source: "qrc:/Views/AnnotationsSection.qml"
                    onLoaded: {
                        item.analysis = Qt.binding(function () { return analysisPage.analysis })
                        // "Inspect visually" — set the handoff before
                        // switching tabs; visualLoader below picks it up
                        // via its own pendingInspectMz binding once
                        // sectionStack.currentIndex flips its `active` on.
                        item.inspectVisuallyRequested.connect(function (mz) {
                            analysisPage.goToVisualInspection(mz)
                        })
                        item.stateSaved.connect(function (s) { analysisPage.annotationsState = s })
                        item.restoreState(analysisPage.annotationsState)
                    }
                }
                Loader {
                    id: visualLoader
                    objectName: "visualSectionLoader"
                    active: sectionStack.currentIndex === 3 && !analysisPage.outputMissing
                    source: "qrc:/Views/VisualInspectionSection.qml"
                    onLoaded: {
                        item.analysis = Qt.binding(function () { return analysisPage.analysis })
                        // Connected before pendingInspectMz is assigned
                        // below, not after — assigning a binding evaluates
                        // it immediately, and (when a request is actually
                        // pending) that synchronously emits
                        // pendingInspectHandled from inside
                        // VisualInspectionSection's own onPendingInspectMzChanged
                        // before this function would otherwise get back to
                        // its next line — connecting first ensures this
                        // handler already exists to catch it.
                        item.pendingInspectHandled.connect(function () {
                            analysisPage.pendingInspectMz = NaN
                            analysisPage.pendingInspectLayer = ""
                        })
                        // Before the pending-inspect bindings below, so a
                        // jump from "Inspect visually" wins over it.
                        item.stateSaved.connect(function (s) { analysisPage.visualState = s })
                        item.restoreState(analysisPage.visualState)
                        // Layer *before* mz: assigning pendingInspectMz's
                        // binding below fires applyPendingInspect()
                        // synchronously (see the comment above) — if
                        // pendingInspectLayer's own binding weren't already
                        // in place by then, applyPendingInspect() would
                        // read its old/default value ("") and silently
                        // skip switching the layer, one assignment too late
                        // to ever apply once the one-shot mz request is
                        // already consumed.
                        item.pendingInspectLayer = Qt.binding(function () { return analysisPage.pendingInspectLayer })
                        item.pendingInspectMz = Qt.binding(function () { return analysisPage.pendingInspectMz })
                    }
                }
                Loader {
                    id: spatialCorrLoader
                    objectName: "spatialCorrelationSectionLoader"
                    active: sectionStack.currentIndex === 4 && !analysisPage.outputMissing
                    source: "qrc:/Views/SpatialCorrelationSection.qml"
                    onLoaded: {
                        item.analysis = Qt.binding(function () { return analysisPage.analysis })
                        // "Show spatial distribution" — identical handoff
                        // to Annotations' "Inspect visually" above (ADR 32).
                        item.showSpatialDistributionRequested.connect(function (mz) {
                            analysisPage.goToVisualInspection(mz)
                        })
                        // Same handoff, from the *local* summary table's
                        // own context menu — also switches the heatmap
                        // layer to Local Moran's I, not just the feature.
                        item.showLocalSpatialDistributionRequested.connect(function (mz) {
                            analysisPage.goToVisualInspection(mz, "localMoran")
                        })
                    }
                }
            }

            // A targeted analysis' sole page — see `isTargeted`'s own
            // comment. Not one of `sectionStack`'s Loaders (that whole
            // StackLayout is hidden for a targeted analysis) since there's
            // no tab to switch to it from; it just fills this Item outright.
            Loader {
                id: calibrationLoader
                objectName: "calibrationSectionLoader"
                active: analysisPage.isTargeted && !analysisPage.outputMissing
                anchors.fill: parent
                source: "qrc:/Views/CalibrationSection.qml"
                onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
            }

            // "whenever I change a page there is a small delay/page
            // construction step that is ugly. Can we have like a loading
            // element... and when the whole page is ready (plots
            // included) it is displayed?" — covers the currently-active
            // tab's own Loader (incrementally constructing, see above)
            // and, for MS1 specifically, the spectrum plot's own load
            // ("plots included").
            LoadingOverlay {
                id: sectionLoadingOverlay
                objectName: "sectionLoadingOverlay"
                anchors.fill: parent
                visible: !analysisPage.currentSectionReady && !analysisPage.outputMissing
            }

            Label {
                objectName: "outputMissingLabel"
                anchors.centerIn: parent
                width: Math.min(parent.width - 32, 640)
                visible: analysisPage.outputMissing
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                text: "Output folder not found:\n" + (analysis ? analysis.outDir : "")
                      + "\n\nThis analysis' results are not in this project folder on this "
                      + "computer. Check that the project's output/ folder was copied or "
                      + "is reachable from here."
            }
        }
    }

    readonly property bool currentSectionReady: {
        if (analysisPage.isTargeted) {
            return calibrationLoader.status === Loader.Ready
                   && calibrationLoader.item && !calibrationLoader.item.rowsLoading
        }
        if (analysisPage.switchingToVisual) return false
        switch (sectionStack.currentIndex) {
        case 0: return summaryLoader.status === Loader.Ready
                       && summaryLoader.item && !summaryLoader.item.statsLoading
        case 1: return ms1Loader.status === Loader.Ready
                       && ms1Loader.item && !ms1Loader.item.plotLoading
        case 2: return annotationsLoader.status === Loader.Ready
                       && annotationsLoader.item && !annotationsLoader.item.rowsLoading
        case 3: return visualLoader.status === Loader.Ready
                       && visualLoader.item && !visualLoader.item.featuresLoading
        case 4: return spatialCorrLoader.status === Loader.Ready
                       && spatialCorrLoader.item && !spatialCorrLoader.item.rowsLoading
        default: return true
        }
    }

    Dialog {
        id: removeDatasetDialog
        objectName: "removeDatasetDialog"
        title: "Remove imported dataset"
        modal: true
        anchors.centerIn: parent
        standardButtons: Dialog.Yes | Dialog.No
        readonly property var dataset: datasetCombo.currentIndex > 0
                                       ? analysisPage.importedDatasets[datasetCombo.currentIndex - 1] : null

        Label {
            width: 420
            wrapMode: Text.Wrap
            text: removeDatasetDialog.dataset
                  ? "Remove \"" + removeDatasetDialog.dataset.name + "\" from this analysis? "
                    + "Its per-sample copies (and any ROIs or Moran's I computed on them) are deleted. "
                    + "Your own file is kept:\n" + removeDatasetDialog.dataset.source_path
                  : ""
        }
        onAccepted: {
            if (removeDatasetDialog.dataset)
                AnalysisBridge.deleteImportedDataset(analysisPage.analysis.analysisDbPath,
                                                     removeDatasetDialog.dataset.id)
        }
    }
}

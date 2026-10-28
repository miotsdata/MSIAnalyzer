import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"
import "qrc:/Utils/SearchQuery.js" as SearchQuery

Item {
    id: annotationsSection
    objectName: "annotationsSection"

    // Right-click a row -> "Inspect visually" -> jumps the workspace to
    // the Visual Inspection tab, already on this feature. Handled by the
    // owning AnalysisPage (which owns sectionStack, not this section) —
    // see its own annotationsLoader.onLoaded comment.
    signal inspectVisuallyRequested(real mz)

    property var analysis
    // Bumped after a successful "Predict formula" run to force rows/
    // topHits to re-fetch — a predicted formula can change a feature's
    // representative identity (see analysis_db's 3-tier precedence,
    // ADR 27), which a plain property binding on analysisDbPath alone
    // wouldn't notice (the path itself doesn't change).
    property int refreshToken: 0

    // `rows` used to be a synchronous property binding straight onto
    // AnalysisBridge.getAnnotationTable — a direct blocking SQL query on
    // the GUI thread, evaluated during this section's own Loader
    // construction, reported as freezing the app on a real analysis (see
    // ADR 33 for the query-side half of the fix; this is the other half).
    // Fetched on a background thread instead (requestAnnotationTable /
    // annotationTableReady, same fire-and-forget pattern as
    // requestMirrorPlot) — `rowsLoading` gates AnalysisPage.qml's
    // existing LoadingOverlay via currentSectionReady, the same mechanism
    // MS1's plotLoading already uses.
    property var rows: []
    property bool rowsLoading: false
    // Also list features with no identity at all, to look at their MS2.
    property bool showUnannotated: false
    onShowUnannotatedChanged: annotationsSection.refreshRows()

    function refreshRows() {
        if (analysis && analysis.analysisDbPath) {
            annotationsSection.rowsLoading = true
            AnalysisBridge.requestAnnotationTable(analysis.analysisDbPath,
                                                  annotationsSection.showUnannotated)
        } else {
            annotationsSection.rows = []
            annotationsSection.rowsLoading = false
        }
    }

    onAnalysisChanged: annotationsSection.refreshRows()
    Connections {
        target: AnalysisBridge
        function onTargetListMatchesChanged(dbPath) {
            if (analysis && dbPath === analysis.analysisDbPath) annotationsSection.refreshRows()
        }
    }
    onRefreshTokenChanged: annotationsSection.refreshRows()

    Connections {
        target: AnalysisBridge
        function onAnnotationTableReady(receivedRows) {
            annotationsSection.rows = receivedRows
            annotationsSection.rowsLoading = false
        }
    }

    // "User can decide to sort for any of these" (feature id, name, m/z) —
    // clicking a column header sorts by it, clicking the same header again
    // flips direction. Sorting never changes `selectedFeatureId` itself
    // (see below) — only the row order.
    property string sortColumn: "feature_id"
    property bool sortAscending: true

    function sortBy(column) {
        if (annotationsSection.sortColumn === column) {
            annotationsSection.sortAscending = !annotationsSection.sortAscending
        } else {
            annotationsSection.sortColumn = column
            annotationsSection.sortAscending = true
        }
    }

    // Two separate fields (ADR 34) — name (contains) and m/z (a single
    // value within a small tolerance, or an explicit "min-max" range),
    // see SearchQuery.matchesName/matchesMz for the exact syntax. Both
    // must match when both are non-empty (AND, not OR). Applied before
    // sorting, never changes `selectedFeatureId`.
    property string nameQuery: ""
    property string mzQuery: ""

    readonly property var filteredRows: annotationsSection.rows.filter(function (r) {
        return SearchQuery.matchesName(annotationsSection.nameQuery, r.compound_name)
            && SearchQuery.matchesMz(annotationsSection.mzQuery, r.mz)
    })

    readonly property var sortedRows: {
        var col = annotationsSection.sortColumn
        var dir = annotationsSection.sortAscending ? 1 : -1
        var arr = annotationsSection.filteredRows.slice()
        arr.sort(function (a, b) {
            var av = a[col]
            var bv = b[col]
            if (av === bv) return 0
            if (av === null || av === undefined) return 1
            if (bv === null || bv === undefined) return -1
            if (typeof av === "string" || typeof bv === "string")
                return dir * String(av).localeCompare(String(bv))
            return dir * (av < bv ? -1 : 1)
        })
        return arr
    }

    // Nothing selected until the user picks a feature — a live binding to
    // "the sorted list's first row" would otherwise jump the selection
    // around every time the user re-sorts, which sorting should never do.
    property int selectedFeatureId: -1

    property int topN: 5
    property var topHits: []
    property var selectedHit: null
    // Always the stored *filtered* spectra, rendered as a plain PNG. Raw
    // sources and the interactive mirror plot only exist in the "Details"
    // popup (mirrorPlotDetailWindow below); see
    // AnalysisBridge.getBasicMirrorPlotImage's docstring.
    property string basicPlotImage: ""

    function refreshTopHits() {
        annotationsSection.topHits = (analysis && analysis.analysisDbPath
                                       && annotationsSection.selectedFeatureId >= 0)
            ? AnalysisBridge.getFeatureTopHits(analysis.analysisDbPath,
                                                annotationsSection.selectedFeatureId,
                                                annotationsSection.topN)
            : []
        annotationsSection.selectedHit = annotationsSection.topHits.length > 0
                                          ? annotationsSection.topHits[0] : null
    }

    function refreshBasicPlot() {
        // A predicted-formula hit (ADR 27) has no MS2 scan behind it at
        // all — getBasicMirrorPlotImage is keyed on ms2_annotations.id,
        // which its negative id was deliberately built to never match
        // (see getFeatureTopHits); skip the round trip entirely rather
        // than asking the bridge to fail gracefully for a case we
        // already know the answer to.
        annotationsSection.basicPlotImage = (annotationsSection.selectedHit
                                              && annotationsSection.selectedHit.kind === "ms2"
                                              && analysis && analysis.analysisDbPath)
            ? AnalysisBridge.getBasicMirrorPlotImage(
                  analysis.analysisDbPath, annotationsSection.selectedHit.id)
            : ""
    }

    onSelectedFeatureIdChanged: refreshTopHits()
    onSelectedHitChanged: refreshBasicPlot()

    // Remembered across tab switches: AnalysisPage keeps what stateSaved
    // hands it and passes it back to restoreState on the next visit.
    signal stateSaved(var state)
    Component.onDestruction: annotationsSection.stateSaved({
        "selectedFeatureId": annotationsSection.selectedFeatureId,
        "selectedHitId": annotationsSection.selectedHit ? annotationsSection.selectedHit.id : null,
        "sortColumn": annotationsSection.sortColumn,
        "sortAscending": annotationsSection.sortAscending,
        "nameQuery": annotationsSection.nameQuery,
        "mzQuery": annotationsSection.mzQuery,
        "topN": annotationsSection.topN,
        "showUnannotated": annotationsSection.showUnannotated,
    })
    function restoreState(s) {
        if (!s) return
        annotationsSection.sortColumn = s.sortColumn
        annotationsSection.sortAscending = s.sortAscending
        annotationsSection.nameQuery = s.nameQuery
        annotationsSection.mzQuery = s.mzQuery
        annotationsSection.topN = s.topN
        annotationsSection.showUnannotated = s.showUnannotated
        annotationsSection.selectedFeatureId = s.selectedFeatureId
        var hit = annotationsSection.topHits.find(function (h) { return h.id === s.selectedHitId })
        if (hit) annotationsSection.selectedHit = hit
    }

    // The selected feature's table row — its `ms2_*` columns name its
    // best MS2 scan (null without MS2).
    readonly property var selectedRow: annotationsSection.rows.find(function (r) {
        return r.feature_id === annotationsSection.selectedFeatureId
    }) || null
    // No library hit to plot, but the feature has MS2: show that scan
    // alone, library side empty.
    readonly property bool showsBestScan: !(annotationsSection.selectedHit
                                            && annotationsSection.selectedHit.kind === "ms2")
                                          && !!annotationsSection.selectedRow
                                          && annotationsSection.selectedRow.ms2_scan_id !== null
                                          && annotationsSection.selectedRow.ms2_scan_id !== undefined
    property string bestScanPlotImage: ""
    function refreshBestScanPlot() {
        annotationsSection.bestScanPlotImage = ""
        if (annotationsSection.showsBestScan)
            AnalysisBridge.requestBestScanPlot(annotationsSection.selectedRow.ms2_raw_db_path || "",
                                               annotationsSection.selectedRow.ms2_scan_id)
    }
    onShowsBestScanChanged: refreshBestScanPlot()
    onSelectedRowChanged: refreshBestScanPlot()
    Connections {
        target: AnalysisBridge
        function onBestScanPlotReady(uri) {
            if (annotationsSection.showsBestScan) annotationsSection.bestScanPlotImage = uri
        }
    }

    // "all the stats are written better (list, clear)" — a flat
    // label/value pair list (statsRepeater below lays it out as a
    // 2-column grid), built straight from the already-fetched
    // `selectedHit` — no extra bridge round trip needed. A predicted
    // hit (ADR 27) has no score/coverage/peak-count/sample/library — it
    // was never a scored MS2 match, just a mass-decomposition guess —
    // so it gets its own, much shorter, set of rows instead.
    // A non-annotated row's label: why MS2 annotation found nothing (ADR
    // 0062), or just whether it has MS2 for an analysis run before that.
    function unannotatedLabel(r) {
        switch (r.ms2_annotation_outcome) {
        case "no_candidates": return "Unannotated · no library candidate"
        case "no_shared_fragments":
            return "Unannotated · no shared fragments"
                   + (r.best_n_matched_peaks > 0 ? " (best " + r.best_n_matched_peaks + ")" : "")
        case "all_scans_skipped": return "Unannotated · all MS2 scans skipped"
        case "no_ms2": return "Unannotated · no MS2"
        }
        return r.ms2_scan_id != null ? "Unannotated" : "Unannotated · no MS2"
    }

    readonly property var statsRows: {
        var h = annotationsSection.selectedHit
        var r = annotationsSection.selectedRow
        if (!h && annotationsSection.showsBestScan) {
            var scanPairs = [
                ["Annotation", "none (no library match)"],
                ["Best MS2 scan", r.ms2_scan_id + " in " + (r.ms2_sample_name || "?")],
                ["MS2 scans", r.ms2_n_scans],
                ["Peaks", r.ms2_n_peaks !== null ? r.ms2_n_peaks : "?"],
                ["Purity", r.ms2_purity_score !== null ? Number(r.ms2_purity_score).toFixed(2) : "?"],
            ]
            if (r.n_candidates != null) {
                var skipped = r.n_skipped_flat + r.n_skipped_purity + r.n_skipped_unreadable
                scanPairs.push(
                    ["Library candidates", r.n_candidates + " within the m/z window"],
                    ["Best shared fragments", r.best_n_matched_peaks],
                    ["Scans scored", (r.n_scans - skipped) + " of " + r.n_scans
                        + (skipped > 0 ? "  (skipped: " + r.n_skipped_flat + " flat, "
                           + r.n_skipped_purity + " low purity, "
                           + r.n_skipped_unreadable + " unreadable)" : "")])
            }
            return [].concat.apply([], scanPairs.map(function (p) { return [p[0] + ":", String(p[1])] }))
        }
        if (!h)
            return []
        var pairs = h.kind === "predicted"
            ? [
                ["Formula", h.formula || "?"],
                ["Adduct", h.adduct || "?"],
                ["Mass error", Number(h.mass_error_ppm).toFixed(2) + " ppm"],
              ]
            : [
                ["Compound", (h.compound_name || h.inchikey || "?")
                             + (h.compound_formula ? " (" + h.compound_formula + ")" : "")],
                ["InChIKey", h.inchikey || "?"],
                ["Sample", h.sample_name || "?"],
                ["Library", h.library_name || "?"],
                ["Score", Number(h.score).toFixed(3)
                          + "  (dot product " + Number(h.dot_product_score).toFixed(3) + ")"],
                ["Coverage", "library " + Number(h.lib_coverage).toFixed(2)
                             + "  ·  empirical " + Number(h.emp_coverage).toFixed(2)],
                ["Matched peaks", h.n_matched_peaks + " / " + h.n_lib_peaks],
              ]
        var flat = []
        for (var i = 0; i < pairs.length; i++) {
            flat.push(pairs[i][0] + ":")
            flat.push(pairs[i][1])
        }
        return flat
    }

    Label {
        id: emptyStateLabel
        objectName: "annotationsEmptyStateLabel"
        visible: rows.length === 0 && !annotationsSection.showUnannotated && !annotationsSection.rowsLoading
        anchors.centerIn: parent
        text: "No annotated features."
        color: Theme.mutedTextColor
    }

    // A resizable split (not a fixed-fraction RowLayout — dragging the
    // handle should actually work, and the two sides want independent
    // widths depending on how many/long the feature names are) between
    // the feature table and the detail panel.
    SplitView {
        id: annotationsSplit
        objectName: "annotationsSplit"
        anchors.fill: parent
        anchors.margins: 16
        visible: rows.length > 0 || annotationsSection.showUnannotated
        orientation: Qt.Horizontal

        ColumnLayout {
            id: tablePanel
            objectName: "annotationsTablePanel"
            // Explicit — a SplitView pane's cross-axis size (height, for
            // this horizontal orientation) is supposed to auto-fill from
            // the SplitView itself with no binding needed at all, and
            // that's the case elsewhere in this codebase (ProjectHomePage.qml's
            // panes). Here it intermittently didn't: measured stuck at
            // the pane's own tiny implicit content height instead of
            // annotationsSplit's real height, with no explicit binding of
            // ours to blame — a `SplitView` cross-axis quirk, not
            // something a specific property change caused. Binding this
            // directly sidesteps it regardless of the underlying cause.
            height: annotationsSplit.height
            SplitView.preferredWidth: annotationsSplit.width * 0.4
            SplitView.minimumWidth: 240
            spacing: 8

            RowLayout {
                Layout.fillWidth: true

                Label {
                    text: (annotationsSection.showUnannotated ? "Features (" : "Annotated features (")
                          + annotationsSection.rows.length + ")"
                    font.bold: true
                    font.pixelSize: 14
                    Layout.fillWidth: true
                }
                AppCheckBox {
                    objectName: "showUnannotatedCheckBox"
                    text: "Show non-annotated"
                    checked: annotationsSection.showUnannotated
                    onToggled: annotationsSection.showUnannotated = checked
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
                Button {
                    objectName: "openPredictFormulaButton"
                    text: "Predict formula…"
                    enabled: !!(analysis && analysis.analysisDbPath)
                    onClicked: {
                        // Lazily constructs predictFormulaWindowLoader.item on
                        // first click, not before — see the Loader's own comment.
                        predictFormulaWindowLoader.active = true
                        var win = predictFormulaWindowLoader.item
                        win.showFor(analysis.analysisDbPath)
                    }

                    HoverHandler {
                        cursorShape: Qt.PointingHandCursor
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 4

                TextField {
                    id: annotationsNameSearchField
                    objectName: "annotationsNameSearchField"
                    Layout.fillWidth: true
                    placeholderText: "Search name…"
                    text: annotationsSection.nameQuery
                    onTextChanged: annotationsSection.nameQuery = text
                }
                TextField {
                    id: annotationsMzSearchField
                    objectName: "annotationsMzSearchField"
                    Layout.preferredWidth: 100
                    placeholderText: "m/z…"
                    text: annotationsSection.mzQuery
                    onTextChanged: annotationsSection.mzQuery = text
                }
            }

            RowLayout {
                objectName: "annotationsTableHeader"
                Layout.fillWidth: true
                spacing: 4

                Repeater {
                    model: [
                        {key: "feature_id", label: "Feature", width: 70},
                        {key: "compound_name", label: "Name", width: -1},
                        {key: "mz", label: "m/z", width: 90},
                        {key: "best_score", label: "Score", width: 70},
                    ]

                    delegate: MouseArea {
                        objectName: "annotationSortHeader_" + modelData.key
                        Layout.preferredWidth: modelData.width > 0 ? modelData.width : -1
                        Layout.fillWidth: modelData.width < 0
                        implicitHeight: headerLabel.implicitHeight
                        cursorShape: Qt.PointingHandCursor
                        onClicked: annotationsSection.sortBy(modelData.key)

                        Label {
                            id: headerLabel
                            anchors.fill: parent
                            font.bold: true
                            elide: Text.ElideRight
                            text: modelData.label
                                  + (annotationsSection.sortColumn === modelData.key
                                     ? (annotationsSection.sortAscending ? " ▲" : " ▼")
                                     : "")
                        }
                    }
                }
            }

            // A plain Item wrapping both the empty-state label and the
            // real table, not two Layout-managed siblings each toggling
            // its own `visible` — an invisible item claims zero space in
            // QtQuick.Layouts regardless of Layout.fillWidth/fillHeight,
            // so toggling *which sibling* is visible used to reflow
            // whatever sits below this panel (reported: switching between
            // "no search matches" and a populated table shifted things
            // around it). Wrapping both in one always-fillHeight Item
            // means this panel's own size never changes when its content
            // does — only what's drawn *inside* that fixed space toggles.
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                Label {
                    objectName: "annotationsSearchEmptyLabel"
                    visible: annotationsSection.rows.length > 0 && annotationsSection.sortedRows.length === 0
                    text: "No features match your search."
                    color: Theme.mutedTextColor
                    anchors.centerIn: parent
                    horizontalAlignment: Text.AlignHCenter
                }

                Flickable {
                    id: tableFlickable
                    visible: annotationsSection.sortedRows.length > 0
                    anchors.fill: parent
                    clip: true
                contentWidth: width
                // Computed from the row count directly, not
                // `tableColumn.implicitHeight` — that formed a genuine
                // binding cycle (Column's own implicit size, computed
                // from its children's positions, indirectly depends on
                // this Flickable's contentItem geometry, which this
                // property was supposed to help determine) that silently
                // never resolved: `tableColumn` stayed frozen at height 0
                // and every row at y=0, permanently (confirmed: still 0
                // after a 1.2s wait, not a timing issue). Every row here
                // has the same fixed height (40) and spacing (4), so the
                // total is just arithmetic — no dependency on anything
                // this property itself feeds into.
                contentHeight: annotationsSection.sortedRows.length > 0
                    ? annotationsSection.sortedRows.length * 40
                      + (annotationsSection.sortedRows.length - 1) * tableColumn.spacing
                    : 0
                boundsBehavior: Flickable.StopAtBounds

                // A plain `Column` positioner, not `ColumnLayout` — the
                // latter, for a Repeater-generated list of same-width
                // rows nested inside a Flickable, reproducibly failed to
                // stack its children vertically at all (every row landed
                // at y=0, overlapping) regardless of what Layout.*
                // properties the delegates carried. `Column` just stacks
                // children by `spacing` unconditionally, with no Layout-
                // system sizing negotiation to go wrong — everything
                // these rows need (`topHitsColumn` below, same fix).
                Column {
                    id: tableColumn
                    width: tableFlickable.width
                    spacing: 4

                    Repeater {
                        id: annotationsRepeater
                        objectName: "annotationsRepeater"
                        model: annotationsSection.sortedRows

                        delegate: Rectangle {
                            objectName: "annotationRow_" + modelData.feature_id
                            width: tableColumn.width
                            height: 40
                            radius: 4
                            color: annotationsSection.selectedFeatureId === modelData.feature_id
                                   ? palette.highlight : "transparent"
                            border.color: palette.mid
                            border.width: 1

                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                cursorShape: Qt.PointingHandCursor
                                onClicked: (mouse) => {
                                    if (mouse.button === Qt.RightButton) {
                                        annotationRowContextMenu.popup()
                                    } else {
                                        annotationsSection.selectedFeatureId = modelData.feature_id
                                    }
                                }
                            }

                            Menu {
                                id: annotationRowContextMenu
                                objectName: "annotationRowContextMenu_" + modelData.feature_id

                                MenuItem {
                                    objectName: "inspectVisuallyMenuItem_" + modelData.feature_id
                                    text: "Inspect visually"
                                    onTriggered: annotationsSection.inspectVisuallyRequested(modelData.mz)
                                }
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 4

                                Label {
                                    objectName: "annotationRowFeature_" + modelData.feature_id
                                    text: String(modelData.feature_id)
                                    Layout.preferredWidth: 70
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Label {
                                    objectName: "annotationRowCompound_" + modelData.feature_id
                                    readonly property bool unannotated: !modelData.source
                                    text: unannotated
                                          ? annotationsSection.unannotatedLabel(modelData)
                                          : (modelData.compound_name || modelData.inchikey || "?")
                                    font.italic: unannotated
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText
                                           : (unannotated ? Theme.mutedTextColor : palette.text)
                                }
                                Label {
                                    objectName: "annotationRowMz_" + modelData.feature_id
                                    text: modelData.mz !== null && modelData.mz !== undefined
                                          ? Number(modelData.mz).toFixed(4) : "?"
                                    Layout.preferredWidth: 90
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Label {
                                    objectName: "annotationRowScore_" + modelData.feature_id
                                    text: modelData.best_score !== null && modelData.best_score !== undefined
                                          ? Number(modelData.best_score).toFixed(3) : ""
                                    Layout.preferredWidth: 70
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                            }
                        }
                    }
                }
                }
            }
        }

        ColumnLayout {
            id: detailPanel
            objectName: "annotationsDetailPanel"
            height: annotationsSplit.height
            SplitView.fillWidth: true
            SplitView.minimumWidth: 320
            spacing: 12

            Label {
                objectName: "annotationsDetailEmptyLabel"
                visible: annotationsSection.selectedFeatureId < 0
                text: "Click a feature to see its top hits and mirror plot."
                color: Theme.mutedTextColor
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            // A vertical SplitView, not a plain ColumnLayout — a
            // ColumnLayout with one child's `Layout.preferredHeight` set
            // and the other's `Layout.fillHeight: true` turned out to
            // *not* actually respect the preferred value (measured: the
            // fillHeight sibling got squeezed to its own minimum and the
            // "preferred" one silently claimed the leftover space
            // instead). SplitView's own `SplitView.preferredHeight` is
            // the correct mechanism for each pane's *default* share
            // below — it's also what makes the divider draggable
            // ("resizable SplitView over a fixed ratio" desktop-app
            // direction), which a plain `height:` binding doesn't
            // integrate with (tried that first: it happened to measure
            // correctly under the offscreen test platform, but in the
            // real app SplitView's own sizing pass overrides a plain
            // `height:` binding on its own pane once it actually engages
            // — so the *default* silently stopped matching the intended
            // ratio there, even though dragging still worked).
            SplitView {
                id: detailSplit
                objectName: "detailSplit"
                orientation: Qt.Vertical
                visible: annotationsSection.selectedFeatureId >= 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Explicit, not relying on Layout.fillHeight alone — see
                // tablePanel/detailPanel's own `height: annotationsSplit.height`
                // above for the same fix and why it's needed.
                height: detailPanel.height

                // Row 1 (of 3): top-N hits for the selected feature —
                // "like in ms1", but each hit also states which sample it
                // came from, since a feature's best hit for one compound
                // can come from any of its samples. Scrolls internally
                // (topHitsFlickable) if there isn't room for all of them.
                ColumnLayout {
                    objectName: "topHitsPanel"
                    SplitView.preferredHeight: detailSplit.height * 0.3
                    SplitView.minimumHeight: 80
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "Top hits:"
                            font.bold: true
                            Layout.fillWidth: true
                        }
                        SpinBox {
                            id: topNSpin
                            objectName: "topNSpinBox"
                            editable: true
                            from: 1
                            to: 20
                            value: annotationsSection.topN
                            onValueModified: annotationsSection.topN = value
                        }
                    }

                    // Same always-reserve-the-space wrapper as tablePanel's
                    // own Item above, and for the same reason — see its
                    // comment.
                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                    Label {
                        objectName: "topHitsEmptyLabel"
                        visible: annotationsSection.topHits.length === 0
                        text: "No annotation hits for this feature."
                        color: Theme.mutedTextColor
                        wrapMode: Text.Wrap
                        anchors.centerIn: parent
                        width: parent.width
                    }

                    Flickable {
                        id: topHitsFlickable
                        objectName: "topHitsFlickable"
                        visible: annotationsSection.topHits.length > 0
                        anchors.fill: parent
                        clip: true
                        contentWidth: width
                        // Computed directly, not topHitsColumn.implicitHeight
                        // — see tableFlickable's own contentHeight comment
                        // above for why (the same binding-cycle freeze).
                        contentHeight: annotationsSection.topHits.length > 0
                            ? annotationsSection.topHits.length * 30
                              + (annotationsSection.topHits.length - 1) * topHitsColumn.spacing
                            : 0
                        boundsBehavior: Flickable.StopAtBounds

                        // Column, not ColumnLayout — see tableColumn's own
                        // comment above for why.
                        Column {
                            id: topHitsColumn
                            width: topHitsFlickable.width
                            spacing: 2

                            Repeater {
                                id: topHitsRepeater
                                objectName: "topHitsRepeater"
                                model: annotationsSection.topHits

                                delegate: Rectangle {
                                    objectName: "topHit_" + modelData.id
                                    width: topHitsColumn.width
                                    height: 30
                                    radius: 4
                                    color: annotationsSection.selectedHit
                                           && annotationsSection.selectedHit.id === modelData.id
                                           ? palette.highlight : "transparent"

                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: annotationsSection.selectedHit = modelData
                                    }

                                    Label {
                                        objectName: "topHitLabel_" + modelData.id
                                        anchors.fill: parent
                                        anchors.leftMargin: 4
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        textFormat: Text.StyledText
                                        color: annotationsSection.selectedHit
                                               && annotationsSection.selectedHit.id === modelData.id
                                               ? palette.highlightedText : palette.text
                                        text: modelData.kind === "predicted"
                                              ? (index + 1) + ". <b>" + modelData.formula + "</b>"
                                                + "  ·  " + modelData.adduct
                                                + "  ·  " + Number(modelData.mass_error_ppm).toFixed(2) + " ppm"
                                                + "  <i>(predicted, no MS2)</i>"
                                              : (index + 1) + ". "
                                                + (modelData.compound_name || modelData.inchikey || "?")
                                                + " (score <b>" + Number(modelData.score).toFixed(3) + "</b>)"
                                                + "  ·  " + (modelData.sample_name || "?")
                                                + (modelData.library_name ? "  ·  " + modelData.library_name : "")
                                    }
                                }
                            }
                        }
                    }
                    }
                }

                // Row 2 (of 3): the match's stats, as a plain label/value
                // list — "all the stats are written better (list,
                // clear)" — instead of packed into the interactive
                // plot's own title/metadata box (still there in the
                // "Details" popup's interactive plot, just not the
                // primary way to read them here).
                ColumnLayout {
                    objectName: "statsPanel"
                    SplitView.preferredHeight: detailSplit.height * 0.3
                    SplitView.minimumHeight: 60
                    spacing: 4

                    Label {
                        text: "Match details:"
                        font.bold: true
                    }

                    // Same always-reserve-the-space wrapper as tablePanel's
                    // own Item above.
                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                    Label {
                        objectName: "statsEmptyLabel"
                        visible: annotationsSection.statsRows.length === 0
                        text: "No hit selected."
                        color: Theme.mutedTextColor
                        anchors.top: parent.top
                    }

                    Flickable {
                        objectName: "statsFlickable"
                        visible: annotationsSection.statsRows.length > 0
                        anchors.fill: parent
                        clip: true
                        contentWidth: width
                        contentHeight: statsGrid.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds

                        GridLayout {
                            id: statsGrid
                            width: parent.width
                            columns: 2
                            columnSpacing: 10
                            rowSpacing: 2

                            Repeater {
                                objectName: "statsRepeater"
                                model: annotationsSection.statsRows

                                delegate: Label {
                                    objectName: index % 2 === 0
                                                ? "statLabel_" + Math.floor(index / 2)
                                                : "statValue_" + Math.floor(index / 2)
                                    Layout.column: index % 2
                                    Layout.row: Math.floor(index / 2)
                                    Layout.fillWidth: index % 2 === 1
                                    font.bold: index % 2 === 0
                                    elide: Text.ElideRight
                                    text: modelData
                                }
                            }
                        }
                    }
                    }
                }

                // Row 3 (of 3): a fast static raster plot — "a basic plot
                // in that panel (no plotly)" — always the stored filtered
                // spectra. The "Details" button opens the interactive
                // mirror plot (with the raw/filtered toggles) in its own
                // window instead.
                ColumnLayout {
                    objectName: "basicPlotPanel"
                    SplitView.preferredHeight: detailSplit.height * 0.4
                    SplitView.minimumHeight: 150
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "Mirror plot:"
                            font.bold: true
                            Layout.fillWidth: true
                        }
                        ToolButton {
                            objectName: "mirrorPlotDetailsButton"
                            text: "🔍 Details"
                            // A predicted hit (ADR 27) has no MS2 scan —
                            // nothing for the interactive mirror plot
                            // to show.
                            enabled: !!annotationsSection.selectedHit
                                     && annotationsSection.selectedHit.kind === "ms2"

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }

                            onClicked: {
                                // Lazily constructs mirrorPlotDetailWindowLoader.item on
                                // first click, not before — see the Loader's own comment.
                                // showFor() reuses the same window across clicks (one
                                // mirror plot window at a time) and refreshes its
                                // content even when it was already open.
                                mirrorPlotDetailWindowLoader.active = true
                                var win = mirrorPlotDetailWindowLoader.item
                                win.showFor(analysis.analysisDbPath, annotationsSection.selectedHit.id)
                            }
                        }
                    }

                    // Same always-reserve-the-space wrapper as tablePanel's
                    // own Item above — this is the panel specifically
                    // reported: without it, selecting a no-MS2 feature
                    // (only the small empty-state label, no fillHeight
                    // Image) shrank this panel and moved its own "Mirror
                    // plot:" title along with it.
                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                    Label {
                        objectName: "basicPlotEmptyLabel"
                        visible: (!annotationsSection.selectedHit
                                  || annotationsSection.selectedHit.kind === "predicted")
                                 && !annotationsSection.showsBestScan
                        text: annotationsSection.selectedRow && annotationsSection.selectedRow.ms2_scan_id == null
                              ? "No MS2 spectrum for this feature."
                              : !annotationsSection.selectedHit
                              ? "No hit selected."
                              : "No MS2 spectrum — predicted formula only (ADR 27)."
                        color: Theme.mutedTextColor
                        anchors.centerIn: parent
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                    }

                    Image {
                        objectName: "basicPlotImage"
                        visible: (!!annotationsSection.selectedHit
                                  && annotationsSection.selectedHit.kind === "ms2")
                                 || annotationsSection.showsBestScan
                        anchors.fill: parent
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        source: annotationsSection.showsBestScan
                                ? annotationsSection.bestScanPlotImage
                                : annotationsSection.basicPlotImage
                    }
                    }
                }
            }
        }
    }

    // Lazy, so opening the Annotations tab never builds a second
    // top-level window nobody asked for ("Details" builds it on first use).
    Loader {
        id: mirrorPlotDetailWindowLoader
        objectName: "mirrorPlotDetailWindowLoader"
        active: false
        sourceComponent: MirrorPlotDetailWindow {
            objectName: "mirrorPlotDetailWindow"
        }
    }

    // Lazy for the same reason as mirrorPlotDetailWindowLoader above (a
    // second top-level window nobody asked for yet shouldn't be built
    // just because the Annotations tab opened).
    Loader {
        id: predictFormulaWindowLoader
        objectName: "predictFormulaWindowLoader"
        active: false
        sourceComponent: PredictFormulaWindow {
            objectName: "predictFormulaWindow"
        }
        onLoaded: {
            // A predicted formula can change a feature's representative
            // identity (ADR 27's 3-tier precedence) — refresh the table
            // and, if a feature is currently selected, its top-hits list
            // too, so the new "predicted" tier shows up without the user
            // having to reselect anything.
            item.predictionFinished.connect(function () {
                annotationsSection.refreshToken += 1
                annotationsSection.refreshTopHits()
            })
        }
    }
}

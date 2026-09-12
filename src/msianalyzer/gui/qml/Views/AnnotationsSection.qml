import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: annotationsSection
    objectName: "annotationsSection"

    property var analysis
    property var rows: (analysis && analysis.analysisDbPath)
                        ? AnalysisBridge.getAnnotationTable(analysis.analysisDbPath) : []

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

    readonly property var sortedRows: {
        var col = annotationsSection.sortColumn
        var dir = annotationsSection.sortAscending ? 1 : -1
        var arr = annotationsSection.rows.slice()
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
    // Always the stored *filtered* spectra, rendered as a plain PNG — no
    // WebEngineView/Chromium renderer for this inline panel at all. Raw
    // sources and the full interactive Plotly view only exist in the
    // "Details" popup (mirrorPlotDetailWindow below); see
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
        annotationsSection.basicPlotImage = (annotationsSection.selectedHit
                                              && analysis && analysis.analysisDbPath)
            ? AnalysisBridge.getBasicMirrorPlotImage(
                  analysis.analysisDbPath, annotationsSection.selectedHit.id)
            : ""
    }

    onSelectedFeatureIdChanged: refreshTopHits()
    onSelectedHitChanged: refreshBasicPlot()

    // "all the stats are written better (list, clear)" — a flat
    // label/value pair list (statsRepeater below lays it out as a
    // 2-column grid), built straight from the already-fetched
    // `selectedHit` — no extra bridge round trip needed.
    readonly property var statsRows: {
        var h = annotationsSection.selectedHit
        if (!h)
            return []
        var pairs = [
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

    Text {
        id: emptyStateLabel
        objectName: "annotationsEmptyStateLabel"
        visible: rows.length === 0
        anchors.centerIn: parent
        text: "No annotated features."
        color: "gray"
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
        visible: rows.length > 0
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

            Text {
                text: "Annotated features (" + annotationsSection.rows.length + ")"
                font.bold: true
                font.pixelSize: 14
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

                        Text {
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

            Flickable {
                id: tableFlickable
                Layout.fillWidth: true
                Layout.fillHeight: true
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
                            border.color: "#dddddd"
                            border.width: 1

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: annotationsSection.selectedFeatureId = modelData.feature_id
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 4

                                Text {
                                    objectName: "annotationRowFeature_" + modelData.feature_id
                                    text: String(modelData.feature_id)
                                    Layout.preferredWidth: 70
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Text {
                                    objectName: "annotationRowCompound_" + modelData.feature_id
                                    text: modelData.compound_name || modelData.inchikey || "?"
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Text {
                                    objectName: "annotationRowMz_" + modelData.feature_id
                                    text: modelData.mz !== null && modelData.mz !== undefined
                                          ? Number(modelData.mz).toFixed(4) : "?"
                                    Layout.preferredWidth: 90
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Text {
                                    objectName: "annotationRowScore_" + modelData.feature_id
                                    text: Number(modelData.best_score).toFixed(3)
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

        ColumnLayout {
            id: detailPanel
            objectName: "annotationsDetailPanel"
            height: annotationsSplit.height
            SplitView.fillWidth: true
            SplitView.minimumWidth: 320
            spacing: 12

            Text {
                objectName: "annotationsDetailEmptyLabel"
                visible: annotationsSection.selectedFeatureId < 0
                text: "Click a feature to see its top hits and mirror plot."
                color: "gray"
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
                        Text {
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

                    Text {
                        objectName: "topHitsEmptyLabel"
                        visible: annotationsSection.topHits.length === 0
                        text: "No annotation hits for this feature."
                        color: "gray"
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }

                    Flickable {
                        id: topHitsFlickable
                        objectName: "topHitsFlickable"
                        visible: annotationsSection.topHits.length > 0
                        Layout.fillWidth: true
                        Layout.fillHeight: true
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

                                    Text {
                                        objectName: "topHitLabel_" + modelData.id
                                        anchors.fill: parent
                                        anchors.leftMargin: 4
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        textFormat: Text.StyledText
                                        color: annotationsSection.selectedHit
                                               && annotationsSection.selectedHit.id === modelData.id
                                               ? palette.highlightedText : palette.text
                                        text: (index + 1) + ". "
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

                // Row 2 (of 3): the match's stats, as a plain label/value
                // list — "all the stats are written better (list,
                // clear)" — instead of packed into the interactive
                // plot's own title/metadata box (still there in the
                // "Details" popup's full Plotly view, just not the
                // primary way to read them here).
                ColumnLayout {
                    objectName: "statsPanel"
                    SplitView.preferredHeight: detailSplit.height * 0.3
                    SplitView.minimumHeight: 60
                    spacing: 4

                    Text {
                        text: "Match details:"
                        font.bold: true
                    }

                    Text {
                        objectName: "statsEmptyLabel"
                        visible: !annotationsSection.selectedHit
                        text: "No hit selected."
                        color: "gray"
                    }

                    Flickable {
                        objectName: "statsFlickable"
                        visible: !!annotationsSection.selectedHit
                        Layout.fillWidth: true
                        Layout.fillHeight: true
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

                                delegate: Text {
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

                // Row 3 (of 3): a fast static raster plot — "a basic plot
                // in that panel (no plotly)" — always the stored filtered
                // spectra. No WebEngineView here at all; the "Details"
                // button opens the full interactive Plotly view (with the
                // raw/filtered toggles) in its own window instead.
                ColumnLayout {
                    objectName: "basicPlotPanel"
                    SplitView.preferredHeight: detailSplit.height * 0.4
                    SplitView.minimumHeight: 150
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Mirror plot:"
                            font.bold: true
                            Layout.fillWidth: true
                        }
                        ToolButton {
                            objectName: "mirrorPlotDetailsButton"
                            text: "🔍 Details"
                            enabled: !!annotationsSection.selectedHit

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }

                            onClicked: {
                                // Lazily constructs mirrorPlotDetailWindowLoader.item on
                                // first click, not before — see the Loader's own comment.
                                mirrorPlotDetailWindowLoader.active = true
                                var win = mirrorPlotDetailWindowLoader.item
                                win.analysisDbPath = analysis.analysisDbPath
                                win.annotationId = annotationsSection.selectedHit.id
                                win.visible = true
                                win.raise()
                                win.requestActivate()
                            }
                        }
                    }

                    Text {
                        objectName: "basicPlotEmptyLabel"
                        visible: !annotationsSection.selectedHit
                        text: "No hit selected."
                        color: "gray"
                    }

                    Image {
                        objectName: "basicPlotImage"
                        visible: !!annotationsSection.selectedHit
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        source: annotationsSection.basicPlotImage
                    }
                }
            }
        }
    }

    // Lazy — a WebEngineView (inside MirrorPlotDetailWindow) starts its
    // Chromium renderer as soon as it's *constructed*, regardless of the
    // owning Window's own `visible` state; declaring it as a plain child
    // here got one spun up the moment the Annotations tab opened, even
    // if "Details" was never clicked. Same reasoning as MS1/Annotations/
    // VisualInspection themselves being lazy Loaders in AnalysisPage.qml
    // rather than eager StackLayout children.
    Loader {
        id: mirrorPlotDetailWindowLoader
        objectName: "mirrorPlotDetailWindowLoader"
        active: false
        sourceComponent: MirrorPlotDetailWindow {
            objectName: "mirrorPlotDetailWindow"
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"
import "qrc:/Utils/SearchQuery.js" as SearchQuery

Item {
    id: spatialCorrelationSection
    objectName: "spatialCorrelationSection"

    // Right-click a row -> "Show spatial distribution" -> jumps the
    // workspace to the Visual Inspection tab, already on this feature.
    // Handled by the owning AnalysisPage, same handoff Annotations'
    // inspectVisuallyRequested already uses (ADR 32) — see
    // AnalysisPage.qml's spatialCorrLoader.onLoaded.
    signal showSpatialDistributionRequested(real mz)

    property var analysis

    // Flat rows, one per (feature, sample) — {mz, sample, moran_i,
    // compound_name}. Fetched via AnalysisBridge, which caches the last
    // analysis' rows itself (see requestSpatialCorrelation's own
    // docstring) — this section is a lazy Loader (AnalysisPage.qml),
    // destroyed on every tab switch away from it, so the cache has to
    // live on the Python side to actually avoid re-reading every
    // sample's .h5ad on each visit back.
    property var rows: []
    property bool rowsLoading: false

    function refreshRows() {
        if (analysis && analysis.analysisDbPath) {
            spatialCorrelationSection.rowsLoading = true
            AnalysisBridge.requestSpatialCorrelation(analysis.analysisDbPath)
        } else {
            spatialCorrelationSection.rows = []
            spatialCorrelationSection.rowsLoading = false
        }
    }

    onAnalysisChanged: spatialCorrelationSection.refreshRows()

    Connections {
        target: AnalysisBridge
        function onSpatialCorrelationReady(receivedRows) {
            spatialCorrelationSection.rows = receivedRows
            spatialCorrelationSection.rowsLoading = false
        }
    }

    // Every distinct sample name that appears in `rows`, alphabetical —
    // drives the table's (dynamic, one-per-sample) columns.
    readonly property var sampleNames: {
        var seen = {}
        var names = []
        for (var i = 0; i < spatialCorrelationSection.rows.length; i++) {
            var s = spatialCorrelationSection.rows[i].sample
            if (!seen[s]) {
                seen[s] = true
                names.push(s)
            }
        }
        names.sort()
        return names
    }

    // One row per feature (mz), one column per sample, plus that
    // feature's representative compound name (same "annotated: name,
    // else m/z" convention Visual Inspection's feature picker uses —
    // HeatmapControlsPanel.qml's featureLabels).
    readonly property var featureRows: {
        var byMz = {}
        var order = []
        for (var i = 0; i < spatialCorrelationSection.rows.length; i++) {
            var r = spatialCorrelationSection.rows[i]
            var key = String(r.mz)
            if (!byMz[key]) {
                byMz[key] = {mz: r.mz, compound_name: r.compound_name || null}
                order.push(key)
            }
            byMz[key][r.sample] = r.moran_i
        }
        return order.map(function (key) { return byMz[key] })
    }

    function featureLabel(row) {
        return row.compound_name
               ? (Number(row.mz).toFixed(4) + ": " + row.compound_name)
               : ("m/z " + Number(row.mz).toFixed(4))
    }

    // Two separate fields (same convention as Annotate/Visual Inspection,
    // ADR 34/35) — name (contains) and m/z (a value or "min-max" range).
    property string nameQuery: ""
    property string mzQuery: ""

    readonly property var filteredFeatureRows: spatialCorrelationSection.featureRows.filter(function (r) {
        return SearchQuery.matchesName(spatialCorrelationSection.nameQuery, r.compound_name)
            && SearchQuery.matchesMz(spatialCorrelationSection.mzQuery, r.mz)
    })

    property string sortColumn: "mz"
    property bool sortAscending: true

    function sortBy(column) {
        if (spatialCorrelationSection.sortColumn === column) {
            spatialCorrelationSection.sortAscending = !spatialCorrelationSection.sortAscending
        } else {
            spatialCorrelationSection.sortColumn = column
            spatialCorrelationSection.sortAscending = true
        }
    }

    readonly property var sortedFeatureRows: {
        var col = spatialCorrelationSection.sortColumn
        var dir = spatialCorrelationSection.sortAscending ? 1 : -1
        var arr = spatialCorrelationSection.filteredFeatureRows.slice()
        arr.sort(function (a, b) {
            var av = a[col]
            var bv = b[col]
            if (av === bv) return 0
            if (av === undefined) return 1
            if (bv === undefined) return -1
            return dir * (av < bv ? -1 : 1)
        })
        return arr
    }

    readonly property string defaultFontFamily: Qt.application.font.family
    readonly property int featureColumnWidth: 240
    readonly property int sampleColumnWidth: 130
    readonly property int headerHeight: 56
    readonly property int rowHeight: 32

    readonly property var tableColumns: {
        var cols = [{key: "mz", label: "Feature", width: spatialCorrelationSection.featureColumnWidth}]
        var samples = spatialCorrelationSection.sampleNames
        for (var i = 0; i < samples.length; i++) {
            cols.push({key: samples[i], label: samples[i], width: spatialCorrelationSection.sampleColumnWidth})
        }
        return cols
    }

    readonly property int totalColumnsWidth: spatialCorrelationSection.tableColumns.reduce(
        function (sum, c) { return sum + c.width }, 0)

    Label {
        objectName: "spatialCorrelationEmptyStateLabel"
        visible: !spatialCorrelationSection.rowsLoading && spatialCorrelationSection.rows.length === 0
        anchors.centerIn: parent
        text: "No spatial correlation scores yet — enable `spatial_correlation` in the run config."
        color: Theme.mutedTextColor
        wrapMode: Text.Wrap
        width: parent.width * 0.6
        horizontalAlignment: Text.AlignHCenter
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        visible: spatialCorrelationSection.rows.length > 0
        spacing: 8

        RowLayout {
            Layout.fillWidth: true

            Label {
                text: "Spatial correlation (Moran's I) — " + spatialCorrelationSection.featureRows.length + " feature(s)"
                font.bold: true
                font.pixelSize: 14
                Layout.fillWidth: true
            }
            TextField {
                id: spatialCorrelationNameSearchField
                objectName: "spatialCorrelationNameSearchField"
                Layout.preferredWidth: 200
                placeholderText: "Search name…"
                text: spatialCorrelationSection.nameQuery
                onTextChanged: spatialCorrelationSection.nameQuery = text
            }
            TextField {
                id: spatialCorrelationMzSearchField
                objectName: "spatialCorrelationMzSearchField"
                Layout.preferredWidth: 100
                placeholderText: "m/z…"
                text: spatialCorrelationSection.mzQuery
                onTextChanged: spatialCorrelationSection.mzQuery = text
            }
        }

        Label {
            objectName: "spatialCorrelationSearchEmptyLabel"
            visible: spatialCorrelationSection.featureRows.length > 0
                     && spatialCorrelationSection.sortedFeatureRows.length === 0
            text: "No features match your search."
            color: Theme.mutedTextColor
            Layout.alignment: Qt.AlignHCenter
        }

        // Header — its own non-vertically-scrolling Flickable, horizontal
        // position slaved to the body's (headerFlickable.interactive is
        // false: it never scrolls on its own, only follows bodyFlickable
        // below) — a "frozen header row" over a scrolling body, both
        // moving together horizontally when there are more sample
        // columns than fit on screen (fixed column widths, per-column
        // scroll rather than squeezed-to-fit — "6 samples max, then
        // scrolling").
        Flickable {
            id: headerFlickable
            objectName: "spatialCorrelationHeaderFlickable"
            visible: spatialCorrelationSection.sortedFeatureRows.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: spatialCorrelationSection.headerHeight
            interactive: false
            clip: true
            contentWidth: Math.max(width, spatialCorrelationSection.totalColumnsWidth)
            contentX: bodyFlickable.contentX

            Row {
                id: headerRow
                height: spatialCorrelationSection.headerHeight

                Repeater {
                    model: spatialCorrelationSection.tableColumns

                    delegate: Rectangle {
                        objectName: "spatialCorrelationSortHeader_" + modelData.key
                        width: modelData.width
                        height: spatialCorrelationSection.headerHeight
                        color: "transparent"
                        border.color: palette.mid
                        border.width: 1

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: spatialCorrelationSection.sortBy(modelData.key)
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 4
                            spacing: 0

                            Label {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                text: modelData.label
                                font.bold: true
                                wrapMode: Text.WrapAnywhere
                                elide: Text.ElideRight
                                maximumLineCount: 3
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            Label {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                font.pixelSize: 11
                                text: spatialCorrelationSection.sortColumn === modelData.key
                                      ? (spatialCorrelationSection.sortAscending ? "▲" : "▼")
                                      : " "
                            }
                        }
                    }
                }
            }
        }

        // Body — scrolls both ways; horizontal scrollbar makes the
        // "more samples than fit -> scroll" affordance visible even
        // without a trackpad gesture.
        Flickable {
            id: bodyFlickable
            objectName: "spatialCorrelationBodyFlickable"
            visible: spatialCorrelationSection.sortedFeatureRows.length > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: Math.max(width, spatialCorrelationSection.totalColumnsWidth)
            contentHeight: spatialCorrelationSection.sortedFeatureRows.length * spatialCorrelationSection.rowHeight
            boundsBehavior: Flickable.StopAtBounds

            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: bodyColumn

                Repeater {
                    objectName: "spatialCorrelationTableRepeater"
                    model: spatialCorrelationSection.sortedFeatureRows

                    delegate: Rectangle {
                        id: featureRowDelegate
                        objectName: "spatialCorrelationRow_" + modelData.mz
                        // Captured at this outer-Repeater scope, before
                        // the inner Repeater's own `modelData` (one
                        // table column, not this row) shadows it below.
                        property var featureRow: modelData
                        width: spatialCorrelationSection.totalColumnsWidth
                        height: spatialCorrelationSection.rowHeight
                        color: "transparent"
                        border.color: palette.mid
                        border.width: 1

                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.RightButton
                            cursorShape: Qt.PointingHandCursor
                            onClicked: fullTableRowContextMenu.popup()
                        }

                        Menu {
                            id: fullTableRowContextMenu
                            objectName: "spatialCorrelationRowContextMenu_" + featureRowDelegate.featureRow.mz
                            MenuItem {
                                objectName: "spatialCorrelationShowDistribution_" + featureRowDelegate.featureRow.mz
                                text: "Show spatial distribution"
                                onTriggered: spatialCorrelationSection.showSpatialDistributionRequested(
                                    featureRowDelegate.featureRow.mz)
                            }
                        }

                        Row {
                            anchors.fill: parent

                            Repeater {
                                model: spatialCorrelationSection.tableColumns

                                delegate: Item {
                                    width: modelData.width
                                    height: spatialCorrelationSection.rowHeight

                                    Label {
                                        anchors.fill: parent
                                        anchors.leftMargin: 8
                                        anchors.rightMargin: 8
                                        verticalAlignment: Text.AlignVCenter
                                        horizontalAlignment: modelData.key === "mz" ? Text.AlignLeft : Text.AlignRight
                                        elide: Text.ElideRight
                                        font.family: modelData.key === "mz"
                                                      ? spatialCorrelationSection.defaultFontFamily : "monospace"
                                        text: {
                                            if (modelData.key === "mz")
                                                return spatialCorrelationSection.featureLabel(featureRowDelegate.featureRow)
                                            var v = featureRowDelegate.featureRow[modelData.key]
                                            return v !== undefined ? Number(v).toFixed(3) : "–"
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

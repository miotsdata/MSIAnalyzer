import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

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

    // Flat rows, one per (feature, sample) — {mz, sample, moran_i}.
    // Fetched on a background thread (real per-sample .h5ad file reads),
    // same reasoning/pattern as AnnotationsSection's rows/rowsLoading.
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
    // drives both the per-sample top-N blocks and the full table's
    // (dynamic, one-per-sample) columns.
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

    // "for each slide/sample (not overall)" — one independent top-N per
    // sample, not one global ranking across samples.
    property int topN: 5

    readonly property var topNBySample: {
        var result = {}
        var samples = spatialCorrelationSection.sampleNames
        for (var i = 0; i < samples.length; i++) {
            var name = samples[i]
            var forSample = spatialCorrelationSection.rows.filter(function (r) {
                return r.sample === name
            })
            forSample.sort(function (a, b) { return b.moran_i - a.moran_i })
            result[name] = forSample.slice(0, spatialCorrelationSection.topN)
        }
        return result
    }

    // Full table: one row per feature (mz), one column per sample.
    readonly property var featureRows: {
        var byMz = {}
        var order = []
        for (var i = 0; i < spatialCorrelationSection.rows.length; i++) {
            var r = spatialCorrelationSection.rows[i]
            var key = String(r.mz)
            if (!byMz[key]) {
                byMz[key] = {mz: r.mz}
                order.push(key)
            }
            byMz[key][r.sample] = r.moran_i
        }
        return order.map(function (key) { return byMz[key] })
    }

    readonly property var tableColumns: {
        var cols = [{key: "mz", label: "m/z", width: 90}]
        var samples = spatialCorrelationSection.sampleNames
        for (var i = 0; i < samples.length; i++) {
            cols.push({key: samples[i], label: samples[i], width: -1})
        }
        return cols
    }

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
        var arr = spatialCorrelationSection.featureRows.slice()
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

    SplitView {
        id: spatialCorrelationSplit
        objectName: "spatialCorrelationSplit"
        anchors.fill: parent
        anchors.margins: 16
        visible: spatialCorrelationSection.rows.length > 0
        orientation: Qt.Vertical

        // Top: per-sample top-N ranked lists.
        ColumnLayout {
            id: topNPanel
            objectName: "spatialCorrelationTopNPanel"
            SplitView.preferredHeight: spatialCorrelationSplit.height * 0.45
            SplitView.minimumHeight: 150
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "Top features by Moran's I, per sample:"
                    font.bold: true
                    font.pixelSize: 14
                    Layout.fillWidth: true
                }
                SpinBox {
                    id: topNSpin
                    objectName: "spatialCorrelationTopNSpinBox"
                    editable: true
                    from: 1
                    to: 50
                    value: spatialCorrelationSection.topN
                    onValueModified: spatialCorrelationSection.topN = value
                }
            }

            Flickable {
                id: topNFlickable
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: width
                contentHeight: topNRow.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                RowLayout {
                    id: topNRow
                    width: topNFlickable.width
                    spacing: 12

                    Repeater {
                        id: sampleBlocksRepeater
                        objectName: "spatialCorrelationSampleBlocks"
                        model: spatialCorrelationSection.sampleNames

                        delegate: ColumnLayout {
                            objectName: "spatialCorrelationSampleBlock_" + modelData
                            Layout.preferredWidth: 220
                            Layout.fillHeight: true
                            spacing: 2

                            Label {
                                text: modelData
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }

                            Repeater {
                                model: spatialCorrelationSection.topNBySample[modelData] || []

                                delegate: Rectangle {
                                    objectName: "spatialCorrelationTopNRow_" + modelData.sample + "_" + index
                                    Layout.fillWidth: true
                                    height: 26
                                    radius: 4
                                    color: "transparent"
                                    border.color: palette.mid
                                    border.width: 1

                                    MouseArea {
                                        anchors.fill: parent
                                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: (mouse) => {
                                            if (mouse.button === Qt.RightButton) {
                                                topNContextMenu.popup()
                                            }
                                        }
                                    }

                                    Menu {
                                        id: topNContextMenu
                                        MenuItem {
                                            objectName: "spatialCorrelationTopNShowDistribution_" + modelData.sample + "_" + index
                                            text: "Show spatial distribution"
                                            onTriggered: spatialCorrelationSection.showSpatialDistributionRequested(modelData.mz)
                                        }
                                    }

                                    Label {
                                        anchors.fill: parent
                                        anchors.leftMargin: 6
                                        anchors.rightMargin: 6
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        text: (index + 1) + ". " + Number(modelData.mz).toFixed(4)
                                              + "  (I=" + Number(modelData.moran_i).toFixed(3) + ")"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // Bottom: full sortable features x samples table.
        ColumnLayout {
            id: fullTablePanel
            objectName: "spatialCorrelationFullTablePanel"
            SplitView.fillHeight: true
            SplitView.minimumHeight: 150
            spacing: 4

            Label {
                text: "All features (" + spatialCorrelationSection.featureRows.length + ")"
                font.bold: true
                font.pixelSize: 14
            }

            RowLayout {
                objectName: "spatialCorrelationTableHeader"
                Layout.fillWidth: true
                spacing: 4

                Repeater {
                    model: spatialCorrelationSection.tableColumns

                    delegate: MouseArea {
                        objectName: "spatialCorrelationSortHeader_" + modelData.key
                        Layout.preferredWidth: modelData.width > 0 ? modelData.width : -1
                        Layout.fillWidth: modelData.width < 0
                        implicitHeight: headerLabel.implicitHeight
                        cursorShape: Qt.PointingHandCursor
                        onClicked: spatialCorrelationSection.sortBy(modelData.key)

                        Label {
                            id: headerLabel
                            anchors.fill: parent
                            font.bold: true
                            elide: Text.ElideRight
                            text: modelData.label
                                  + (spatialCorrelationSection.sortColumn === modelData.key
                                     ? (spatialCorrelationSection.sortAscending ? " ▲" : " ▼")
                                     : "")
                        }
                    }
                }
            }

            Flickable {
                id: fullTableFlickable
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: width
                contentHeight: spatialCorrelationSection.sortedFeatureRows.length > 0
                    ? spatialCorrelationSection.sortedFeatureRows.length * 32
                      + (spatialCorrelationSection.sortedFeatureRows.length - 1) * fullTableColumn.spacing
                    : 0
                boundsBehavior: Flickable.StopAtBounds

                Column {
                    id: fullTableColumn
                    width: fullTableFlickable.width
                    spacing: 4

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
                            width: fullTableColumn.width
                            height: 32
                            radius: 4
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

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 6
                                spacing: 4

                                Repeater {
                                    model: spatialCorrelationSection.tableColumns

                                    delegate: Label {
                                        Layout.preferredWidth: modelData.width > 0 ? modelData.width : -1
                                        Layout.fillWidth: modelData.width < 0
                                        elide: Text.ElideRight
                                        text: {
                                            if (modelData.key === "mz")
                                                return Number(featureRowDelegate.featureRow.mz).toFixed(4)
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

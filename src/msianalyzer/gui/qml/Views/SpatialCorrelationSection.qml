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

    // The persistent model backing this page — a plain QObject owned by
    // AnalysisBridge (SpatialCorrelationModel), not something this
    // section constructs or holds any real state in itself. This
    // section is a lazy Loader (AnalysisPage.qml's spatialCorrLoader,
    // `active` only while its tab is current) — its whole item is
    // destroyed on every tab switch away, so `model`'s rows/sort
    // column/search text living in Python (surviving that destruction)
    // is what actually makes "switch away and back" instant and
    // state-preserving, rather than a local `property var rows` that
    // would just reset every time. See getSpatialCorrelationModel's own
    // docstring for the full reasoning (including why this is a plain
    // QObject + Repeater/Flickable, not a TableView + QAbstractTableModel
    // — the latter doesn't render under this project's GUI test harness).
    property var model: (analysis && analysis.analysisDbPath)
                         ? AnalysisBridge.getSpatialCorrelationModel(analysis.analysisDbPath)
                         : null

    property bool rowsLoading: spatialCorrelationSection.model
                                ? spatialCorrelationSection.model.rowsLoading : false
    readonly property var visibleRows: spatialCorrelationSection.model
                                        ? spatialCorrelationSection.model.visibleRows : []
    readonly property var sampleNames: spatialCorrelationSection.model
                                        ? spatialCorrelationSection.model.samples : []
    readonly property string sortColumn: spatialCorrelationSection.model
                                          ? spatialCorrelationSection.model.sortColumn : "mz"
    readonly property bool sortAscending: spatialCorrelationSection.model
                                           ? spatialCorrelationSection.model.sortAscending : true

    function sortBy(column) {
        if (spatialCorrelationSection.model) spatialCorrelationSection.model.sortBy(column)
    }

    readonly property int featureColumnWidth: 240
    readonly property int sampleColumnWidth: 130
    readonly property int headerHeight: 56
    readonly property int rowHeight: 32
    readonly property string defaultFontFamily: Qt.application.font.family

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
        // No samples ever had `spatial_correlation` run on them for this
        // analysis (or there's no analysis/model at all yet) — distinct
        // from "the table is there but a search matched nothing", which
        // spatialCorrelationSearchEmptyLabel below covers instead.
        visible: !spatialCorrelationSection.rowsLoading
                 && (!spatialCorrelationSection.model
                     || spatialCorrelationSection.model.samples.length === 0)
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
        visible: spatialCorrelationSection.model && spatialCorrelationSection.model.samples.length > 0
        spacing: 8

        RowLayout {
            Layout.fillWidth: true

            Label {
                text: "Spatial correlation (Moran's I) — " + spatialCorrelationSection.visibleRows.length + " feature(s)"
                font.bold: true
                font.pixelSize: 14
                Layout.fillWidth: true
            }
            TextField {
                id: spatialCorrelationNameSearchField
                objectName: "spatialCorrelationNameSearchField"
                Layout.preferredWidth: 200
                placeholderText: "Search name…"
                text: spatialCorrelationSection.model ? spatialCorrelationSection.model.nameQuery : ""
                onTextChanged: if (spatialCorrelationSection.model) spatialCorrelationSection.model.nameQuery = text
            }
            TextField {
                id: spatialCorrelationMzSearchField
                objectName: "spatialCorrelationMzSearchField"
                Layout.preferredWidth: 100
                placeholderText: "m/z…"
                text: spatialCorrelationSection.model ? spatialCorrelationSection.model.mzQuery : ""
                onTextChanged: if (spatialCorrelationSection.model) spatialCorrelationSection.model.mzQuery = text
            }
        }

        Label {
            objectName: "spatialCorrelationSearchEmptyLabel"
            visible: spatialCorrelationSection.model
                     && spatialCorrelationSection.model.samples.length > 0
                     && spatialCorrelationSection.visibleRows.length === 0
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
            visible: spatialCorrelationSection.visibleRows.length > 0
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
            visible: spatialCorrelationSection.visibleRows.length > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: Math.max(width, spatialCorrelationSection.totalColumnsWidth)
            contentHeight: spatialCorrelationSection.visibleRows.length * spatialCorrelationSection.rowHeight
            boundsBehavior: Flickable.StopAtBounds

            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: bodyColumn

                Repeater {
                    objectName: "spatialCorrelationTableRepeater"
                    model: spatialCorrelationSection.visibleRows

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
                                                return featureRowDelegate.featureRow.label
                                            var v = featureRowDelegate.featureRow[modelData.key]
                                            return (v !== undefined && v !== null) ? Number(v).toFixed(3) : "–"
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

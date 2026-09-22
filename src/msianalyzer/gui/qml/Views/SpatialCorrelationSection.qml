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

    // The persistent model backing this page — a QAbstractTableModel
    // owned by AnalysisBridge (SpatialCorrelationModel), not something
    // this section constructs or holds any real state in itself. This
    // section is a lazy Loader (AnalysisPage.qml's spatialCorrLoader,
    // `active` only while its tab is current) — its whole item is
    // destroyed on every tab switch away, so `model`'s rows/sort
    // column/search text living in Python (surviving that destruction)
    // is what actually makes "switch away and back" instant and
    // state-preserving, rather than a local `property var rows` that
    // would just reset every time. Rendered via TableView (not a
    // Repeater, per an earlier version of this page) so sorting only
    // touches visible cells, not every row's delegate — see
    // getSpatialCorrelationModel's/SpatialCorrelationModel's own
    // docstrings for the full reasoning.
    property var model: (analysis && analysis.analysisDbPath)
                         ? AnalysisBridge.getSpatialCorrelationModel(analysis.analysisDbPath)
                         : null

    property bool rowsLoading: spatialCorrelationSection.model
                                ? spatialCorrelationSection.model.rowsLoading : false
    readonly property var sampleNames: spatialCorrelationSection.model
                                        ? spatialCorrelationSection.model.samples : []
    readonly property string sortColumn: spatialCorrelationSection.model
                                          ? spatialCorrelationSection.model.sortColumn : "mz"
    readonly property bool sortAscending: spatialCorrelationSection.model
                                           ? spatialCorrelationSection.model.sortAscending : true
    readonly property int rowCountVisible: spatialCorrelationSection.model
                                            ? spatialCorrelationSection.model.rowCount() : 0

    function sortBy(column) {
        if (spatialCorrelationSection.model) spatialCorrelationSection.model.sortBy(column)
    }

    readonly property string defaultFontFamily: Qt.application.font.family
    readonly property int featureColumnWidth: 240
    readonly property int sampleColumnWidth: 130
    readonly property int headerHeight: 56
    readonly property int rowHeight: 32

    function columnWidth(column) {
        return column === 0 ? spatialCorrelationSection.featureColumnWidth
                             : spatialCorrelationSection.sampleColumnWidth
    }

    Label {
        objectName: "spatialCorrelationEmptyStateLabel"
        // No samples ever had `spatial_correlation` run on them for this
        // analysis (or there's no analysis/model at all yet) — distinct
        // from "the table is there but a search matched nothing", which
        // spatialCorrelationSearchEmptyLabel below covers instead.
        visible: !spatialCorrelationSection.rowsLoading
                 && (!spatialCorrelationSection.model
                     || spatialCorrelationSection.sampleNames.length === 0)
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
        visible: spatialCorrelationSection.sampleNames.length > 0
        spacing: 8

        RowLayout {
            Layout.fillWidth: true

            Label {
                text: "Spatial correlation (Moran's I) — " + spatialCorrelationSection.rowCountVisible + " feature(s)"
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
            visible: spatialCorrelationSection.sampleNames.length > 0
                     && spatialCorrelationSection.rowCountVisible === 0
            text: "No features match your search."
            color: Theme.mutedTextColor
            Layout.alignment: Qt.AlignHCenter
        }

        // Header — HorizontalHeaderView keeps itself horizontally synced
        // to tableView below (syncView); click-to-sort is hand-rolled
        // (no native sort-indicator support in this Qt/PySide version —
        // confirmed directly, nothing to wire up instead of building).
        HorizontalHeaderView {
            id: headerView
            objectName: "spatialCorrelationHeaderView"
            visible: spatialCorrelationSection.rowCountVisible > 0
            Layout.fillWidth: true
            syncView: tableView
            clip: true

            delegate: Rectangle {
                id: headerCell
                objectName: "spatialCorrelationSortHeader_" + column
                implicitWidth: spatialCorrelationSection.columnWidth(column)
                implicitHeight: spatialCorrelationSection.headerHeight
                color: "transparent"
                border.color: palette.mid
                border.width: 1

                required property int column
                required property var display

                readonly property string columnKey: column === 0 ? "mz" : spatialCorrelationSection.sampleNames[column - 1]

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: spatialCorrelationSection.sortBy(headerCell.columnKey)
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 4
                    spacing: 0

                    Label {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: headerCell.display
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
                        text: spatialCorrelationSection.sortColumn === headerCell.columnKey
                              ? (spatialCorrelationSection.sortAscending ? "▲" : "▼")
                              : " "
                    }
                }
            }
        }

        // Body — TableView only ever instantiates delegates for the
        // visible viewport (plus a small overscan buffer), regardless of
        // total row count — sorting/filtering only has to redraw what's
        // on screen, not rebuild every row (the actual fix for "ordering
        // the table is still super slow": the previous Repeater-based
        // version rebuilt every row's delegate on every sort).
        TableView {
            id: tableView
            objectName: "spatialCorrelationTableView"
            visible: spatialCorrelationSection.rowCountVisible > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            columnSpacing: 0
            rowSpacing: 0
            model: spatialCorrelationSection.model
            columnWidthProvider: function (column) { return spatialCorrelationSection.columnWidth(column) }
            rowHeightProvider: function (row) { return spatialCorrelationSection.rowHeight }

            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: Rectangle {
                id: cellDelegate
                objectName: "spatialCorrelationCell_" + row + "_" + column
                implicitWidth: spatialCorrelationSection.columnWidth(column)
                implicitHeight: spatialCorrelationSection.rowHeight
                color: "transparent"
                border.color: palette.mid
                border.width: 1

                required property int row
                required property int column
                required property var display
                required property real mz

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.RightButton
                    cursorShape: Qt.PointingHandCursor
                    onClicked: rowContextMenu.popup()
                }

                Menu {
                    id: rowContextMenu
                    objectName: "spatialCorrelationRowContextMenu_" + cellDelegate.row
                    MenuItem {
                        objectName: "spatialCorrelationShowDistribution_" + cellDelegate.row
                        text: "Show spatial distribution"
                        onTriggered: spatialCorrelationSection.showSpatialDistributionRequested(cellDelegate.mz)
                    }
                }

                Label {
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: cellDelegate.column === 0 ? Text.AlignLeft : Text.AlignRight
                    elide: Text.ElideRight
                    font.family: cellDelegate.column === 0
                                 ? spatialCorrelationSection.defaultFontFamily : "monospace"
                    text: cellDelegate.display
                }
            }
        }
    }
}

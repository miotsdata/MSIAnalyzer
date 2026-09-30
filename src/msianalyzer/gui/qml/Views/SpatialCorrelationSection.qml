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
    // Same handoff, from the local-summary table below — also switches
    // Visual Inspection's heatmap layer to Local Moran's I, not just the
    // feature (see AnalysisPage.qml's own handling of this signal).
    signal showLocalSpatialDistributionRequested(real mz)

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
    //
    // Both models follow the active dataset (ADR 49): the bridge caches
    // one per (analysis, dataset). Reading activeDatasetId/modelRevision
    // here re-evaluates the binding on a dataset switch and after "Compute
    // Moran's I" rewrote the dataset's files.
    property int modelRevision: 0
    property var model: (analysis && analysis.analysisDbPath
                         && AnalysisBridge && AnalysisBridge.activeDatasetId >= -1 && modelRevision >= 0)
                         ? AnalysisBridge.getSpatialCorrelationModel(analysis.analysisDbPath)
                         : null

    // Same model shape, backed by the local Moran's I summary instead
    // (`spatial_correlation.local_enabled`) — see
    // AnalysisBridge.getLocalMoranSummaryModel. A second, independent
    // table below the global one; gated on `local_enabled` having
    // actually run for a sample the same way the global table is gated
    // on `enabled`, not on each other.
    property var localModel: (analysis && analysis.analysisDbPath
                              && AnalysisBridge && AnalysisBridge.activeDatasetId >= -1 && modelRevision >= 0)
                              ? AnalysisBridge.getLocalMoranSummaryModel(analysis.analysisDbPath)
                              : null

    // --- Moran's I on an imported dataset (ADR 49) --------------------
    // The pipeline computes Moran's I during Run (config
    // `spatial_correlation`). An imported notebook dataset has no Run, so
    // it gets an explicit compute action instead (the user's choice: "a
    // button after import").
    readonly property bool datasetActive: (AnalysisBridge ? AnalysisBridge.activeDatasetId : -1) >= 0
    property var datasetInfo: ({})
    property bool computing: false
    property int computeDone: 0
    property int computeTotal: 0
    property string computeMessage: ""
    property bool computeFailed: false

    function refreshDatasetInfo() {
        spatialCorrelationSection.datasetInfo = spatialCorrelationSection.datasetActive
            ? AnalysisBridge.getActiveDatasetInfo() : ({})
        spatialCorrelationSection.computeMessage = ""
    }
    Component.onCompleted: spatialCorrelationSection.refreshDatasetInfo()

    function computeMoran() {
        spatialCorrelationSection.computing = true
        spatialCorrelationSection.computeFailed = false
        spatialCorrelationSection.computeMessage = ""
        spatialCorrelationSection.computeDone = 0
        spatialCorrelationSection.computeTotal = (spatialCorrelationSection.datasetInfo.samples || []).length
        AnalysisBridge.computeDatasetMoran(analysis.analysisDbPath, moranLayerCombo.currentText,
                                           globalMoranCheck.checked, localMoranCheck.checked)
    }

    Connections {
        target: AnalysisBridge
        function onActiveDatasetChanged() { spatialCorrelationSection.refreshDatasetInfo() }
        function onDatasetTaskProgress(done, total, sampleName) {
            if (!spatialCorrelationSection.computing) return
            spatialCorrelationSection.computeDone = done
            spatialCorrelationSection.computeTotal = total
        }
        function onDatasetMoranFinished(message) {
            if (!spatialCorrelationSection.computing) return
            spatialCorrelationSection.computing = false
            spatialCorrelationSection.computeMessage = message
            spatialCorrelationSection.modelRevision += 1
        }
        function onDatasetMoranFailed(message) {
            if (!spatialCorrelationSection.computing) return
            spatialCorrelationSection.computing = false
            spatialCorrelationSection.computeFailed = true
            spatialCorrelationSection.computeMessage = message
        }
    }

    // True while *either* table's fetch is still in flight — AnalysisPage's
    // currentSectionReady reads this one property, so both tables gate the
    // same loading overlay without AnalysisPage needing to know there are
    // two of them.
    property bool rowsLoading: (spatialCorrelationSection.model
                                 && spatialCorrelationSection.model.rowsLoading)
                                || (spatialCorrelationSection.localModel
                                    && spatialCorrelationSection.localModel.rowsLoading)
    readonly property var sampleNames: spatialCorrelationSection.model
                                        ? spatialCorrelationSection.model.samples : []
    readonly property string sortColumn: spatialCorrelationSection.model
                                          ? spatialCorrelationSection.model.sortColumn : "mz"
    readonly property bool sortAscending: spatialCorrelationSection.model
                                           ? spatialCorrelationSection.model.sortAscending : true
    readonly property int rowCountVisible: spatialCorrelationSection.model
                                            ? spatialCorrelationSection.model.rowCount() : 0

    readonly property var localSampleNames: spatialCorrelationSection.localModel
                                             ? spatialCorrelationSection.localModel.samples : []
    readonly property string localSortColumn: spatialCorrelationSection.localModel
                                               ? spatialCorrelationSection.localModel.sortColumn : "mz"
    readonly property bool localSortAscending: spatialCorrelationSection.localModel
                                                ? spatialCorrelationSection.localModel.sortAscending : true
    readonly property int localRowCountVisible: spatialCorrelationSection.localModel
                                                 ? spatialCorrelationSection.localModel.rowCount() : 0

    function sortBy(column) {
        if (spatialCorrelationSection.model) spatialCorrelationSection.model.sortBy(column)
    }

    function sortLocalBy(column) {
        if (spatialCorrelationSection.localModel) spatialCorrelationSection.localModel.sortBy(column)
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

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 16

        // Imported dataset only: compute Moran's I over its own files.
        RowLayout {
            objectName: "datasetMoranBar"
            visible: spatialCorrelationSection.datasetActive
            Layout.fillWidth: true
            spacing: 8

            Label {
                text: "Dataset \"" + (spatialCorrelationSection.datasetInfo.name || "") + "\" — layer:"
                font.bold: true
            }
            ComboBox {
                id: moranLayerCombo
                objectName: "datasetMoranLayerCombo"
                enabled: !spatialCorrelationSection.computing
                model: ["X"].concat((spatialCorrelationSection.datasetInfo.layers || [])
                                    .filter(function (l) { return l !== "localMoranI" }))
                // Same default the pipeline stage uses, when present.
                currentIndex: Math.max(0, model.indexOf("TIC"))
                ToolTip.visible: hovered
                ToolTip.text: "Which intensities to score: \"X\" is the imported file's own main matrix."
            }
            CheckBox {
                id: globalMoranCheck
                objectName: "datasetGlobalMoranCheck"
                text: "Global"
                checked: true
                enabled: !spatialCorrelationSection.computing
            }
            CheckBox {
                id: localMoranCheck
                objectName: "datasetLocalMoranCheck"
                text: "Local"
                enabled: !spatialCorrelationSection.computing
            }
            Button {
                objectName: "datasetComputeMoranButton"
                text: "Compute Moran's I"
                enabled: !spatialCorrelationSection.computing
                         && (globalMoranCheck.checked || localMoranCheck.checked)
                onClicked: spatialCorrelationSection.computeMoran()
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            ProgressBar {
                objectName: "datasetMoranProgressBar"
                visible: spatialCorrelationSection.computing
                Layout.preferredWidth: 160
                from: 0
                to: Math.max(1, spatialCorrelationSection.computeTotal)
                value: spatialCorrelationSection.computeDone
                indeterminate: spatialCorrelationSection.computeDone === 0
            }
            Label {
                objectName: "datasetMoranMessageLabel"
                visible: spatialCorrelationSection.computeMessage !== ""
                text: spatialCorrelationSection.computeMessage
                color: spatialCorrelationSection.computeFailed ? Theme.errorColor : Theme.mutedTextColor
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Item { Layout.fillWidth: true; visible: spatialCorrelationSection.computeMessage === "" }
        }

        // ============================================================
        // Global Moran's I — one score per feature per sample.
        // ============================================================
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            Label {
                objectName: "spatialCorrelationEmptyStateLabel"
                // No samples ever had `spatial_correlation` run on them
                // for this analysis (or there's no analysis/model at all
                // yet) — distinct from "the table is there but a search
                // matched nothing", which spatialCorrelationSearchEmptyLabel
                // below covers instead.
                visible: !spatialCorrelationSection.rowsLoading
                         && (!spatialCorrelationSection.model
                             || spatialCorrelationSection.sampleNames.length === 0)
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: spatialCorrelationSection.datasetActive
                      ? "No Moran's I scores for this imported dataset yet — use \"Compute Moran's I\" above."
                      : "No spatial correlation scores yet — enable `spatial_correlation` in the run config."
                color: Theme.mutedTextColor
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
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

                // Header — HorizontalHeaderView keeps itself horizontally
                // synced to tableView below (syncView); click-to-sort is
                // hand-rolled (no native sort-indicator support in this
                // Qt/PySide version — confirmed directly, nothing to wire
                // up instead of building).
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

                // Body — TableView only ever instantiates delegates for
                // the visible viewport (plus a small overscan buffer),
                // regardless of total row count — sorting/filtering only
                // has to redraw what's on screen, not rebuild every row
                // (the actual fix for "ordering the table is still super
                // slow": the previous Repeater-based version rebuilt
                // every row's delegate on every sort).
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

        // ============================================================
        // Local Moran's I — per feature, % of pixels above threshold.
        // Same shape as the table above, deliberately not factored into
        // a shared component: only two of these exist, and each is small
        // enough on its own that a shared abstraction wouldn't meaningfully
        // reduce the QML here (see LocalMoranSummaryModel's own docstring
        // for the equivalent reasoning on the Python side).
        // ============================================================
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            Label {
                objectName: "localMoranSummaryEmptyStateLabel"
                visible: !spatialCorrelationSection.rowsLoading
                         && (!spatialCorrelationSection.localModel
                             || spatialCorrelationSection.localSampleNames.length === 0)
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "No local Moran's I summary yet — enable `spatial_correlation.local_enabled` in the run config."
                color: Theme.mutedTextColor
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: spatialCorrelationSection.localSampleNames.length > 0
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true

                    Label {
                        text: "Local Moran's I — % of pixels above threshold — "
                              + spatialCorrelationSection.localRowCountVisible + " feature(s)"
                        font.bold: true
                        font.pixelSize: 14
                        Layout.fillWidth: true
                    }
                    Label {
                        text: "Threshold:"
                    }
                    TextField {
                        id: localMoranThresholdField
                        objectName: "localMoranThresholdField"
                        Layout.preferredWidth: 70
                        // Seeded from SpatialCorrelationConfig.local_threshold
                        // (this analysis' own run) via
                        // AnalysisBridge.getLocalMoranSummaryModel — editing
                        // this only re-thresholds the already-loaded data
                        // (LocalMoranSummaryModel.threshold), no re-fetch,
                        // no re-run. Committed on Enter/focus-loss (a
                        // numeric field, unlike the live-as-you-type search
                        // fields below), same convention as
                        // HeatmapControlsPanel.qml's vmin/vmax fields.
                        text: spatialCorrelationSection.localModel
                              ? spatialCorrelationSection.localModel.threshold.toFixed(2) : ""
                        onEditingFinished: {
                            if (!spatialCorrelationSection.localModel) return
                            var v = parseFloat(text)
                            if (!isNaN(v)) spatialCorrelationSection.localModel.threshold = v
                        }
                    }
                    TextField {
                        id: localMoranSummaryNameSearchField
                        objectName: "localMoranSummaryNameSearchField"
                        Layout.preferredWidth: 200
                        placeholderText: "Search name…"
                        text: spatialCorrelationSection.localModel ? spatialCorrelationSection.localModel.nameQuery : ""
                        onTextChanged: if (spatialCorrelationSection.localModel) spatialCorrelationSection.localModel.nameQuery = text
                    }
                    TextField {
                        id: localMoranSummaryMzSearchField
                        objectName: "localMoranSummaryMzSearchField"
                        Layout.preferredWidth: 100
                        placeholderText: "m/z…"
                        text: spatialCorrelationSection.localModel ? spatialCorrelationSection.localModel.mzQuery : ""
                        onTextChanged: if (spatialCorrelationSection.localModel) spatialCorrelationSection.localModel.mzQuery = text
                    }
                }

                Label {
                    objectName: "localMoranSummarySearchEmptyLabel"
                    visible: spatialCorrelationSection.localSampleNames.length > 0
                             && spatialCorrelationSection.localRowCountVisible === 0
                    text: "No features match your search."
                    color: Theme.mutedTextColor
                    Layout.alignment: Qt.AlignHCenter
                }

                HorizontalHeaderView {
                    id: localHeaderView
                    objectName: "localMoranSummaryHeaderView"
                    visible: spatialCorrelationSection.localRowCountVisible > 0
                    Layout.fillWidth: true
                    syncView: localTableView
                    clip: true

                    delegate: Rectangle {
                        id: localHeaderCell
                        objectName: "localMoranSummarySortHeader_" + column
                        implicitWidth: spatialCorrelationSection.columnWidth(column)
                        implicitHeight: spatialCorrelationSection.headerHeight
                        color: "transparent"
                        border.color: palette.mid
                        border.width: 1

                        required property int column
                        required property var display

                        readonly property string columnKey: column === 0 ? "mz" : spatialCorrelationSection.localSampleNames[column - 1]

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: spatialCorrelationSection.sortLocalBy(localHeaderCell.columnKey)
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 4
                            spacing: 0

                            Label {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                text: localHeaderCell.display
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
                                text: spatialCorrelationSection.localSortColumn === localHeaderCell.columnKey
                                      ? (spatialCorrelationSection.localSortAscending ? "▲" : "▼")
                                      : " "
                            }
                        }
                    }
                }

                TableView {
                    id: localTableView
                    objectName: "localMoranSummaryTableView"
                    visible: spatialCorrelationSection.localRowCountVisible > 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    columnSpacing: 0
                    rowSpacing: 0
                    model: spatialCorrelationSection.localModel
                    columnWidthProvider: function (column) { return spatialCorrelationSection.columnWidth(column) }
                    rowHeightProvider: function (row) { return spatialCorrelationSection.rowHeight }

                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        id: localCellDelegate
                        objectName: "localMoranSummaryCell_" + row + "_" + column
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
                            onClicked: localRowContextMenu.popup()
                        }

                        Menu {
                            id: localRowContextMenu
                            objectName: "localMoranSummaryRowContextMenu_" + localCellDelegate.row
                            MenuItem {
                                objectName: "localMoranSummaryShowDistribution_" + localCellDelegate.row
                                text: "Show spatial distribution"
                                onTriggered: spatialCorrelationSection.showLocalSpatialDistributionRequested(localCellDelegate.mz)
                            }
                        }

                        Label {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: localCellDelegate.column === 0 ? Text.AlignLeft : Text.AlignRight
                            elide: Text.ElideRight
                            font.family: localCellDelegate.column === 0
                                         ? spatialCorrelationSection.defaultFontFamily : "monospace"
                            text: localCellDelegate.display
                        }
                    }
                }
            }
        }
    }
}

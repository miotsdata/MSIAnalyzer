import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

Item {
    id: summarySection
    objectName: "summarySection"

    property var analysis
    // Fetched on a background thread (requestSummary / summaryReady): the
    // counts aggregate over every MS2 annotation and froze the GUI for
    // seconds on a large analysis (ADR 50). `statsLoading` gates
    // AnalysisPage's loading overlay via currentSectionReady.
    property var stats: null
    property bool statsLoading: false

    function refreshStats() {
        if (analysis && analysis.analysisDbPath && AnalysisBridge) {
            summarySection.statsLoading = true
            AnalysisBridge.requestSummary(analysis.analysisDbPath)
        } else {
            summarySection.stats = null
            summarySection.statsLoading = false
        }
    }
    onAnalysisChanged: summarySection.refreshStats()
    Component.onCompleted: summarySection.refreshStats()

    Connections {
        target: AnalysisBridge
        // Names, outcomes or MS2 tables changed (post-run target list,
        // re-annotate, restore): the cached counts were dropped.
        function onTargetListMatchesChanged(dbPath) {
            if (summarySection.analysis && dbPath === summarySection.analysis.analysisDbPath)
                summarySection.refreshStats()
        }
        function onSummaryReady(dbPath, counts) {
            if (!summarySection.analysis || dbPath !== summarySection.analysis.analysisDbPath)
                return
            summarySection.stats = counts
            summarySection.statsLoading = false
        }
    }
    // Distinct from "stats is null" (no db path resolved at all) — an
    // existing-but-empty analysis (0 samples, 0 features) also counts as
    // "nothing to show" for the empty-state message.
    readonly property var background: stats && stats.ms2_background ? stats.ms2_background : []
    readonly property bool hasData: !!stats && (stats.n_samples > 0 || stats.n_features > 0)

    readonly property var statDefs: [
        { key: "n_samples", label: "Samples" },
        { key: "n_features", label: "Features" },
        { key: "n_ms2_associated_features", label: "Features with MS2" },
        { key: "n_annotated_features", label: "Annotated features" },
        { key: "n_distinct_compounds", label: "Distinct compounds" },
        { key: "n_precursor_matched_features", label: "Precursor-matched features" }
    ]

    // Where MS2 library annotation lost each feature (ADR 0062), in
    // pipeline order. Only shown once a run has recorded outcomes.
    readonly property var outcomeDefs: [
        { key: "n_outcome_no_ms2", label: "No MS2" },
        { key: "n_outcome_all_scans_skipped", label: "All MS2 scans skipped" },
        { key: "n_outcome_no_candidates", label: "No library candidate" },
        { key: "n_outcome_no_shared_fragments", label: "No shared fragments" },
        { key: "n_outcome_annotated", label: "Library-matched" }
    ]

    function tileText(key) {
        if (!stats)
            return "—"
        if ((key === "n_annotated_features" || key === "n_distinct_compounds")
                && !stats.annotation_ran) {
            return "—"
        }
        if (key === "n_precursor_matched_features" && !stats.precursor_ran)
            return "—"
        var v = stats[key]
        return (v === undefined || v === null) ? "—" : String(v)
    }

    Label {
        id: emptyStateLabel
        objectName: "summaryEmptyStateLabel"
        visible: !hasData && !statsLoading
        anchors.centerIn: parent
        text: "No analysis data available."
        color: Theme.mutedTextColor
    }

    component StatTile: Rectangle {
        required property var modelData
        objectName: "statTile_" + modelData.key
        Layout.preferredWidth: 180
        Layout.preferredHeight: 100
        color: Theme.secondaryColor
        border.color: palette.mid
        border.width: 1
        radius: 6

        ColumnLayout {
            anchors.centerIn: parent
            spacing: 4

            Label {
                objectName: "statValue_" + modelData.key
                text: summarySection.tileText(modelData.key)
                font.pixelSize: 22
                font.bold: true
                Layout.alignment: Qt.AlignHCenter
            }
            Label {
                text: modelData.label
                Layout.alignment: Qt.AlignHCenter
            }
        }
    }

    // Scrolls: the tiles and the background list don't fit a small window.
    Flickable {
        objectName: "summaryFlickable"
        anchors.fill: parent
        visible: hasData
        clip: true
        contentWidth: width
        contentHeight: summaryColumn.implicitHeight + 48
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: summaryColumn
            x: 24
            y: 24
            spacing: 16

            GridLayout {
                columns: 3
                columnSpacing: 16
                rowSpacing: 16

                Repeater {
                    model: summarySection.statDefs

                    delegate: StatTile {}
                }
            }

            Label {
                objectName: "outcomeFunnelTitle"
                visible: !!summarySection.stats && summarySection.stats.outcomes_recorded
                text: "Where MS2 annotation lost features"
                font.bold: true
                font.pixelSize: 14
            }
            GridLayout {
                objectName: "outcomeFunnel"
                visible: !!summarySection.stats && summarySection.stats.outcomes_recorded
                columns: 3
                columnSpacing: 16
                rowSpacing: 16

                Repeater {
                    model: summarySection.outcomeDefs
                    delegate: StatTile {}
                }
            }

            // MS2 cleaning (ADR 0064): fragments found in the spectra of many
            // precursors, whether or not background removal was on.
            BackgroundFragmentsList {
                objectName: "summaryBackgroundFragments"
                visible: !!summarySection.stats && (summarySection.stats.ms2_cleaning_ran
                                                     || summarySection.background.length > 0)
                Layout.preferredWidth: 560
                rows: summarySection.background
                ran: !!summarySection.stats && summarySection.stats.ms2_cleaning_ran
            }
        }
    }
}

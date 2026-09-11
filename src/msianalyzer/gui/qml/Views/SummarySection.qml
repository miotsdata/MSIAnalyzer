import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: summarySection
    objectName: "summarySection"

    property var analysis
    property var stats: (analysis && analysis.analysisDbPath)
                         ? AnalysisBridge.getSummary(analysis.analysisDbPath) : null
    // Distinct from "stats is null" (no db path resolved at all) — an
    // existing-but-empty analysis (0 samples, 0 features) also counts as
    // "nothing to show" for the empty-state message.
    readonly property bool hasData: !!stats && (stats.n_samples > 0 || stats.n_features > 0)

    readonly property var statDefs: [
        { key: "n_samples", label: "Samples" },
        { key: "n_features", label: "Features" },
        { key: "n_ms2_associated_features", label: "Features with MS2" },
        { key: "n_annotated_features", label: "Annotated features" },
        { key: "n_distinct_compounds", label: "Distinct compounds" }
    ]

    function tileText(key) {
        if (!stats)
            return "—"
        if ((key === "n_annotated_features" || key === "n_distinct_compounds")
                && !stats.annotation_ran) {
            return "—"
        }
        var v = stats[key]
        return (v === undefined || v === null) ? "—" : String(v)
    }

    Text {
        id: emptyStateLabel
        objectName: "summaryEmptyStateLabel"
        visible: !hasData
        anchors.centerIn: parent
        text: "No analysis data available."
        color: "gray"
    }

    GridLayout {
        anchors.fill: parent
        anchors.margins: 24
        visible: hasData
        columns: 3
        columnSpacing: 16
        rowSpacing: 16

        Repeater {
            model: summarySection.statDefs

            delegate: Rectangle {
                objectName: "statTile_" + modelData.key
                Layout.preferredWidth: 180
                Layout.preferredHeight: 100
                border.color: "#cccccc"
                border.width: 1
                radius: 6

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 4

                    Text {
                        objectName: "statValue_" + modelData.key
                        text: summarySection.tileText(modelData.key)
                        font.pixelSize: 26
                        font.bold: true
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Text {
                        text: modelData.label
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }
    }
}

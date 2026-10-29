import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

// MS2 cleaning's shared fragments (ADR 0064), most shared first: on the
// Summary page and in the Re-annotate dialog's MS2 cleaning tab.
ColumnLayout {
    id: backgroundList
    spacing: 6

    property var rows: []        // load_ms2_background rows
    property bool ran: false     // computed at all (not before ADR 0064)
    property int listHeight: 240

    Label {
        objectName: "backgroundFragmentsTitle"
        text: "MS2 background fragments (in at least 5 % of precursors)"
        font.bold: true
        font.pixelSize: 14
    }
    Label {
        objectName: "backgroundFragmentsEmpty"
        visible: backgroundList.rows.length === 0
        text: backgroundList.ran
              ? "No fragment is in at least 5 % of precursors."
              : "Not computed yet: re-annotate this analysis to list them."
        color: Theme.mutedTextColor
    }
    // Header and rows share colWidths, so the columns line up.
    readonly property var colWidths: [90, 70, 90, 70, 70]
    Row {
        visible: backgroundList.rows.length > 0
        spacing: 12
        Repeater {
            model: ["m/z", "Polarity", "Precursors", "%", "Removed"]
            delegate: Label {
                required property int index
                required property string modelData
                width: backgroundList.colWidths[index]
                horizontalAlignment: index === 1 || index === 4 ? Text.AlignLeft : Text.AlignRight
                text: modelData
                font.bold: true
            }
        }
    }
    ListView {
        id: list
        objectName: "backgroundFragmentsList"
        visible: backgroundList.rows.length > 0
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(contentHeight, backgroundList.listHeight)
        clip: true
        model: backgroundList.rows.length
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        delegate: Row {
            required property int index
            readonly property var row: backgroundList.rows[index]
            spacing: 12
            Repeater {
                model: [Number(row.mz).toFixed(4),
                        row.polarity ? row.polarity.toLowerCase() : "?",
                        String(row.n_precursors),
                        Number(row.percent).toFixed(1),
                        row.removed ? "yes" : ""]
                delegate: Label {
                    required property int index
                    required property string modelData
                    width: backgroundList.colWidths[index]
                    horizontalAlignment: index === 1 || index === 4 ? Text.AlignLeft : Text.AlignRight
                    text: modelData
                    font.bold: row.removed
                    color: row.removed ? Theme.textColor : Theme.mutedTextColor
                }
            }
        }
    }
}

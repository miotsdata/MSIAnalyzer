import QtQuick
import QtQuick.Layouts

// One label/value pair in MirrorPlotDetailWindow's metadata side table.
// A plain reusable type instead of a Repeater over a JS array — this
// codebase's test harness has a confirmed fragility with locating
// Repeater-created items by objectName (see conftest.py's
// `find_visual_child` docstring), and the table's field set is fixed
// (never a variable-length list), so a Repeater buys nothing here.
ColumnLayout {
    id: row
    property alias label: labelText.text
    property alias value: valueText.text
    property alias valueObjectName: valueText.objectName

    Layout.fillWidth: true
    spacing: 0

    Text {
        id: labelText
        color: "gray"
        font.pixelSize: 10
    }
    Text {
        id: valueText
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }
}

import QtQuick
import QtQuick.Controls

// A `CheckBox` whose box is clearly visible in both themes: a white box
// with a dark tick (Theme.checkBoxColor / checkMarkColor). Fusion's own
// indicator uses palette.base, which in dark mode left an unticked box as
// little more than a faint outline. Same pattern as AppMenuItem: a real
// CheckBox, so every property/signal is inherited; only `indicator` changes.
CheckBox {
    id: control

    indicator: Rectangle {
        objectName: "appCheckBoxIndicator"
        implicitWidth: 16
        implicitHeight: 16
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: 2
        color: Theme.checkBoxColor
        border.color: control.visualFocus ? palette.highlight : Theme.checkBoxBorderColor
        border.width: control.visualFocus ? 2 : 1
        opacity: control.enabled ? 1.0 : 0.4

        Text {
            anchors.centerIn: parent
            visible: control.checkState === Qt.Checked
            text: "✓"
            font.pixelSize: 13
            font.bold: true
            color: Theme.checkMarkColor
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

// The "Get statistics" Reorder dialog (ADR 68): drag a name up or down
// to change where it is drawn. One list (the violins/bars), or two when
// grouped by ROI (the ROI panels, and the samples along each panel's x).
// Backed by ListModel.move(): a Repeater over a JS array of objects is
// unreliable in this codebase's test harness.
Dialog {
    id: reorderDialog
    objectName: "statsReorderDialog"
    title: "Reorder"
    modal: true
    standardButtons: Dialog.Ok | Dialog.Cancel
    width: twoLists ? 520 : 300
    height: 460

    property bool twoLists: false

    function fill(model, names) {
        model.clear()
        names.forEach(function (n) { model.append({"name": n}) })
    }
    function names(model) {
        var out = []
        for (var i = 0; i < model.count; i++) out.push(model.get(i).name)
        return out
    }
    function openFor(result) {
        var rois = result.rois || []
        reorderDialog.twoLists = rois.length > 0
        reorderDialog.fill(firstModel, reorderDialog.twoLists ? rois : (result.labels || []))
        reorderDialog.fill(secondModel, reorderDialog.twoLists ? (result.samples || []) : [])
        reorderDialog.open()
    }
    function firstOrder() { return reorderDialog.names(firstModel) }
    function secondOrder() { return reorderDialog.names(secondModel) }

    ListModel { id: firstModel; objectName: "statsReorderFirstModel" }
    ListModel { id: secondModel; objectName: "statsReorderSecondModel" }

    RowLayout {
        anchors.fill: parent
        spacing: 12
        ReorderList {
            title: reorderDialog.twoLists ? "Panels (ROIs)" : "Order"
            model: firstModel
        }
        ReorderList {
            visible: reorderDialog.twoLists
            title: "Samples (x axis)"
            model: secondModel
        }
    }

    component ReorderList: ColumnLayout {
        id: reorderList
        property string title
        property alias model: listView.model
        Layout.fillWidth: true
        Layout.fillHeight: true

        Label { text: reorderList.title; font.bold: true }
        Label {
            text: "Drag to reorder"
            color: Theme.mutedTextColor
            font.pointSize: 8
        }
        ListView {
            id: listView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 2
            ScrollBar.vertical: ScrollBar {}

            delegate: Rectangle {
                id: row
                required property int index
                required property string name
                width: ListView.view.width - 12
                height: 28
                radius: 3
                color: dragArea.pressed ? palette.highlight : palette.button
                border.color: palette.mid

                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    text: "≡  " + row.name
                    color: dragArea.pressed ? palette.highlightedText : palette.buttonText
                }
                // Moves the row live while dragging: whichever row is
                // under the pointer swaps places with this one.
                MouseArea {
                    id: dragArea
                    anchors.fill: parent
                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                    onPositionChanged: (mouse) => {
                        var p = mapToItem(listView.contentItem, mouse.x, mouse.y)
                        var to = listView.indexAt(10, p.y)
                        if (to >= 0 && to !== row.index) listView.model.move(row.index, to, 1)
                    }
                }
            }
        }
    }
}

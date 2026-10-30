import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

// One of Feature Correlation's two tables (ADR 66): a title, a header
// row, then one row per feature — name, m/z, mean r over the samples,
// and r in each sample. Right-click a row for "Show spatial
// distribution". A ListView: only the visible rows get delegates.
ColumnLayout {
    id: correlationTable
    spacing: 4

    property string title: ""
    property var rows: []
    property var samples: []
    // Prefix for child objectNames, so the two tables can be told apart.
    property string tableName: "correlationTable"

    signal showSpatialDistributionRequested(real mz)

    readonly property int nameWidth: 240
    readonly property int mzWidth: 90
    readonly property int valueWidth: 80
    readonly property int rowHeight: 28

    function formatR(value) {
        return (value === null || value === undefined) ? "—" : Number(value).toFixed(3)
    }

    Label {
        objectName: correlationTable.tableName + "Title"
        text: correlationTable.title
        font.bold: true
    }

    // Header row, scrolled together with the body horizontally.
    Flickable {
        id: headerFlick
        Layout.fillWidth: true
        Layout.preferredHeight: correlationTable.rowHeight
        contentWidth: headerRow.implicitWidth
        contentX: body.contentX
        interactive: false
        clip: true

        Row {
            id: headerRow
            height: parent.height
            Label {
                width: correlationTable.nameWidth
                height: parent.height
                text: "Feature"
                font.bold: true
                verticalAlignment: Text.AlignVCenter
                leftPadding: 6
            }
            Label {
                width: correlationTable.mzWidth
                height: parent.height
                text: "m/z"
                font.bold: true
                verticalAlignment: Text.AlignVCenter
            }
            Label {
                width: correlationTable.valueWidth
                height: parent.height
                text: "Mean r"
                font.bold: true
                verticalAlignment: Text.AlignVCenter
            }
            Repeater {
                model: correlationTable.samples
                delegate: Label {
                    required property var modelData
                    width: correlationTable.valueWidth
                    height: headerRow.height
                    text: modelData
                    font.bold: true
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        height: 1
        color: palette.mid
    }

    ListView {
        id: body
        objectName: correlationTable.tableName + "List"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: correlationTable.rows
        contentWidth: headerRow.implicitWidth
        flickableDirection: Flickable.AutoFlickIfNeeded
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        ScrollBar.horizontal: ScrollBar {}

        delegate: Rectangle {
            id: rowItem
            required property var modelData
            required property int index
            objectName: correlationTable.tableName + "Row_" + index
            width: Math.max(body.width, headerRow.implicitWidth)
            height: correlationTable.rowHeight
            color: rowMouse.containsMouse ? palette.alternateBase : "transparent"

            Row {
                height: parent.height
                Label {
                    width: correlationTable.nameWidth
                    height: parent.height
                    leftPadding: 6
                    text: rowItem.modelData.compound_name || ("m/z " + Number(rowItem.modelData.mz).toFixed(4))
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }
                Label {
                    width: correlationTable.mzWidth
                    height: parent.height
                    text: Number(rowItem.modelData.mz).toFixed(4)
                    verticalAlignment: Text.AlignVCenter
                }
                Label {
                    objectName: correlationTable.tableName + "MeanR_" + rowItem.index
                    width: correlationTable.valueWidth
                    height: parent.height
                    text: correlationTable.formatR(rowItem.modelData.mean_r)
                    font.bold: true
                    verticalAlignment: Text.AlignVCenter
                }
                Repeater {
                    model: correlationTable.samples
                    delegate: Label {
                        required property var modelData
                        width: correlationTable.valueWidth
                        height: correlationTable.rowHeight
                        text: correlationTable.formatR(rowItem.modelData[modelData])
                        color: Theme.mutedTextColor
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }

            MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.RightButton
                onClicked: rowMenu.popup()
            }

            Menu {
                id: rowMenu
                objectName: correlationTable.tableName + "RowMenu_" + rowItem.index
                AppMenuItem {
                    objectName: correlationTable.tableName + "ShowDistribution_" + rowItem.index
                    text: "Show spatial distribution"
                    onTriggered: correlationTable.showSpatialDistributionRequested(rowItem.modelData.mz)
                }
            }
        }
    }
}

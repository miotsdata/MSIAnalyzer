import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

// One of Feature Correlation's two tables (ADR 66): a title, a header
// row, then one row per feature — name, m/z, mean r over the samples,
// and r in each sample. Right-click a row for "Show spatial
// distribution" or "Add to overlay" (ADR 67). A ListView: only the
// visible rows get delegates.
ColumnLayout {
    id: correlationTable
    spacing: 4

    property string title: ""
    property var rows: []
    property var samples: []
    // Prefix for child objectNames, so the two tables can be told apart.
    property string tableName: "correlationTable"

    signal showSpatialDistributionRequested(real mz)
    signal addToOverlayRequested(real mz)

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

    // Header and rows scroll sideways together inside one horizontal
    // Flickable; the rows also scroll vertically inside it (the ListView).
    // A vertical ListView ignores a horizontal contentWidth, which is why
    // the sideways scroll lives on this outer Flickable.
    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: 0

        Flickable {
            id: hFlick
            objectName: correlationTable.tableName + "HScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: Math.max(width, headerRow.implicitWidth)
            contentHeight: height
            flickableDirection: Flickable.HorizontalFlick
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.horizontal: ScrollBar {
                id: hBar
                policy: hFlick.contentWidth > hFlick.width ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded
            }

            Row {
                id: headerRow
                height: correlationTable.rowHeight
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
                        required property int index
                        objectName: correlationTable.tableName + "SampleHeader_" + index
                        width: correlationTable.valueWidth
                        height: headerRow.height
                        rightPadding: 4
                        text: modelData
                        font.bold: true
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                        // Sample names rarely fit the column: the full name on hover.
                        ToolTip.visible: headerHover.hovered
                        ToolTip.delay: 300
                        ToolTip.text: modelData
                        HoverHandler { id: headerHover }
                    }
                }
            }

            Rectangle {
                y: headerRow.height
                width: hFlick.contentWidth
                height: 1
                color: palette.mid
            }

            ListView {
                id: body
                objectName: correlationTable.tableName + "List"
                y: headerRow.height + 1
                width: hFlick.contentWidth
                height: hFlick.height - y - (hBar.visible ? hBar.height : 0)
                clip: true
                model: correlationTable.rows
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: vBar

                delegate: Rectangle {
                    id: rowItem
                    required property var modelData
                    required property int index
                    objectName: correlationTable.tableName + "Row_" + index
                    width: body.width
                    height: correlationTable.rowHeight
                    color: rowMouse.containsMouse ? palette.alternateBase : "transparent"

                    Row {
                        height: parent.height
                        Label {
                            id: nameLabel
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

                    // A cut-off feature name in full, on hover over it.
                    ToolTip.visible: rowMouse.containsMouse && nameLabel.truncated
                                     && rowMouse.mouseX < correlationTable.nameWidth
                    ToolTip.delay: 300
                    ToolTip.text: nameLabel.text

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
                        AppMenuItem {
                            objectName: correlationTable.tableName + "AddToOverlay_" + rowItem.index
                            text: "Add to overlay"
                            onTriggered: correlationTable.addToOverlayRequested(rowItem.modelData.mz)
                        }
                    }
                }
            }
        }

        // Outside the horizontal Flickable, so it stays in view however
        // far the table is scrolled sideways.
        ScrollBar {
            id: vBar
            Layout.fillHeight: true
            orientation: Qt.Vertical
        }
    }
}

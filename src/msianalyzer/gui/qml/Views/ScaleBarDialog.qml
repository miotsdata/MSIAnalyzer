import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "qrc:/Style"

// Visual Inspection's "Scale bar…" settings (ADR 53): on/off, one bar
// length and color for every sample, and one pixel size per sample (with
// a "Set to all" shortcut). Pixels are assumed square. Edits are a draft
// until OK, which saves them to the analysis DB and emits `saved`.
//
// Pixel-size rows are a Repeater over a plain integer count indexing into
// `sampleNames`/`pixelUmTexts`, not over an array of objects (see
// ImportH5adDialog.qml's header for the test-harness fragility).
Dialog {
    id: scaleBarDialog
    objectName: "scaleBarDialog"
    title: "Scale bar"
    modal: true
    width: Math.min(460, (parent ? parent.width : 600) - 40)
    height: Math.min(560, (parent ? parent.height : 600) - 40)
    anchors.centerIn: parent

    property var analysis: null
    property var sampleNames: []
    property bool barEnabled: false
    property string barUmText: "50"
    property color barColor: "white"
    property var pixelUmTexts: []  // one per sampleNames entry, "" = not set

    readonly property bool barUmValid: Number(barUmText) > 0

    signal saved(var settings)

    readonly property bool useNativeDialogs: Qt.platform.pluginName !== "offscreen"

    function openFor(analysisModel, names, settings) {
        analysis = analysisModel
        sampleNames = names
        barEnabled = !!settings.enabled
        barUmText = String(settings.bar_um || 50)
        barColor = settings.color || "white"
        var px = settings.pixel_um || {}
        pixelUmTexts = names.map(function (n) { return px[n] > 0 ? String(px[n]) : "" })
        setAllField.text = ""
        open()
    }

    function setPixelUmText(index, text) {
        var copy = pixelUmTexts.slice()
        copy[index] = text
        pixelUmTexts = copy
    }

    function setAll(text) {
        pixelUmTexts = sampleNames.map(function () { return text })
    }

    // Invalid / empty pixel sizes are left out: that sample gets no bar.
    function settingsObject() {
        var px = {}
        sampleNames.forEach(function (n, i) {
            var v = Number(pixelUmTexts[i])
            if (pixelUmTexts[i] !== "" && v > 0) px[n] = v
        })
        return {
            "enabled": barEnabled,
            "bar_um": Number(barUmText),
            "color": String(barColor),
            "pixel_um": px,
        }
    }

    onAccepted: {
        var settings = settingsObject()
        AnalysisBridge.setScaleBar(analysis.analysisDbPath, JSON.stringify(settings))
        saved(settings)
    }

    function openHelp(title, body) {
        scaleBarHelpDialog.helpTitle = title
        scaleBarHelpDialog.helpBody = body
        scaleBarHelpDialog.open()
    }

    footer: DialogButtonBox {
        Button {
            objectName: "scaleBarOkButton"
            text: "OK"
            enabled: scaleBarDialog.barUmValid
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
        }
        Button {
            objectName: "scaleBarCancelButton"
            text: "Cancel"
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
    }

    ColorDialog {
        id: barColorDialog
        objectName: "scaleBarColorDialog"
        options: scaleBarDialog.useNativeDialogs ? 0 : ColorDialog.DontUseNativeDialog
        selectedColor: scaleBarDialog.barColor
        onAccepted: scaleBarDialog.barColor = selectedColor
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            AppCheckBox {
                objectName: "scaleBarEnabledCheckBox"
                text: "Show scale bar"
                checked: scaleBarDialog.barEnabled
                onToggled: scaleBarDialog.barEnabled = checked
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            HelpButton {
                onClicked: scaleBarDialog.openHelp("Show scale bar",
                    "Draws a bar of a known physical length in the bottom-right "
                    + "corner of every heatmap, in Visual Inspection, ROI Design and "
                    + "Coregistration. Its on-screen length follows the zoom.\n\n"
                    + "A sample with no pixel size set below shows no bar.\n\n"
                    + "**Interaction:** Export > Image… has its own \"Include scale "
                    + "bar\" option, independent of this switch.")
            }
        }

        RowLayout {
            Label { text: "Bar length (µm):" }
            TextField {
                objectName: "scaleBarLengthField"
                Layout.preferredWidth: 90
                text: scaleBarDialog.barUmText
                validator: DoubleValidator { bottom: 0 }
                onTextEdited: scaleBarDialog.barUmText = text
            }
            HelpButton {
                onClicked: scaleBarDialog.openHelp("Bar length",
                    "The physical length the bar represents, the same for every "
                    + "sample so they stay comparable.\n\n"
                    + "**Formula:** on-screen length = bar length / pixel size × "
                    + "screen pixels per image pixel.\n\n"
                    + "**Range:** > 0 µm.")
            }
            Item { Layout.fillWidth: true }
            Label { text: "Color:" }
            Button {
                objectName: "scaleBarColorButton"
                implicitWidth: 40
                onClicked: barColorDialog.open()
                contentItem: Rectangle {
                    color: scaleBarDialog.barColor
                    border.color: Theme.borderColor
                    implicitHeight: 16
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }

        Label {
            visible: !scaleBarDialog.barUmValid
            text: "Bar length must be greater than 0."
            color: Theme.errorColor
        }

        RowLayout {
            Label { text: "Pixel size (µm)"; font.bold: true }
            HelpButton {
                onClicked: scaleBarDialog.openHelp("Pixel size",
                    "The edge length of one (square) MSI pixel of each sample, i.e. "
                    + "the raster step of the acquisition. mzML files don't record "
                    + "it, so it has to be entered here.\n\n"
                    + "Leave a sample empty to show no bar on it.\n\n"
                    + "**Range:** > 0 µm.\n\n"
                    + "**Interaction:** \"Set to all\" fills every sample with one value.")
            }
        }

        RowLayout {
            Label { text: "Set to all:" }
            TextField {
                id: setAllField
                objectName: "scaleBarSetAllField"
                Layout.preferredWidth: 90
                validator: DoubleValidator { bottom: 0 }
            }
            Button {
                objectName: "scaleBarSetAllButton"
                text: "Apply"
                enabled: Number(setAllField.text) > 0
                onClicked: scaleBarDialog.setAll(setAllField.text)
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: parent.width
                Repeater {
                    model: scaleBarDialog.sampleNames.length
                    delegate: RowLayout {
                        Label {
                            text: scaleBarDialog.sampleNames[index]
                            elide: Text.ElideRight
                            Layout.preferredWidth: 220
                        }
                        TextField {
                            objectName: "scaleBarPixelField_" + scaleBarDialog.sampleNames[index]
                            Layout.preferredWidth: 90
                            placeholderText: "not set"
                            text: scaleBarDialog.pixelUmTexts[index] || ""
                            validator: DoubleValidator { bottom: 0 }
                            onTextEdited: scaleBarDialog.setPixelUmText(index, text)
                        }
                    }
                }
            }
        }
    }

    // Same shared, read-only Markdown help window as NewAnalysisPage.qml's
    // fieldHelpDialog (ADR 44).
    Dialog {
        id: scaleBarHelpDialog
        objectName: "scaleBarHelpDialog"
        modal: true
        standardButtons: Dialog.Close
        anchors.centerIn: parent
        width: Math.min(420, scaleBarDialog.width - 20)
        height: Math.min(340, scaleBarDialog.height - 20)
        property string helpTitle: ""
        property string helpBody: ""
        title: helpTitle

        ScrollView {
            anchors.fill: parent
            clip: true
            Text {
                width: scaleBarHelpDialog.availableWidth
                textFormat: Text.MarkdownText
                wrapMode: Text.Wrap
                text: scaleBarHelpDialog.helpBody
                color: Theme.textColor
            }
        }
    }

    component HelpButton: ToolButton {
        text: "?"
        font.bold: true
        implicitWidth: 24
        implicitHeight: 24
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }
}

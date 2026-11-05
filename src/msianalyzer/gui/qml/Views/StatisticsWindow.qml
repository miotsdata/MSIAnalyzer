import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Dialogs
import "qrc:/Style"

// "Get statistics" (ADR 68): violins of what Visual Inspection shows (a
// feature on its layer, or a numeric obs column), or stacked bars of a
// categorical obs column, per sample or per sample × ROI. Follows
// Visual Inspection's selection live through `controls` (the same
// HeatmapControlsPanel instance, like RoiDesignWindow). The figure is a
// matplotlib PNG rendered off the GUI thread (AnalysisBridge.requestStatsPlot);
// Width/Height are the figure's size in inches, like Jupyter's figsize, and
// the figure is always fitted into the panel, as Jupyter shrinks a wide
// figure to the cell: a bigger figure means more room and smaller text, never
// scrolling. Only Zoom (also Ctrl+wheel) makes it bigger than the panel, and
// then it scrolls. Both are done by choosing the render dpi, so text stays sharp.
Window {
    id: statsWindow
    objectName: "statisticsWindow"
    title: "Statistics — " + statsWindow.targetLabel
    visible: false  // see RoiDesignWindow.qml: the Loader must not pop it up
    width: 1100
    height: 750
    minimumWidth: 700
    minimumHeight: 450

    // Same palette block as RoiDesignWindow.qml: a top-level Window doesn't
    // inherit Main.qml's.
    color: palette.window
    palette {
        window: Theme.backgroundColor
        windowText: Theme.textColor
        base: Theme.fieldColor
        alternateBase: Theme.secondaryColor
        text: Theme.textColor
        button: Theme.controlColor
        buttonText: Theme.textColor
        toolTipBase: Theme.controlColor
        toolTipText: Theme.textColor
        placeholderText: Theme.placeholderTextColor
        highlight: Theme.primaryColor
        highlightedText: Theme.highlightedTextColor
        light: Theme.lightColor
        midlight: Theme.midlightColor
        mid: Theme.borderColor
        dark: Theme.darkColor
        shadow: Theme.shadowColor
        disabled.windowText: Theme.disabledTextColor
        disabled.buttonText: Theme.disabledTextColor
        disabled.text: Theme.disabledTextColor
    }

    property var analysis: null
    property var samples: []
    property var controls: null

    function openFor(analysis, samples, controls) {
        statsWindow.analysis = analysis
        statsWindow.samples = samples
        statsWindow.controls = controls
        statsWindow.show()
        statsWindow.raise()
        statsWindow.requestActivate()
        statsWindow.scheduleRefresh()
    }

    // What Visual Inspection shows right now, or null (nothing selected).
    readonly property var spec: {
        var c = statsWindow.controls
        if (!c) return null
        if (c.inspectionMode === "feature") {
            return c.selectedFeature
                ? {"kind": "feature", "mz": c.selectedFeature.mz, "layer": c.dataLayer} : null
        }
        return c.selectedObsColumn
            ? {"kind": "obs", "column": c.selectedObsColumn, "numeric": c.isSelectedObsNumeric} : null
    }
    readonly property bool categorical: !!statsWindow.spec && statsWindow.spec.kind === "obs"
                                        && !statsWindow.spec.numeric
    readonly property string targetLabel: !statsWindow.spec ? ""
        : statsWindow.spec.kind === "feature"
            ? "m/z " + Number(statsWindow.spec.mz).toFixed(4) + " (" + statsWindow.spec.layer + ")"
            : statsWindow.spec.column
    readonly property var visibleSampleNames: statsWindow.samples
        .filter(function (s) { return !statsWindow.controls || !statsWindow.controls.isHidden(s.name) })
        .map(function (s) { return s.name })

    // Options. "Detected pixels only" starts on for a feature (most pixels
    // are zero there) and off for an obs column.
    property bool useRoi: false
    property bool groupByRoi: false
    property bool outsideRoi: false
    property bool detectedOnly: !!statsWindow.spec && statsWindow.spec.kind === "feature"
    property bool proportion: false
    property bool sharedY: true
    property int rows: 1
    property int cols: 4
    // Figure size in inches (figsize) and zoom in % (100 % = 100 dpi).
    property int widthIn: 10
    property int heightIn: 6
    property int zoom: 100
    // The dpi that fits widthIn x heightIn into the panel, times the zoom.
    function renderDpi() {
        var fit = Math.min((plotFlickable.width - 12) / statsWindow.widthIn,
                           (plotFlickable.height - 12) / statsWindow.heightIn)
        return Math.max(10, fit) * statsWindow.zoom / 100
    }
    // Reorder dialog results: group labels (one panel each), or the ROI
    // panels and the samples along x when grouped by ROI.
    property var order: []
    property var roiOrder: []
    property var sampleOrder: []
    // Grouped panels stack one below the other by default.
    onGroupByRoiChanged: {
        statsWindow.rows = 1
        statsWindow.cols = statsWindow.groupByRoi ? 1 : 4
        statsWindow.scheduleRefresh()
    }
    onUseRoiChanged: {
        statsWindow.order = []  // the labels change
        statsWindow.scheduleRefresh()
    }

    // Last result from AnalysisBridge.statsPlotReady.
    property var result: ({})
    property bool loading: false

    function plotParams() {
        return {
            "sampleNames": statsWindow.visibleSampleNames,
            "spec": statsWindow.spec,
            "useRoi": statsWindow.useRoi,
            "groupByRoi": statsWindow.groupByRoi,
            "outsideRoi": statsWindow.outsideRoi,
            "detectedOnly": statsWindow.detectedOnly && !statsWindow.categorical,
            "proportion": statsWindow.proportion,
            "sharedY": statsWindow.sharedY,
            "rows": statsWindow.rows,
            "cols": statsWindow.cols,
            "widthIn": statsWindow.widthIn,
            "heightIn": statsWindow.heightIn,
            "dpi": statsWindow.renderDpi(),
            "order": statsWindow.order,
            "roiOrder": statsWindow.roiOrder,
            "sampleOrder": statsWindow.sampleOrder,
        }
    }

    function scheduleRefresh() {
        if (statsWindow.visible) refreshTimer.restart()
    }
    // Debounced: spin boxes and window resizes fire many changes.
    Timer {
        id: refreshTimer
        interval: 250
        onTriggered: {
            if (!statsWindow.spec || statsWindow.visibleSampleNames.length === 0) {
                statsWindow.result = {}
                return
            }
            statsWindow.loading = true
            AnalysisBridge.requestStatsPlot(statsWindow.plotParams())
        }
    }
    onSpecChanged: statsWindow.scheduleRefresh()
    onVisibleSampleNamesChanged: statsWindow.scheduleRefresh()
    onOutsideRoiChanged: statsWindow.scheduleRefresh()
    onDetectedOnlyChanged: statsWindow.scheduleRefresh()
    onProportionChanged: statsWindow.scheduleRefresh()
    onSharedYChanged: statsWindow.scheduleRefresh()
    onRowsChanged: statsWindow.scheduleRefresh()
    onColsChanged: statsWindow.scheduleRefresh()
    onWidthInChanged: statsWindow.scheduleRefresh()
    onHeightInChanged: statsWindow.scheduleRefresh()
    onZoomChanged: statsWindow.scheduleRefresh()
    onOrderChanged: statsWindow.scheduleRefresh()
    onRoiOrderChanged: statsWindow.scheduleRefresh()
    onSampleOrderChanged: statsWindow.scheduleRefresh()

    Connections {
        target: AnalysisBridge
        function onStatsPlotReady(received) {
            statsWindow.result = received
            statsWindow.loading = false
        }
    }

    function openHelp(title, body) {
        statsHelpDialog.helpTitle = title
        statsHelpDialog.helpBody = body
        statsHelpDialog.open()
    }

    SplitView {
        anchors.fill: parent
        anchors.margins: 12
        orientation: Qt.Horizontal

        // Same padded handle as ProjectHomePage.qml.
        handle: Rectangle {
            implicitWidth: 12
            color: "transparent"
            Rectangle {
                anchors.centerIn: parent
                width: 1
                height: parent.height
                color: SplitHandle.hovered ? palette.dark : palette.mid
            }
        }

        ScrollView {
            SplitView.preferredWidth: 260
            SplitView.minimumWidth: 220
            height: parent.height
            clip: true

            ColumnLayout {
                width: 240
                spacing: 8

                Label { text: statsWindow.categorical ? "Bar plot" : "Violin plot"; font.bold: true }
                Label {
                    text: statsWindow.targetLabel || "Nothing selected in Visual Inspection."
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    color: Theme.mutedTextColor
                }

                RowLayout {
                    AppCheckBox {
                        objectName: "statsUseRoiCheckBox"
                        text: "Use ROI"
                        checked: statsWindow.useRoi
                        onToggled: statsWindow.useRoi = checked
                    }
                    HelpButton {
                        onClicked: statsWindow.openHelp("Use ROI",
                            "**General:** one violin (or bar) per ROI in each sample instead of "
                            + "one per sample. ROIs are the ones drawn in ROI Design; each keeps its "
                            + "colour.\n\n"
                            + "**Interaction:** a pixel inside two nested ROIs counts in both. Pixels "
                            + "outside every ROI are left out unless \"Pixels outside ROI\" is on.")
                    }
                }
                RowLayout {
                    enabled: statsWindow.useRoi
                    AppCheckBox {
                        objectName: "statsGroupByRoiCheckBox"
                        text: "Grouped by ROI"
                        checked: statsWindow.groupByRoi
                        onToggled: statsWindow.groupByRoi = checked
                    }
                    HelpButton {
                        onClicked: statsWindow.openHelp("Grouped by ROI",
                            "**General:** one panel per ROI, with the samples along the x axis, "
                            + "so the same ROI is compared across samples on one axis.\n\n"
                            + "**Interaction:** rows and columns arrange the ROI panels (one column, "
                            + "one panel below the other, by default). When off, every violin gets "
                            + "its own panel. All panels share the y axis.")
                    }
                }
                RowLayout {
                    enabled: statsWindow.useRoi
                    AppCheckBox {
                        objectName: "statsOutsideRoiCheckBox"
                        text: "Pixels outside ROI"
                        checked: statsWindow.outsideRoi
                        onToggled: statsWindow.outsideRoi = checked
                    }
                    HelpButton {
                        onClicked: statsWindow.openHelp("Pixels outside ROI",
                            "**General:** adds one more group per sample, \"(no ROI)\", with every "
                            + "pixel that is inside none of its ROIs (drawn in grey).\n\n"
                            + "**Interaction:** only used with \"Use ROI\".")
                    }
                }
                RowLayout {
                    visible: !statsWindow.categorical
                    AppCheckBox {
                        objectName: "statsDetectedOnlyCheckBox"
                        text: "Detected pixels only"
                        checked: statsWindow.detectedOnly
                        onToggled: statsWindow.detectedOnly = checked
                    }
                    HelpButton {
                        onClicked: statsWindow.openHelp("Detected pixels only",
                            "**General:** leaves out pixels whose value is 0. For a feature, 0 means "
                            + "\"not detected\": most pixels are 0 for most features, and they would "
                            + "squash the violin into a flat shape at 0.\n\n"
                            + "**Range:** on by default for a feature, off for an obs column.\n\n"
                            + "**Interaction:** pixels with no value at all (NaN) are always left out.")
                    }
                }
                AppCheckBox {
                    objectName: "statsProportionCheckBox"
                    visible: statsWindow.categorical
                    text: "Proportion"
                    checked: statsWindow.proportion
                    onToggled: statsWindow.proportion = checked
                }

                RowLayout {
                    AppCheckBox {
                        objectName: "statsSharedYCheckBox"
                        text: "Shared y axis"
                        checked: statsWindow.sharedY
                        onToggled: statsWindow.sharedY = checked
                    }
                    HelpButton {
                        onClicked: statsWindow.openHelp("Shared y axis",
                            "**General:** every panel uses the same y range, so heights compare "
                            + "directly between panels. Turn it off to give each panel its own "
                            + "range, when one group's values would flatten all the others.\n\n"
                            + "**Interaction:** only matters with more than one panel. For bars "
                            + "with Proportion on, every panel already goes from 0 to 1.")
                    }
                }

                GridLayout {
                    columns: 2
                    Label { text: "Rows" }
                    SpinBox {
                        objectName: "statsRowsSpinBox"
                        from: 1; to: 50
                        value: statsWindow.rows
                        onValueModified: statsWindow.rows = value
                    }
                    Label { text: "Columns" }
                    SpinBox {
                        objectName: "statsColsSpinBox"
                        from: 1; to: 50
                        value: statsWindow.cols
                        onValueModified: statsWindow.cols = value
                    }
                    Label { text: "Width (in)" }
                    SpinBox {
                        objectName: "statsWidthSpinBox"
                        from: 2; to: 100
                        editable: true
                        value: statsWindow.widthIn
                        onValueModified: statsWindow.widthIn = value
                    }
                    Label { text: "Height (in)" }
                    SpinBox {
                        objectName: "statsHeightSpinBox"
                        from: 2; to: 100
                        editable: true
                        value: statsWindow.heightIn
                        onValueModified: statsWindow.heightIn = value
                    }
                    Label { text: "Zoom" }
                    SpinBox {
                        objectName: "statsZoomSpinBox"
                        from: 25; to: 400; stepSize: 25
                        value: statsWindow.zoom
                        textFromValue: function (v) { return v + " %" }
                        valueFromText: function (t) { return parseInt(t) }
                        onValueModified: statsWindow.zoom = value
                    }
                }
                RowLayout {
                    HelpButton {
                        onClicked: statsWindow.openHelp("Figure size and zoom",
                            "**General:** Width and Height are the figure's size in inches, like "
                            + "`figsize=(width, height)` in a notebook. The figure is always shrunk "
                            + "or grown to fit the panel, as Jupyter does with a wide figure: a "
                            + "bigger size gives the violins more room and makes the text look "
                            + "smaller; changing only one of them changes the proportions.\n\n"
                            + "**Zoom** magnifies the fitted figure, text included (Ctrl+mouse wheel "
                            + "over the plot does the same); above 100 % the plot scrolls.\n\n"
                            + "**Interaction:** export uses Width and Height, not the zoom.")
                    }
                }
                Label {
                    text: "Rows grow by themselves when there are more panels than cells."
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    color: Theme.mutedTextColor
                    font.pointSize: 8
                }

                Button {
                    objectName: "statsReorderButton"
                    text: "Reorder…"
                    enabled: (statsWindow.result.labels || []).length > 1
                    onClicked: reorderDialog.openFor(statsWindow.result)
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
            }
        }

        Item {
            SplitView.fillWidth: true
            height: parent.height

            Flickable {
                id: plotFlickable
                objectName: "statsPlotFlickable"
                anchors.fill: parent
                clip: true
                contentWidth: plotImage.width
                contentHeight: plotImage.height
                boundsBehavior: Flickable.StopAtBounds
                // The fit depends on the panel size.
                onWidthChanged: statsWindow.scheduleRefresh()
                onHeightChanged: statsWindow.scheduleRefresh()
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }

                // The figure's own background is white, like the notebook.
                Image {
                    id: plotImage
                    objectName: "statsPlotImage"
                    source: statsWindow.result.image || ""
                    cache: false

                    TapHandler {
                        acceptedButtons: Qt.RightButton
                        onTapped: plotMenu.popup()
                    }
                    WheelHandler {
                        acceptedModifiers: Qt.ControlModifier
                        onWheel: (event) => {
                            var step = event.angleDelta.y > 0 ? 25 : -25
                            statsWindow.zoom = Math.max(25, Math.min(400, statsWindow.zoom + step))
                        }
                    }
                }
            }

            Label {
                anchors.centerIn: parent
                visible: !statsWindow.loading && !statsWindow.result.image
                text: statsWindow.result.error ? "Could not draw the plot: " + statsWindow.result.error
                    : !statsWindow.spec ? "Select a feature or obs column in Visual Inspection."
                    : "No samples shown."
                color: Theme.mutedTextColor
                wrapMode: Text.Wrap
                width: Math.min(implicitWidth, parent.width - 24)
            }
            BusyIndicator {
                anchors.centerIn: parent
                running: statsWindow.loading
                visible: running
            }
        }
    }

    Menu {
        id: plotMenu
        objectName: "statsPlotMenu"
        AppMenuItem {
            objectName: "statsExportMenuItem"
            text: "Export image…"
            onTriggered: exportDialog.open()
        }
    }

    // The format follows the extension (AnalysisBridge.exportStatsPlot).
    FileDialog {
        id: exportDialog
        objectName: "statsExportDialog"
        fileMode: FileDialog.SaveFile
        nameFilters: ["PNG image (*.png)", "SVG vector image (*.svg)", "PDF document (*.pdf)"]
        options: Qt.platform.pluginName === "offscreen" ? FileDialog.DontUseNativeDialog : 0
        onAccepted: {
            var path = Router.toLocalPath(selectedFile)
            if (!/\.(png|svg|pdf)$/i.test(path))
                path += [".png", ".svg", ".pdf"][Math.max(0, selectedNameFilter.index)]
            AnalysisBridge.exportStatsPlot(statsWindow.plotParams(), path)
        }
    }

    StatsReorderDialog {
        id: reorderDialog
        anchors.centerIn: parent
        onAccepted: {
            if (statsWindow.result.rois && statsWindow.result.rois.length > 0) {
                statsWindow.roiOrder = reorderDialog.firstOrder()
                statsWindow.sampleOrder = reorderDialog.secondOrder()
            } else {
                statsWindow.order = reorderDialog.firstOrder()
            }
        }
    }

    // Same shared read-only Markdown help window as ScaleBarDialog.qml (ADR 44).
    Dialog {
        id: statsHelpDialog
        objectName: "statsHelpDialog"
        modal: true
        standardButtons: Dialog.Close
        anchors.centerIn: parent
        width: Math.min(420, statsWindow.width - 20)
        height: Math.min(340, statsWindow.height - 20)
        property string helpTitle: ""
        property string helpBody: ""
        title: helpTitle

        ScrollView {
            anchors.fill: parent
            clip: true
            Text {
                width: statsHelpDialog.availableWidth
                textFormat: Text.MarkdownText
                wrapMode: Text.Wrap
                text: statsHelpDialog.helpBody
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

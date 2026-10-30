import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "qrc:/Style"
import "qrc:/Utils/SearchQuery.js" as SearchQuery
import "qrc:/Utils/ScaleBar.js" as ScaleBar

// Overlay (ADR 67): several features in one image per sample, one color
// each, like a multi-channel immunofluorescence composite. Each channel
// has its own contrast (min, max, gamma); "Additive" sums the channels so
// overlaps mix (red + green = yellow), "Max" keeps each pixel's brightest
// channel. Rendered by the heatmap image provider
// ("<sample>|overlay|<layer>|<mode>|<channels>", core.plotting.feature_overlay).
Item {
    id: overlaySection
    objectName: "overlaySection"

    property var analysis
    readonly property string dbPath: (analysis && analysis.analysisDbPath) ? analysis.analysisDbPath : ""
    property var samples: []

    // Colors for new channels, in order: the classic fluorescence set,
    // magenta/green first (the pair that stays distinct for red-green
    // color blindness).
    readonly property var channelPalette: ["#ff00ff", "#00ff00", "#00ffff", "#ffff00", "#ff0000", "#0080ff"]
    readonly property int maxChannels: 6

    // [{mz, label, color "#rrggbb", vmin "" (auto) or number, vmax, gamma, visible}]
    property var channels: []
    property string mode: "add"
    property string dataLayer: ""

    // --- Feature picker -------------------------------------------------
    property var features: []
    property bool featuresLoading: false
    property string nameQuery: ""
    property string mzQuery: ""
    readonly property var filteredFeatures: overlaySection.features.filter(function (f) {
        return SearchQuery.matchesName(overlaySection.nameQuery, f.compound_name)
            && SearchQuery.matchesMz(overlaySection.mzQuery, f.mz)
    })
    readonly property bool rowsLoading: overlaySection.featuresLoading

    function featureLabel(f) {
        return f.compound_name ? (Number(f.mz).toFixed(4) + ": " + f.compound_name)
                               : ("m/z " + Number(f.mz).toFixed(4))
    }
    function labelForMz(mz) {
        for (var i = 0; i < overlaySection.features.length; i++)
            if (Math.abs(overlaySection.features[i].mz - mz) < 1e-6)
                return overlaySection.featureLabel(overlaySection.features[i])
        return "m/z " + Number(mz).toFixed(4)
    }

    // --- Channels ---------------------------------------------------------
    function hasChannel(mz) {
        return overlaySection.channels.some(function (c) { return Math.abs(c.mz - mz) < 1e-6 })
    }
    function addChannel(mz) {
        if (overlaySection.channels.length >= overlaySection.maxChannels || overlaySection.hasChannel(mz))
            return false
        var used = overlaySection.channels.map(function (c) { return c.color })
        var free = overlaySection.channelPalette.filter(function (c) { return used.indexOf(c) < 0 })
        var color = free.length ? free[0] : overlaySection.channelPalette[0]
        overlaySection.channels = overlaySection.channels.concat([{
            "mz": mz, "label": overlaySection.labelForMz(mz), "color": color,
            "vmin": "", "vmax": "", "gamma": 1.0, "visible": true
        }])
        return true
    }
    function removeChannel(index) {
        var c = overlaySection.channels.slice()
        c.splice(index, 1)
        overlaySection.channels = c
    }
    function updateChannel(index, key, value) {
        var c = overlaySection.channels.slice()
        c[index] = Object.assign({}, c[index])
        c[index][key] = value
        overlaySection.channels = c
    }
    // Both bounds at once: one re-render, not two. "" = automatic.
    function setChannelRange(index, vmin, vmax) {
        var c = overlaySection.channels.slice()
        c[index] = Object.assign({}, c[index], { "vmin": vmin, "vmax": vmax })
        overlaySection.channels = c
    }

    // --- Range sliders -----------------------------------------------------
    // Each channel's slider spans its feature's values in the current
    // layer over every sample: { "<layer>|<mz>": {vmin, vmax} }, fetched in
    // the background (AnalysisBridge.requestOverlayRange).
    property var channelRanges: ({})
    function rangeFor(mz) {
        return overlaySection.channelRanges[overlaySection.dataLayer + "|" + mz] || null
    }
    function requestRanges() {
        if (!overlaySection.dataLayer || overlaySection.samples.length === 0) return
        var names = overlaySection.samples.map(function (s) { return s.name })
        overlaySection.channels.forEach(function (c) {
            if (!overlaySection.rangeFor(c.mz))
                AnalysisBridge.requestOverlayRange(names, c.mz, overlaySection.dataLayer)
        })
    }
    // A slider readout: short, scientific past 10^4 (raw intensities).
    function formatValue(v) {
        return Math.abs(v) >= 1e4 ? Number(v).toExponential(2) : String(Number(Number(v).toPrecision(3)))
    }
    onChannelsChanged: overlaySection.requestRanges()
    onSamplesChanged: {
        overlaySection.requestRanges()
        overlaySection.refreshSampleRois()
    }
    // Bounds picked on another layer mean nothing on this one.
    onDataLayerChanged: {
        overlaySection.channels = overlaySection.channels.map(function (c) {
            return Object.assign({}, c, { "vmin": "", "vmax": "" })
        })
    }
    // The channels part of the tile id: "mz:rrggbb:vmin:vmax:gamma,..."
    // ("auto" for an empty bound; no "#", it would start the URL fragment).
    readonly property string channelsToken: overlaySection.channels
        .filter(function (c) { return c.visible })
        .map(function (c) {
            return c.mz + ":" + c.color.substring(1).toLowerCase()
                   + ":" + (c.vmin === "" ? "auto" : c.vmin)
                   + ":" + (c.vmax === "" ? "auto" : c.vmax)
                   + ":" + c.gamma
        }).join(",")
    function tileSource(sampleName) {
        if (!overlaySection.channelsToken || !overlaySection.dataLayer) return ""
        return "image://heatmap/" + sampleName + "|overlay|" + overlaySection.dataLayer + "|"
               + overlaySection.mode + "|" + overlaySection.channelsToken
               + "|ds" + (AnalysisBridge ? AnalysisBridge.activeDatasetId : -1)
    }

    // A number typed in a channel's Min/Max/Gamma field, or "" (auto).
    function parseBound(text) {
        var t = text.trim()
        if (t === "") return ""
        var v = Number(t)
        return isFinite(v) ? v : null
    }

    // --- Layer, dataset ----------------------------------------------------
    readonly property bool datasetActive: (AnalysisBridge ? AnalysisBridge.activeDatasetId : -1) >= 0
    property var layerOptions: []
    function refreshLayers() {
        if (!overlaySection.dbPath) return
        if (overlaySection.datasetActive) {
            var info = AnalysisBridge.getActiveDatasetInfo()
            overlaySection.layerOptions = ["X"].concat((info.layers || []).filter(function (l) { return l !== "localMoranI" }))
        } else {
            overlaySection.layerOptions = [AnalysisBridge.getNormalizedLayerName(overlaySection.dbPath), "raw"]
        }
        if (overlaySection.layerOptions.indexOf(overlaySection.dataLayer) < 0)
            overlaySection.dataLayer = overlaySection.layerOptions.indexOf("TIC") >= 0 ? "TIC" : overlaySection.layerOptions[0]
    }
    function refreshSamples() {
        overlaySection.samples = overlaySection.dbPath ? AnalysisBridge.getInspectionSamples(overlaySection.dbPath) : []
    }
    function refreshFeatures() {
        if (!overlaySection.dbPath) return
        overlaySection.featuresLoading = true
        AnalysisBridge.requestFeatureList(overlaySection.dbPath)
    }
    property var scaleBar: ({})

    // Saved ROIs, drawn on the tiles while "Show ROIs" is on (same as
    // Visual Inspection): { sampleName: [{"name","color","vertices"}, ...] }.
    property bool showRois: false
    property var sampleRoisCache: ({})
    function refreshSampleRois() {
        var cache = {}
        if (overlaySection.showRois && overlaySection.dbPath)
            overlaySection.samples.forEach(function (s) {
                cache[s.name] = AnalysisBridge.getSampleRois(overlaySection.dbPath, s.name)
            })
        overlaySection.sampleRoisCache = cache
    }
    onShowRoisChanged: overlaySection.refreshSampleRois()

    // What Export > Image… writes from this tab (Main.qml): the visible
    // channels' legend labels, in the tile id's order.
    readonly property var visibleChannelLabels: overlaySection.channels
        .filter(function (c) { return c.visible })
        .map(function (c) { return c.label })

    onDbPathChanged: {
        overlaySection.refreshLayers()
        overlaySection.refreshSamples()
        overlaySection.refreshFeatures()
        overlaySection.scaleBar = overlaySection.dbPath ? AnalysisBridge.getScaleBar(overlaySection.dbPath) : ({})
    }

    Connections {
        target: AnalysisBridge
        function onFeatureListReady(rows) {
            overlaySection.features = rows
            overlaySection.featuresLoading = false
            // Names may have arrived after channels were restored.
            overlaySection.channels = overlaySection.channels.map(function (c) {
                return Object.assign({}, c, { "label": overlaySection.labelForMz(c.mz) })
            })
        }
        function onOverlayRangeReady(r) {
            var ranges = Object.assign({}, overlaySection.channelRanges)
            ranges[r.layer + "|" + r.mz] = { "vmin": r.vmin, "vmax": r.vmax }
            overlaySection.channelRanges = ranges
        }
        function onActiveDatasetChanged() {
            overlaySection.channelRanges = {}
            overlaySection.refreshLayers()
            overlaySection.refreshSamples()
            overlaySection.refreshFeatures()
        }
    }

    // Remembered across tab switches.
    signal stateSaved(var state)
    Component.onDestruction: overlaySection.stateSaved({
        "channels": overlaySection.channels,
        "mode": overlaySection.mode,
        "layer": overlaySection.dataLayer,
        "showRois": overlaySection.showRois
    })
    function restoreState(s) {
        if (!s) return
        // Layer first: changing it resets the channels' bounds to automatic.
        if (overlaySection.layerOptions.indexOf(s.layer) >= 0) overlaySection.dataLayer = s.layer
        overlaySection.channels = s.channels || []
        overlaySection.mode = s.mode || "add"
        overlaySection.showRois = !!s.showRois
    }

    ColorDialog {
        id: channelColorDialog
        objectName: "overlayChannelColorDialog"
        options: ColorDialog.DontUseNativeDialog
        property int channelIndex: -1
        title: "Channel color"
        onAccepted: overlaySection.updateChannel(channelIndex, "color", selectedColor.toString())
    }

    SplitView {
        anchors.fill: parent
        anchors.margins: 8
        orientation: Qt.Horizontal

        // A gap on both sides of the divider (same handle as
        // ProjectHomePage): a 1 px line centered in a 12 px drag area.
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

        // ============================== controls
        ScrollView {
            id: controlsScroll
            SplitView.preferredWidth: 380
            SplitView.minimumWidth: 300
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                width: controlsScroll.availableWidth
                spacing: 8

                Label { text: "Add a feature"; font.bold: true }
                TextField {
                    objectName: "overlayNameSearchField"
                    Layout.fillWidth: true
                    placeholderText: "Search name…"
                    text: overlaySection.nameQuery
                    onTextChanged: overlaySection.nameQuery = text
                }
                TextField {
                    objectName: "overlayMzSearchField"
                    Layout.fillWidth: true
                    placeholderText: "Search m/z…"
                    text: overlaySection.mzQuery
                    onTextChanged: overlaySection.mzQuery = text
                }
                ListView {
                    id: overlayFeatureList
                    objectName: "overlayFeatureList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 200
                    clip: true
                    model: overlaySection.filteredFeatures
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    delegate: ItemDelegate {
                        required property var modelData
                        required property int index
                        objectName: "overlayFeature_" + index
                        width: overlayFeatureList.width
                        text: overlaySection.featureLabel(modelData)
                        enabled: overlaySection.channels.length < overlaySection.maxChannels
                                 && !overlaySection.hasChannel(modelData.mz)
                        onClicked: overlaySection.addChannel(modelData.mz)
                        ToolTip.visible: hovered && enabled
                        ToolTip.text: "Add as a channel"
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: palette.mid }

                Label {
                    objectName: "overlayChannelsTitle"
                    text: "Channels (" + overlaySection.channels.length + "/" + overlaySection.maxChannels + ")"
                    font.bold: true
                }
                Label {
                    visible: overlaySection.channels.length === 0
                    text: "Click features above to add them, one color each."
                    color: Theme.mutedTextColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                Repeater {
                    model: overlaySection.channels
                    delegate: Rectangle {
                        id: channelCard
                        required property var modelData
                        required property int index
                        objectName: "overlayChannel_" + index
                        Layout.fillWidth: true
                        implicitHeight: channelColumn.implicitHeight + 12
                        radius: 4
                        color: "transparent"
                        border.color: palette.mid

                        ColumnLayout {
                            id: channelColumn
                            anchors.fill: parent
                            anchors.margins: 6
                            spacing: 4

                            RowLayout {
                                Layout.fillWidth: true
                                Rectangle {
                                    objectName: "overlayChannelSwatch_" + channelCard.index
                                    width: 20
                                    height: 20
                                    radius: 3
                                    color: channelCard.modelData.color
                                    border.color: palette.mid
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            channelColorDialog.channelIndex = channelCard.index
                                            channelColorDialog.selectedColor = channelCard.modelData.color
                                            channelColorDialog.open()
                                        }
                                    }
                                    ToolTip.visible: swatchHover.hovered
                                    ToolTip.text: "Change color"
                                    HoverHandler { id: swatchHover }
                                }
                                AppCheckBox {
                                    objectName: "overlayChannelVisible_" + channelCard.index
                                    checked: channelCard.modelData.visible
                                    onClicked: overlaySection.updateChannel(channelCard.index, "visible", checked)
                                    ToolTip.visible: hovered
                                    ToolTip.text: "Show this channel"
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: channelCard.modelData.label
                                    elide: Text.ElideRight
                                }
                                ToolButton {
                                    objectName: "overlayChannelRemove_" + channelCard.index
                                    text: "✕"
                                    onClicked: overlaySection.removeChannel(channelCard.index)
                                    ToolTip.visible: hovered
                                    ToolTip.text: "Remove channel"
                                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppCheckBox {
                                    objectName: "overlayChannelAuto_" + channelCard.index
                                    text: "Auto"
                                    checked: channelCard.modelData.vmin === "" && channelCard.modelData.vmax === ""
                                    onClicked: {
                                        if (checked) overlaySection.setChannelRange(channelCard.index, "", "")
                                        else overlaySection.setChannelRange(channelCard.index,
                                                                           rangeSlider.first.value, rangeSlider.second.value)
                                    }
                                    ToolTip.visible: hovered
                                    ToolTip.text: "Automatic contrast, worked out for each sample on its own"
                                }
                                Item { Layout.fillWidth: true }
                                Label { text: "Gamma" }
                                TextField {
                                    objectName: "overlayChannelGamma_" + channelCard.index
                                    Layout.preferredWidth: 52
                                    text: String(channelCard.modelData.gamma)
                                    onEditingFinished: {
                                        var v = overlaySection.parseBound(text)
                                        if (v !== null && v !== "" && v > 0)
                                            overlaySection.updateChannel(channelCard.index, "gamma", v)
                                    }
                                    ToolTip.visible: hovered
                                    ToolTip.text: "Below 1 brightens faint pixels, above 1 darkens them."
                                }
                            }
                            // Min and Max on one track, over the feature's
                            // values in every sample. Applied on release:
                            // each change re-renders every tile.
                            RowLayout {
                                Layout.fillWidth: true
                                readonly property var range: overlaySection.rangeFor(channelCard.modelData.mz)
                                Label {
                                    objectName: "overlayChannelMinLabel_" + channelCard.index
                                    Layout.preferredWidth: 56
                                    text: rangeSlider.enabled ? overlaySection.formatValue(rangeSlider.first.value) : "…"
                                    color: channelCard.modelData.vmin === "" ? Theme.mutedTextColor : palette.windowText
                                }
                                RangeSlider {
                                    id: rangeSlider
                                    objectName: "overlayChannelRange_" + channelCard.index
                                    Layout.fillWidth: true
                                    enabled: parent.range !== null
                                    from: parent.range ? parent.range.vmin : 0
                                    to: parent.range ? Math.max(parent.range.vmax, parent.range.vmin + 1e-9) : 1
                                    first.value: channelCard.modelData.vmin === "" ? from : channelCard.modelData.vmin
                                    second.value: channelCard.modelData.vmax === "" ? to : channelCard.modelData.vmax
                                    function commit() {
                                        overlaySection.setChannelRange(channelCard.index, first.value, second.value)
                                    }
                                    first.onPressedChanged: if (!first.pressed) commit()
                                    second.onPressedChanged: if (!second.pressed) commit()
                                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                                }
                                Label {
                                    objectName: "overlayChannelMaxLabel_" + channelCard.index
                                    Layout.preferredWidth: 56
                                    horizontalAlignment: Text.AlignRight
                                    text: rangeSlider.enabled ? overlaySection.formatValue(rangeSlider.second.value) : "…"
                                    color: channelCard.modelData.vmax === "" ? Theme.mutedTextColor : palette.windowText
                                }
                            }
                        }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: palette.mid }

                GridLayout {
                    columns: 2
                    Layout.fillWidth: true
                    Label { text: "Blend" }
                    ComboBox {
                        objectName: "overlayModeCombo"
                        Layout.fillWidth: true
                        model: ["Additive", "Max"]
                        currentIndex: overlaySection.mode === "max" ? 1 : 0
                        onActivated: (index) => overlaySection.mode = index === 1 ? "max" : "add"
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    Label { text: "Layer" }
                    ComboBox {
                        objectName: "overlayLayerCombo"
                        Layout.fillWidth: true
                        model: overlaySection.layerOptions
                        currentIndex: model.indexOf(overlaySection.dataLayer)
                        onActivated: (index) => overlaySection.dataLayer = model[index]
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
                AppCheckBox {
                    objectName: "overlayShowRoisCheckBox"
                    text: "Show ROIs"
                    checked: overlaySection.showRois
                    onToggled: overlaySection.showRois = checked
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    color: Theme.mutedTextColor
                    text: "Additive: overlapping features mix (magenta + green = white, red + green = yellow). "
                          + "Max: each pixel keeps its brightest channel. Auto: contrast set for each sample on its own "
                          + "(the 99.5th percentile of the detected pixels as Max). Moving the slider sets one "
                          + "Min/Max for every sample, so samples compare."
                }
            }
        }

        // ============================== tiles
        ColumnLayout {
            SplitView.fillWidth: true
            spacing: 6

            // Legend: one swatch per shown channel.
            Flow {
                objectName: "overlayLegend"
                Layout.fillWidth: true
                spacing: 12
                Repeater {
                    model: overlaySection.channels.filter(function (c) { return c.visible })
                    delegate: Row {
                        required property var modelData
                        spacing: 4
                        Rectangle { width: 14; height: 14; radius: 2; color: modelData.color; anchors.verticalCenter: parent.verticalCenter }
                        Label { text: modelData.label }
                    }
                }
            }

            Label {
                objectName: "overlayEmptyLabel"
                visible: overlaySection.channelsToken === ""
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "Add features on the left to overlay them."
                color: Theme.mutedTextColor
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            // Same hand-stacked tiles as Visual Inspection (see the comment
            // on its heatmapGrid for why not a layout or positioner).
            Flickable {
                id: overlayFlick
                objectName: "overlayTilesFlick"
                visible: overlaySection.channelsToken !== ""
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: width
                contentHeight: Math.max(height, overlayGrid.height)
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}

                Item {
                    id: overlayGrid
                    width: overlayFlick.width
                    readonly property int tileHeight: 360
                    readonly property int tileSpacing: 8
                    height: overlayTiles.count * (tileHeight + tileSpacing)

                    Repeater {
                        id: overlayTiles
                        objectName: "overlayTilesRepeater"
                        model: overlaySection.samples
                        delegate: Item {
                            required property var modelData
                            required property int index
                            objectName: "overlayTile_" + modelData.name
                            x: (overlayGrid.width - width) / 2
                            y: index * (overlayGrid.tileHeight + overlayGrid.tileSpacing)
                            width: overlayGrid.width * 0.8
                            height: overlayGrid.tileHeight

                            ColumnLayout {
                                anchors.fill: parent
                                Label {
                                    text: modelData.name + ScaleBar.sizeSuffix(overlayImage.imageSize)
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                ZoomableImage {
                                    id: overlayImage
                                    objectName: "overlayImage_" + modelData.name
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    source: overlaySection.tileSource(modelData.name)
                                    scaleBarPixelUm: ScaleBar.pixelUm(overlaySection.scaleBar, modelData.name)
                                    scaleBarUm: overlaySection.scaleBar.bar_um || 0
                                    scaleBarColor: overlaySection.scaleBar.color || "white"

                                    RoiOverlay {
                                        objectName: "overlayRoiOverlay_" + modelData.name
                                        anchors.fill: parent
                                        readOnly: true
                                        visible: overlaySection.showRois
                                        effectiveScale: parent.effectiveScale
                                        savedRois: overlaySection.sampleRoisCache[modelData.name] || []
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

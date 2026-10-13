import QtQuick
import QtQuick.Controls
import QtGraphs
import "qrc:/Style"

// Interactive stick (centroid) spectrum plot on Qt Graphs (ADR 51), for
// the MS1 spectrum and the mirror plot.
//
// Qt Graphs has no stick series, so each group is one LineSeries that
// zigzags (m, 0) -> (m, y) -> (m, 0) from peak to peak. The return trips
// run along y = 0, under the zero line drawn on top.
//
// Hover and click pick the peak nearest the pointer in x (like Plotly's
// "x unified" hover), not series hit-testing, so a 1 px stick is as easy
// to hit as its whole column. The wheel zooms m/z, a drag pans it, and a
// double-click resets. y is never zoomed: it rescales to the tallest
// visible peak, or stays at yMin..yMax when those are set (mirror plot).
Item {
    id: plot

    // [{key, name, peaks: [[x, y], ...]}] — y < 0 is drawn downward (the
    // mirror plot's library half).
    property var groups: []
    // {key: color} for `groups`.
    property var colors: ({})
    // [[x, yFrom, yTo], ...] — dotted vertical segments (the mirror plot's
    // matched-pair connectors), legend entry `connectorName`.
    property var connectors: []
    property string connectorName: "Matched pair"
    property string xTitle: "m/z"
    property string yTitle: "Intensity"
    // Fixed y range. NaN (default): 0 .. 1.05 x the tallest visible peak.
    property real yMin: NaN
    property real yMax: NaN
    // y tick spacing, anchored at 0; 0 = automatic.
    property real yTickInterval: 0
    // Mirror plot: y tick labels show |y|, and the tooltip too.
    property bool mirrored: false
    // Corner captions inside the plot area (mirror plot's "Empirical" /
    // "Library").
    property string topCaption: ""
    property string bottomCaption: ""
    property string emptyText: "No peaks to show."

    signal peakClicked(real x)

    // Every peak of every group, sorted by x: [{x, y, key}].
    property var _peaks: []
    property var _hoverPeak: null
    readonly property bool hasPeaks: plot._peaks.length > 0

    onGroupsChanged: plot._rebuild()

    function _rebuild() {
        var peaks = []
        for (var g = 0; g < plot.groups.length; g++) {
            var group = plot.groups[g]
            for (var i = 0; i < group.peaks.length; i++)
                peaks.push({x: group.peaks[i][0], y: group.peaks[i][1], key: group.key})
        }
        peaks.sort(function (a, b) { return a.x - b.x })
        plot._peaks = peaks
        plot._hoverPeak = null
        plot.resetView()
    }

    function _dataXMin() { return plot._peaks.length ? plot._peaks[0].x : 0 }
    function _dataXMax() { return plot._peaks.length ? plot._peaks[plot._peaks.length - 1].x : 1 }

    function resetView() {
        var lo = plot._dataXMin(), hi = plot._dataXMax()
        var pad = Math.max((hi - lo) * 0.02, 1)
        xAxis.min = lo - pad
        xAxis.max = hi + pad
        xAxis.zoom = 1
        xAxis.pan = 0
        plot._fitY()
    }

    // y follows the visible m/z window unless pinned (yMin/yMax).
    function _fitY() {
        yAxis.zoom = 1
        yAxis.pan = 0
        if (!isNaN(plot.yMin) && !isNaN(plot.yMax)) {
            yAxis.min = plot.yMin
            yAxis.max = plot.yMax
            return
        }
        var lo = xAxis.visualMin, hi = xAxis.visualMax, top = 0
        for (var i = 0; i < plot._peaks.length; i++) {
            var p = plot._peaks[i]
            if (p.x >= lo && p.x <= hi && Math.abs(p.y) > top) top = Math.abs(p.y)
        }
        yAxis.min = 0
        yAxis.max = top > 0 ? top * 1.05 : 1
    }

    // Data -> pixel in `graph`'s coordinates, through the zoomed/panned
    // visible range.
    function xToPixel(x) {
        var a = graph.plotArea
        return a.x + (x - xAxis.visualMin) / (xAxis.visualMax - xAxis.visualMin) * a.width
    }
    function yToPixel(y) {
        var a = graph.plotArea
        return a.y + a.height - (y - yAxis.visualMin) / (yAxis.visualMax - yAxis.visualMin) * a.height
    }

    // The peak under a pointer at (px, py): the nearest in x within a few
    // pixels; among peaks at about the same x (the mirror plot's two
    // halves), the one whose tip is closest in y. null if none.
    function peakAtPixel(px, py) {
        var best = null, bestDy = Infinity, maxDx = 8
        for (var i = 0; i < plot._peaks.length; i++) {
            var p = plot._peaks[i]
            var dx = Math.abs(plot.xToPixel(p.x) - px)
            if (dx > maxDx) continue
            var dy = Math.abs(plot.yToPixel(p.y) - py) + dx
            if (dy < bestDy) { best = p; bestDy = dy }
        }
        return best
    }

    function _seriesPoints(peaks) {
        var points = []
        for (var i = 0; i < peaks.length; i++) {
            points.push(Qt.point(peaks[i][0], 0))
            points.push(Qt.point(peaks[i][0], peaks[i][1]))
            points.push(Qt.point(peaks[i][0], 0))
        }
        return points
    }

    function _formatValue(v) {
        return Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-2 && v !== 0)
               ? v.toExponential(3) : v.toFixed(3)
    }

    Label {
        objectName: "stickPlotEmptyLabel"
        anchors.centerIn: parent
        visible: !plot.hasPeaks
        text: plot.emptyText
        color: Theme.plotWeakColor
    }

    GraphsView {
        id: graph
        objectName: "stickPlotGraph"
        anchors.fill: parent
        visible: plot.hasPeaks
        zoomStyle: GraphsView.ZoomStyle.Center
        panStyle: GraphsView.PanStyle.Drag
        theme: GraphsTheme {
            colorScheme: Theme.isDark ? GraphsTheme.ColorScheme.Dark : GraphsTheme.ColorScheme.Light
            backgroundColor: Theme.backgroundColor
            plotAreaBackgroundColor: Theme.plotBackgroundColor
            labelTextColor: Theme.textColor
            grid.mainColor: Theme.plotGridColor
        }
        axisX: ValueAxis {
            id: xAxis
            titleText: plot.xTitle
            titleColor: Theme.textColor
            subGridVisible: false
            onVisualMinChanged: plot._fitY()
            onVisualMaxChanged: plot._fitY()
        }
        axisY: ValueAxis {
            id: yAxis
            titleText: plot.yTitle
            titleColor: Theme.textColor
            subGridVisible: false
            labelFormat: "%.3g"
            tickInterval: plot.yTickInterval
            tickAnchor: 0
            // Mirror plot: "1.0 0.5 0 0.5 1.0" — both halves read as
            // positive intensities.
            labelDelegate: plot.mirrored ? mirroredLabel : null
            // y only rescales (_fitY); the wheel/drag act on m/z alone.
            onZoomChanged: if (zoom !== 1) zoom = 1
            onPanChanged: if (pan !== 0) pan = 0
        }

        TapHandler {
            objectName: "stickPlotTapHandler"
            onTapped: (eventPoint) => {
                var p = plot.peakAtPixel(eventPoint.position.x, eventPoint.position.y)
                if (p) plot.peakClicked(p.x)
            }
            onDoubleTapped: plot.resetView()
        }
        HoverHandler {
            onPointChanged: {
                plot._hoverPeak = hovered
                    ? plot.peakAtPixel(point.position.x, point.position.y) : null
            }
            onHoveredChanged: if (!hovered) plot._hoverPeak = null
        }
    }

    Component {
        id: mirroredLabel
        Item {
            property string text
            implicitWidth: mirroredLabelText.implicitWidth
            implicitHeight: mirroredLabelText.implicitHeight
            Text {
                id: mirroredLabelText
                anchors.centerIn: parent
                color: Theme.textColor
                text: parent.text.replace("-", "")
            }
        }
    }

    // One zigzag series per group, added to/removed from the graph as
    // `groups` changes.
    Instantiator {
        model: plot.groups
        delegate: LineSeries {
            objectName: "stickSeries_" + modelData.key
            name: modelData.name
            color: plot.colors[modelData.key] || Theme.plotStrongColor
            width: 1.5
            Component.onCompleted: replace(plot._seriesPoints(modelData.peaks))
        }
        onObjectAdded: (index, object) => graph.insertSeries(index, object)
        onObjectRemoved: (index, object) => graph.removeSeries(object)
    }

    LineSeries {
        id: connectorSeries
        objectName: "stickPlotConnectors"
        color: Theme.plotWeakColor
        width: 1
        strokeStyle: LineSeries.StrokeStyle.DashLine
        dashPattern: [2, 3]
        zValue: -1
    }
    onConnectorsChanged: {
        var points = []
        for (var i = 0; i < plot.connectors.length; i++) {
            var c = plot.connectors[i]
            points.push(Qt.point(c[0], 0), Qt.point(c[0], c[1]), Qt.point(c[0], c[2]), Qt.point(c[0], 0))
        }
        connectorSeries.replace(points)
    }
    Component.onCompleted: graph.addSeries(connectorSeries)

    // The zero line, covering the zigzags' baseline return trips. An item
    // over the graph, not a series: neither series order nor zValue put a
    // series reliably on top of the others.
    Rectangle {
        objectName: "stickPlotZeroLine"
        visible: plot.hasPeaks && graph.plotArea.width > 0
        color: Theme.plotWeakColor
        x: graph.plotArea.x
        width: graph.plotArea.width
        height: 2
        y: plot.yToPixel(0) - height / 2
    }

    // Hovered peak: a dot on its tip and a tooltip next to it.
    Rectangle {
        objectName: "stickPlotHoverDot"
        visible: !!plot._hoverPeak
        width: 7; height: 7; radius: 3.5
        color: plot._hoverPeak ? (plot.colors[plot._hoverPeak.key] || Theme.plotStrongColor) : "transparent"
        x: plot._hoverPeak ? plot.xToPixel(plot._hoverPeak.x) - width / 2 : 0
        y: plot._hoverPeak ? plot.yToPixel(plot._hoverPeak.y) - height / 2 : 0
    }
    Rectangle {
        objectName: "stickPlotTooltip"
        visible: !!plot._hoverPeak
        color: Theme.controlColor
        border.color: Theme.borderColor
        radius: 3
        width: tooltipText.implicitWidth + 12
        height: tooltipText.implicitHeight + 8
        x: plot._hoverPeak ? Math.min(plot.xToPixel(plot._hoverPeak.x) + 10, plot.width - width) : 0
        y: plot._hoverPeak ? Math.max(0, Math.min(plot.yToPixel(plot._hoverPeak.y) - height / 2, plot.height - height)) : 0
        Text {
            id: tooltipText
            anchors.centerIn: parent
            color: Theme.textColor
            text: plot._hoverPeak
                  ? "m/z " + plot._hoverPeak.x.toFixed(4) + "\nintensity "
                    + plot._formatValue(plot.mirrored ? Math.abs(plot._hoverPeak.y) : plot._hoverPeak.y)
                  : ""
        }
    }

    // Legend: one swatch per non-empty group, plus the connectors'.
    Row {
        objectName: "stickPlotLegend"
        visible: plot.hasPeaks
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: 6
        spacing: 12
        Repeater {
            model: plot.groups.filter(function (g) { return g.name && g.peaks.length > 0 })
            delegate: Row {
                spacing: 4
                Rectangle {
                    width: 14; height: 3
                    anchors.verticalCenter: parent.verticalCenter
                    color: plot.colors[modelData.key] || Theme.plotStrongColor
                }
                Text { text: modelData.name; color: Theme.plotTextColor; font.pixelSize: 11 }
            }
        }
        Row {
            visible: plot.connectors.length > 0
            spacing: 4
            Rectangle {
                width: 14; height: 1
                anchors.verticalCenter: parent.verticalCenter
                color: Theme.plotWeakColor
            }
            Text { text: plot.connectorName; color: Theme.plotTextColor; font.pixelSize: 11 }
        }
    }

    Text {
        visible: plot.hasPeaks && plot.topCaption !== ""
        text: plot.topCaption
        color: Theme.plotTextColor
        font.pixelSize: 11
        x: graph.plotArea.x + graph.plotArea.width - width - 6
        y: graph.plotArea.y + 22
    }
    Text {
        visible: plot.hasPeaks && plot.bottomCaption !== ""
        text: plot.bottomCaption
        color: Theme.plotTextColor
        font.pixelSize: 11
        x: graph.plotArea.x + graph.plotArea.width - width - 6
        y: graph.plotArea.y + graph.plotArea.height - height - 6
    }
}

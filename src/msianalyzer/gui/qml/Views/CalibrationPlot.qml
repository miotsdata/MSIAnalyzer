import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtGraphs
import "qrc:/Style"

// The fitted calibration curve on Qt Graphs (ADR 51): standards as
// points, the fit as a dashed line, its equation and R² above.
// `plotData` is AnalysisBridge.getCalibrationPlotData's result ({} = nothing
// to show).
ColumnLayout {
    id: calibrationPlot
    property var plotData: ({})
    readonly property bool hasData: !!calibrationPlot.plotData.points
    // Hovered standard: [concentration, signal] or null.
    property var _hoverPoint: null

    spacing: 4

    onPlotDataChanged: {
        calibrationPlot._hoverPoint = null
        if (!calibrationPlot.hasData) return
        var pts = calibrationPlot.plotData.points, line = calibrationPlot.plotData.fitLine
        standards.replace(pts.map(function (p) { return Qt.point(p[0], p[1]) }))
        fit.replace(line.map(function (p) { return Qt.point(p[0], p[1]) }))
        // Axes span the line (5% past the standards) and every standard.
        var ys = pts.map(function (p) { return p[1] }).concat([line[0][1], line[1][1]])
        var yLo = Math.min.apply(null, ys), yHi = Math.max.apply(null, ys)
        var yPad = (yHi - yLo) * 0.08 || Math.abs(yHi) * 0.1 || 1
        xAxis.min = line[0][0]
        xAxis.max = line[1][0]
        yAxis.min = yLo - yPad
        yAxis.max = yHi + yPad
    }

    Label {
        objectName: "calibrationPlotTitle"
        text: calibrationPlot.hasData ? calibrationPlot.plotData.title : ""
        Layout.alignment: Qt.AlignHCenter
    }

    GraphsView {
        id: graph
        objectName: "calibrationPlotGraph"
        Layout.fillWidth: true
        Layout.fillHeight: true
        theme: GraphsTheme {
            colorScheme: Theme.isDark ? GraphsTheme.ColorScheme.Dark : GraphsTheme.ColorScheme.Light
            backgroundColor: Theme.backgroundColor
            plotAreaBackgroundColor: Theme.backgroundColor
            labelTextColor: Theme.textColor
            grid.mainColor: Theme.borderColor
        }
        // "%.3g", not Qt's default: concentrations can be ~1e-7, and the
        // axis title already carries the unit.
        axisX: ValueAxis {
            id: xAxis
            titleText: calibrationPlot.hasData ? calibrationPlot.plotData.xTitle : ""
            titleColor: Theme.textColor
            labelFormat: "%.3g"
            subGridVisible: false
        }
        axisY: ValueAxis {
            id: yAxis
            titleText: calibrationPlot.hasData ? calibrationPlot.plotData.yTitle : ""
            titleColor: Theme.textColor
            labelFormat: "%.3g"
            subGridVisible: false
        }

        LineSeries {
            id: fit
            objectName: "calibrationFitSeries"
            color: Theme.plotWeakColor
            width: 1.5
            strokeStyle: LineSeries.StrokeStyle.DashLine
            dashPattern: [5, 4]
        }
        ScatterSeries {
            id: standards
            objectName: "calibrationStandardsSeries"
            color: Theme.plotAccentColor
            pointDelegate: Rectangle {
                width: 9; height: 9; radius: 4.5
                color: Theme.plotAccentColor
            }
        }

        HoverHandler {
            onPointChanged: {
                var best = null, bestD = 10
                var pts = calibrationPlot.hasData ? calibrationPlot.plotData.points : []
                for (var i = 0; i < pts.length; i++) {
                    var d = Math.hypot(calibrationPlot._xToPixel(pts[i][0]) - point.position.x,
                                       calibrationPlot._yToPixel(pts[i][1]) - point.position.y)
                    if (d < bestD) { best = pts[i]; bestD = d }
                }
                calibrationPlot._hoverPoint = best
            }
            onHoveredChanged: if (!hovered) calibrationPlot._hoverPoint = null
        }

        Rectangle {
            objectName: "calibrationPlotTooltip"
            visible: !!calibrationPlot._hoverPoint
            color: Theme.controlColor
            border.color: Theme.borderColor
            radius: 3
            width: tooltipText.implicitWidth + 12
            height: tooltipText.implicitHeight + 8
            x: calibrationPlot._hoverPoint
               ? Math.min(calibrationPlot._xToPixel(calibrationPlot._hoverPoint[0]) + 10, graph.width - width) : 0
            y: calibrationPlot._hoverPoint
               ? Math.max(0, calibrationPlot._yToPixel(calibrationPlot._hoverPoint[1]) - height - 6) : 0
            Text {
                id: tooltipText
                anchors.centerIn: parent
                color: Theme.textColor
                text: calibrationPlot._hoverPoint
                      ? "concentration " + calibrationPlot._hoverPoint[0].toPrecision(4)
                        + "\nsignal " + calibrationPlot._hoverPoint[1].toPrecision(4)
                      : ""
            }
        }
    }

    function _xToPixel(x) {
        var a = graph.plotArea
        return a.x + (x - xAxis.visualMin) / (xAxis.visualMax - xAxis.visualMin) * a.width
    }
    function _yToPixel(y) {
        var a = graph.plotArea
        return a.y + a.height - (y - yAxis.visualMin) / (yAxis.visualMax - yAxis.visualMin) * a.height
    }
}

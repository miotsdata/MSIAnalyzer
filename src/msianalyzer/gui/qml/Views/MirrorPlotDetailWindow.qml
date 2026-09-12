import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtWebEngine

// The full interactive (Plotly) mirror plot — matched/unmatched
// coloring, hover tooltips, connector lines, all the metadata in the
// plot's own title/annotation box — plus the raw/filtered source
// toggles, in a separate top-level window rather than embedded inline.
// AnnotationsSection.qml's own panel only ever shows the fast, always-
// filtered static raster plot (AnalysisBridge.getBasicMirrorPlotImage);
// this window is what its "Details" button opens, and is the only place
// `emp_source`/`lib_source == "raw"` is reachable at all — see
// AnalysisBridge.requestMirrorPlot's docstring for why that specifically
// needed to move off the GUI thread (a background MirrorPlotWorker).
Window {
    id: detailWindow
    objectName: "mirrorPlotDetailWindow"
    title: "Mirror plot — " + (annotationId >= 0 ? "annotation " + annotationId : "")
    // Window defaults to visible: true — without this, one popped up
    // immediately on every Annotations tab open (never asked for), which
    // also threw off the *main* window's own layout measurements under
    // the offscreen QPA platform used for tests (a second top-level
    // window fighting the first for the same virtual screen).
    visible: false
    width: 900
    height: 650
    minimumWidth: 600
    minimumHeight: 400

    property string analysisDbPath: ""
    property int annotationId: -1
    property string empSource: "filtered"
    property string libSource: "filtered"
    property bool plotLoading: false

    function refresh() {
        if (analysisDbPath && annotationId >= 0) {
            detailWindow.plotLoading = true
            AnalysisBridge.requestMirrorPlot(
                detailWindow.analysisDbPath, detailWindow.annotationId,
                detailWindow.empSource, detailWindow.libSource)
        }
    }

    // Reset to filtered/filtered each time the window is (re)opened —
    // for a *different* annotation than last time, "raw" from the
    // previous one silently carrying over would be surprising, and it's
    // the cheap/always-available default besides.
    onVisibleChanged: {
        if (visible) {
            empSourceCombo.currentIndex = 0
            libSourceCombo.currentIndex = 0
            detailWindow.empSource = "filtered"
            detailWindow.libSource = "filtered"
            detailWindow.refresh()
        }
    }

    Connections {
        target: AnalysisBridge
        function onMirrorPlotReady(url) {
            if (detailWindow.visible) {
                mirrorPlotView.url = url
                detailWindow.plotLoading = false
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true

            Text { text: "Empirical:" }
            ComboBox {
                id: empSourceCombo
                objectName: "detailEmpSourceCombo"
                model: ["filtered", "raw"]
                currentIndex: 0
                onActivated: (index) => {
                    detailWindow.empSource = model[index]
                    detailWindow.refresh()
                }
            }
            Text { text: "Library:" }
            ComboBox {
                id: libSourceCombo
                objectName: "detailLibSourceCombo"
                model: ["filtered", "raw"]
                currentIndex: 0
                onActivated: (index) => {
                    detailWindow.libSource = model[index]
                    detailWindow.refresh()
                }
            }
            Item { Layout.fillWidth: true }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            WebEngineView {
                id: mirrorPlotView
                objectName: "detailMirrorPlotView"
                anchors.fill: parent
            }

            // A raw source can mean slow/remote file I/O (see
            // AnalysisBridge.requestMirrorPlot) — this is the visible
            // sign that it's in flight, not a frozen window.
            LoadingOverlay {
                objectName: "detailLoadingOverlay"
                anchors.fill: parent
                visible: detailWindow.plotLoading
            }
        }
    }
}

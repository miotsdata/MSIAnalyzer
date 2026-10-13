import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "qrc:/Style"

// The full interactive mirror plot (Qt Graphs StickPlot) — matched/unmatched
// coloring, hover tooltips, connector lines, "Empirical"/"Library" side
// labels on the plot itself — next to a metadata table (compound
// identity, scores, coverage, peak counts; see the MirrorPlotMetadataRow
// instances below) and the raw/filtered source toggles, in a separate
// top-level window rather than embedded inline.
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

    // A separate top-level Window (loaded via a Loader, not a visual
    // child of the ApplicationWindow) doesn't inherit Main.qml's palette
    // override — left unset, it fell back to Qt's own default (light)
    // palette regardless of the app's current theme, e.g. the metadata
    // table's value labels and the ComboBoxes stayed light/mismatched
    // even in dark mode. Mirrors Main.qml's palette block exactly, so it
    // reacts live to Theme.isDark the same way the main window does.
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

    property string analysisDbPath: ""
    property int annotationId: -1
    property string empSource: "filtered"
    property string libSource: "filtered"
    property bool plotLoading: false
    // AnalysisBridge.mirrorPlotReady's dict: mirror_plot_data, or {error}.
    property var plotData: ({})
    // Compound identity/score/coverage/peak-count fields — rendered as a
    // side table next to the plot (see the MirrorPlotMetadataRow instances
    // below) instead of packed into the plot's own title/corner
    // annotation. Independent of empSource/libSource, so fetched once per
    // annotation, not per toggle.
    property var metadata: ({})

    // Formatters for the side table's fields (MirrorPlotMetadataRow
    // instances below) — a fixed set of rows, not a Repeater over a JS
    // array: this codebase's test harness has a confirmed fragility with
    // locating Repeater-created items by objectName (see conftest.py's
    // `find_visual_child` docstring), and the field set here never varies
    // in length, so a Repeater buys nothing.
    function fmtFloat(v, digits) {
        return (v === undefined || v === null) ? "—" : Number(v).toFixed(digits)
    }
    function fmtInt(v) {
        return (v === undefined || v === null) ? "—" : String(v)
    }
    readonly property string compoundText: {
        var m = detailWindow.metadata || {}
        var text = m.compound_name || "—"
        if (m.compound_formula)
            text += "  (" + m.compound_formula + ")"
        return text
    }

    function refresh() {
        if (analysisDbPath && annotationId >= 0) {
            detailWindow.metadata = AnalysisBridge.getAnnotationMetadata(
                detailWindow.analysisDbPath, detailWindow.annotationId)
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

    // Single entry point for "Details" — one window reused across
    // clicks rather than a new one per hit. `onVisibleChanged` above
    // already covers going from closed to open, but a *second* click
    // while the window is already open never flips `visible`, so no
    // change signal fires there — this covers that case explicitly
    // instead of leaving the window showing the previous hit's stale
    // plot under a title bar that (misleadingly) already updated.
    function showFor(dbPath, id) {
        var wasVisible = detailWindow.visible
        detailWindow.analysisDbPath = dbPath
        detailWindow.annotationId = id
        detailWindow.visible = true
        detailWindow.raise()
        detailWindow.requestActivate()
        if (wasVisible) {
            empSourceCombo.currentIndex = 0
            libSourceCombo.currentIndex = 0
            detailWindow.empSource = "filtered"
            detailWindow.libSource = "filtered"
            detailWindow.refresh()
        }
    }

    Connections {
        target: AnalysisBridge
        function onMirrorPlotReady(data) {
            if (detailWindow.visible) {
                detailWindow.plotData = data
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

            Label { text: "Empirical:" }
            ComboBox {
                id: empSourceCombo
                objectName: "detailEmpSourceCombo"
                model: ["filtered", "raw"]
                currentIndex: 0
                onActivated: (index) => {
                    detailWindow.empSource = model[index]
                    detailWindow.refresh()
                }
                HoverHandler {
                    objectName: "detailEmpSourceComboHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Label { text: "Library:" }
            ComboBox {
                id: libSourceCombo
                objectName: "detailLibSourceCombo"
                model: ["filtered", "raw"]
                currentIndex: 0
                onActivated: (index) => {
                    detailWindow.libSource = model[index]
                    detailWindow.refresh()
                }
                HoverHandler {
                    objectName: "detailLibSourceComboHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Item { Layout.fillWidth: true }
        }

        // A fixed 70/30 SplitView: each pane's min/max width is pinned to
        // its preferred width, so the handle can't be dragged. Each
        // pane's `height:` is bound explicitly: SplitView doesn't reliably
        // fill the cross axis in the real app (same fix as
        // AnnotationsSection.qml's panes).
        SplitView {
            id: detailContentSplit
            objectName: "detailContentSplit"
            orientation: Qt.Horizontal
            Layout.fillWidth: true
            Layout.fillHeight: true

            Item {
                id: plotPane
                objectName: "detailPlotPane"
                height: detailContentSplit.height
                SplitView.preferredWidth: detailContentSplit.width * 0.7
                SplitView.minimumWidth: SplitView.preferredWidth
                SplitView.maximumWidth: SplitView.preferredWidth

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 4
                    Label {
                        objectName: "detailMirrorPlotTitle"
                        text: detailWindow.plotData.title || ""
                        visible: text !== ""
                        Layout.alignment: Qt.AlignHCenter
                        elide: Text.ElideRight
                        Layout.maximumWidth: parent.width
                    }
                    StickPlot {
                        id: mirrorPlotView
                        objectName: "detailMirrorPlotView"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        groups: detailWindow.plotData.groups || []
                        connectors: detailWindow.plotData.connectors || []
                        emptyText: detailWindow.plotData.error || (detailWindow.plotLoading ? "" : "No peaks to show.")
                        colors: ({matched: Theme.plotStrongColor, unmatched: Theme.plotWeakColor})
                        yTitle: "Normalised intensity"
                        yMin: -1.15
                        yMax: 1.15
                        yTickInterval: 0.5
                        mirrored: true
                        topCaption: "Empirical"
                        bottomCaption: "Library"
                    }
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

            // Score/coverage/peak-count/identity table, next to the plot
            // instead of packed into its title/corner annotation. A plain
            // Item pane (like plotPane) with the actual content anchored
            // inside and given a left margin, rather than putting
            // SplitView.* directly on the ColumnLayout — that margin is
            // what keeps the text from sitting flush against the divider.
            Item {
                id: metadataPane
                objectName: "detailMetadataPane"
                height: detailContentSplit.height
                SplitView.preferredWidth: detailContentSplit.width * 0.3
                SplitView.minimumWidth: SplitView.preferredWidth
                SplitView.maximumWidth: SplitView.preferredWidth

                ColumnLayout {
                    id: metadataPanel
                    objectName: "detailMetadataPanel"
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    spacing: 8

                    Label { text: "Details"; font.bold: true }

                    MirrorPlotMetadataRow {
                        label: "Compound"; valueObjectName: "detailMetadataCompound"
                        value: detailWindow.compoundText
                    }
                    MirrorPlotMetadataRow {
                        label: "InChIKey"; valueObjectName: "detailMetadataInchikey"
                        value: detailWindow.metadata.inchikey || "—"
                    }
                    MirrorPlotMetadataRow {
                        label: "Library"; valueObjectName: "detailMetadataLibrary"
                        value: detailWindow.metadata.library_name || "—"
                    }
                    MirrorPlotMetadataRow {
                        label: "Score"; valueObjectName: "detailMetadataScore"
                        value: detailWindow.fmtFloat(detailWindow.metadata.score, 4)
                    }
                    MirrorPlotMetadataRow {
                        label: "Dot product"; valueObjectName: "detailMetadataDotProduct"
                        value: detailWindow.fmtFloat(detailWindow.metadata.dot_product_score, 4)
                    }
                    MirrorPlotMetadataRow {
                        label: "Coverage score"; valueObjectName: "detailMetadataCoverageScore"
                        value: detailWindow.fmtFloat(detailWindow.metadata.coverage_score, 4)
                    }
                    MirrorPlotMetadataRow {
                        label: "Library coverage"; valueObjectName: "detailMetadataLibCoverage"
                        value: detailWindow.fmtFloat(detailWindow.metadata.lib_coverage, 2)
                    }
                    MirrorPlotMetadataRow {
                        label: "Empirical coverage"; valueObjectName: "detailMetadataEmpCoverage"
                        value: detailWindow.fmtFloat(detailWindow.metadata.emp_coverage, 2)
                    }
                    MirrorPlotMetadataRow {
                        label: "Matched / library peaks"; valueObjectName: "detailMetadataMatchedPeaks"
                        value: detailWindow.fmtInt(detailWindow.metadata.n_matched_peaks) + " / "
                               + detailWindow.fmtInt(detailWindow.metadata.n_lib_peaks)
                    }
                    MirrorPlotMetadataRow {
                        label: "Empirical peaks (filtered/raw)"; valueObjectName: "detailMetadataEmpPeaks"
                        value: detailWindow.fmtInt(detailWindow.metadata.n_emp_peaks_filtered) + " / "
                               + detailWindow.fmtInt(detailWindow.metadata.n_emp_peaks_raw)
                    }
                    MirrorPlotMetadataRow {
                        label: "Scan ID"; valueObjectName: "detailMetadataScanId"
                        value: detailWindow.fmtInt(detailWindow.metadata.scan_id)
                    }
                    MirrorPlotMetadataRow {
                        label: "Precursor m/z"; valueObjectName: "detailMetadataPrecursorMz"
                        value: detailWindow.fmtFloat(detailWindow.metadata.precursor_mz, 4)
                    }
                    MirrorPlotMetadataRow {
                        label: "Fragment tolerance"; valueObjectName: "detailMetadataFragmentTolerance"
                        value: detailWindow.fmtFloat(detailWindow.metadata.fragment_ppm_tolerance, 1) + " ppm"
                    }

                    Item { Layout.fillHeight: true }
                }
            }
        }
    }
}

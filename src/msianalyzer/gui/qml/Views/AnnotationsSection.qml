import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine

Item {
    id: annotationsSection
    objectName: "annotationsSection"

    property var analysis
    property var rows: (analysis && analysis.analysisDbPath)
                        ? AnalysisBridge.getAnnotationTable(analysis.analysisDbPath) : []

    // "User can decide to sort for any of these" (feature id, name, m/z) —
    // clicking a column header sorts by it, clicking the same header again
    // flips direction. Sorting never changes `selectedFeatureId` itself
    // (see below) — only the row order.
    property string sortColumn: "feature_id"
    property bool sortAscending: true

    function sortBy(column) {
        if (annotationsSection.sortColumn === column) {
            annotationsSection.sortAscending = !annotationsSection.sortAscending
        } else {
            annotationsSection.sortColumn = column
            annotationsSection.sortAscending = true
        }
    }

    readonly property var sortedRows: {
        var col = annotationsSection.sortColumn
        var dir = annotationsSection.sortAscending ? 1 : -1
        var arr = annotationsSection.rows.slice()
        arr.sort(function (a, b) {
            var av = a[col]
            var bv = b[col]
            if (av === bv) return 0
            if (av === null || av === undefined) return 1
            if (bv === null || bv === undefined) return -1
            if (typeof av === "string" || typeof bv === "string")
                return dir * String(av).localeCompare(String(bv))
            return dir * (av < bv ? -1 : 1)
        })
        return arr
    }

    // Nothing selected until the user picks a feature — a live binding to
    // "the sorted list's first row" would otherwise jump the selection
    // around every time the user re-sorts, which sorting should never do.
    property int selectedFeatureId: -1

    property int topN: 5
    property var topHits: []
    property var selectedHit: null
    property string empSource: "filtered"
    property string libSource: "filtered"

    function refreshTopHits() {
        annotationsSection.topHits = (analysis && analysis.analysisDbPath
                                       && annotationsSection.selectedFeatureId >= 0)
            ? AnalysisBridge.getFeatureTopHits(analysis.analysisDbPath,
                                                annotationsSection.selectedFeatureId,
                                                annotationsSection.topN)
            : []
        annotationsSection.selectedHit = annotationsSection.topHits.length > 0
                                          ? annotationsSection.topHits[0] : null
    }

    function refreshMirrorPlot() {
        if (annotationsSection.selectedHit && analysis && analysis.analysisDbPath) {
            // A file:// url, not loadHtml() — see AnalysisBridge.getMirrorPlotUrl:
            // loadHtml()/setHtml() silently fail past Qt's ~2MB limit, and a
            // plot with Plotly.js embedded is already ~4.6MB on its own.
            mirrorPlotView.url = AnalysisBridge.getMirrorPlotUrl(
                analysis.analysisDbPath, annotationsSection.selectedHit.id,
                annotationsSection.empSource, annotationsSection.libSource)
        }
    }

    onSelectedFeatureIdChanged: refreshTopHits()
    onSelectedHitChanged: refreshMirrorPlot()
    onEmpSourceChanged: refreshMirrorPlot()
    onLibSourceChanged: refreshMirrorPlot()

    Text {
        id: emptyStateLabel
        objectName: "annotationsEmptyStateLabel"
        visible: rows.length === 0
        anchors.centerIn: parent
        text: "No annotated features."
        color: "gray"
    }

    // A resizable split (not a fixed-fraction RowLayout — dragging the
    // handle should actually work, and the two sides want independent
    // widths depending on how many/long the feature names are) between
    // the feature table and the detail panel.
    SplitView {
        id: annotationsSplit
        objectName: "annotationsSplit"
        anchors.fill: parent
        anchors.margins: 16
        visible: rows.length > 0
        orientation: Qt.Horizontal

        ColumnLayout {
            id: tablePanel
            objectName: "annotationsTablePanel"
            SplitView.preferredWidth: annotationsSplit.width * 0.4
            SplitView.minimumWidth: 240
            spacing: 8

            Text {
                text: "Annotated features (" + annotationsSection.rows.length + ")"
                font.bold: true
                font.pixelSize: 14
            }

            RowLayout {
                objectName: "annotationsTableHeader"
                Layout.fillWidth: true
                spacing: 4

                Repeater {
                    model: [
                        {key: "feature_id", label: "Feature", width: 70},
                        {key: "compound_name", label: "Name", width: -1},
                        {key: "mz", label: "m/z", width: 90},
                        {key: "best_score", label: "Score", width: 70},
                    ]

                    delegate: MouseArea {
                        objectName: "annotationSortHeader_" + modelData.key
                        Layout.preferredWidth: modelData.width > 0 ? modelData.width : -1
                        Layout.fillWidth: modelData.width < 0
                        implicitHeight: headerLabel.implicitHeight
                        cursorShape: Qt.PointingHandCursor
                        onClicked: annotationsSection.sortBy(modelData.key)

                        Text {
                            id: headerLabel
                            anchors.fill: parent
                            font.bold: true
                            elide: Text.ElideRight
                            text: modelData.label
                                  + (annotationsSection.sortColumn === modelData.key
                                     ? (annotationsSection.sortAscending ? " ▲" : " ▼")
                                     : "")
                        }
                    }
                }
            }

            Flickable {
                id: tableFlickable
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: width
                contentHeight: tableColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds

                ColumnLayout {
                    id: tableColumn
                    width: tableFlickable.width
                    spacing: 4

                    Repeater {
                        id: annotationsRepeater
                        objectName: "annotationsRepeater"
                        model: annotationsSection.sortedRows

                        delegate: Rectangle {
                            objectName: "annotationRow_" + modelData.feature_id
                            Layout.fillWidth: true
                            height: 40
                            radius: 4
                            color: annotationsSection.selectedFeatureId === modelData.feature_id
                                   ? palette.highlight : "transparent"
                            border.color: "#dddddd"
                            border.width: 1

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: annotationsSection.selectedFeatureId = modelData.feature_id
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 4

                                Text {
                                    objectName: "annotationRowFeature_" + modelData.feature_id
                                    text: String(modelData.feature_id)
                                    Layout.preferredWidth: 70
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Text {
                                    objectName: "annotationRowCompound_" + modelData.feature_id
                                    text: modelData.compound_name || modelData.inchikey || "?"
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Text {
                                    objectName: "annotationRowMz_" + modelData.feature_id
                                    text: modelData.mz !== null && modelData.mz !== undefined
                                          ? Number(modelData.mz).toFixed(4) : "?"
                                    Layout.preferredWidth: 90
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                                Text {
                                    objectName: "annotationRowScore_" + modelData.feature_id
                                    text: Number(modelData.best_score).toFixed(3)
                                    Layout.preferredWidth: 70
                                    color: annotationsSection.selectedFeatureId === modelData.feature_id
                                           ? palette.highlightedText : palette.text
                                }
                            }
                        }
                    }
                }
            }
        }

        ColumnLayout {
            id: detailPanel
            objectName: "annotationsDetailPanel"
            SplitView.fillWidth: true
            SplitView.minimumWidth: 320
            spacing: 12

            Text {
                objectName: "annotationsDetailEmptyLabel"
                visible: annotationsSection.selectedFeatureId < 0
                text: "Click a feature to see its top hits and mirror plot."
                color: "gray"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            // "Top hit should be 40% of the available height for the
            // column... mirror plot part should take 60%" — a plain
            // ColumnLayout with one child's `Layout.preferredHeight` set
            // and the other's `Layout.fillHeight: true` turned out to
            // *not* actually respect the preferred value here (measured:
            // the fillHeight sibling got squeezed to its own minimum and
            // the "preferred" one silently claimed the leftover space
            // instead — the opposite of both flags' meaning). A nested
            // vertical SplitView with two explicit `SplitView.preferredHeight`
            // shares avoids that entirely — same mechanism already
            // proven for the table/detail 40/60 *width* split above —
            // and, in keeping with the "resizable SplitView over a fixed
            // ratio" desktop-app direction, the user can still drag the
            // divider if 40/60 isn't right for a given feature.
            SplitView {
                id: detailSplit
                objectName: "detailSplit"
                orientation: Qt.Vertical
                visible: annotationsSection.selectedFeatureId >= 0
                Layout.fillWidth: true
                Layout.fillHeight: true

                // Row 1: top-N hits for the selected feature — "like in
                // ms1", but each hit also states which sample it came
                // from, since a feature's best hit for one compound can
                // come from any of its samples. Scrolls internally
                // (topHitsFlickable) if there isn't room for all of them.
                ColumnLayout {
                    objectName: "topHitsPanel"
                    SplitView.preferredHeight: detailSplit.height * 0.4
                    SplitView.minimumHeight: 80
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Top hits:"
                            font.bold: true
                            Layout.fillWidth: true
                        }
                        SpinBox {
                            id: topNSpin
                            objectName: "topNSpinBox"
                            editable: true
                            from: 1
                            to: 20
                            value: annotationsSection.topN
                            onValueModified: annotationsSection.topN = value
                        }
                    }

                    Text {
                        objectName: "topHitsEmptyLabel"
                        visible: annotationsSection.topHits.length === 0
                        text: "No annotation hits for this feature."
                        color: "gray"
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }

                    Flickable {
                        id: topHitsFlickable
                        objectName: "topHitsFlickable"
                        visible: annotationsSection.topHits.length > 0
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: topHitsColumn.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds

                        ColumnLayout {
                            id: topHitsColumn
                            width: topHitsFlickable.width
                            spacing: 2

                            Repeater {
                                id: topHitsRepeater
                                objectName: "topHitsRepeater"
                                model: annotationsSection.topHits

                                delegate: Rectangle {
                                    objectName: "topHit_" + modelData.id
                                    Layout.fillWidth: true
                                    height: 30
                                    radius: 4
                                    color: annotationsSection.selectedHit
                                           && annotationsSection.selectedHit.id === modelData.id
                                           ? palette.highlight : "transparent"

                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: annotationsSection.selectedHit = modelData
                                    }

                                    Text {
                                        objectName: "topHitLabel_" + modelData.id
                                        anchors.fill: parent
                                        anchors.leftMargin: 4
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        textFormat: Text.StyledText
                                        color: annotationsSection.selectedHit
                                               && annotationsSection.selectedHit.id === modelData.id
                                               ? palette.highlightedText : palette.text
                                        text: (index + 1) + ". "
                                              + (modelData.compound_name || modelData.inchikey || "?")
                                              + " (score <b>" + Number(modelData.score).toFixed(3) + "</b>)"
                                              + "  ·  " + (modelData.sample_name || "?")
                                              + (modelData.library_name ? "  ·  " + modelData.library_name : "")
                                    }
                                }
                            }
                        }
                    }
                }

                // Row 2: empirical (top) vs library (bottom) mirror plot
                // for the selected hit, with an independent raw/filtered
                // toggle for each side. "cannot scroll, so plot adapts to
                // it" is handled on the plot's own side too, see
                // AnalysisBridge.getMirrorPlotUrl.
                ColumnLayout {
                    objectName: "mirrorPlotPanel"
                    SplitView.preferredHeight: detailSplit.height * 0.6
                    SplitView.minimumHeight: 120
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true

                        Text { text: "Empirical:" }
                        ComboBox {
                            id: empSourceCombo
                            objectName: "empSourceCombo"
                            model: ["filtered", "raw"]
                            currentIndex: 0
                            onActivated: (index) => annotationsSection.empSource = model[index]
                        }
                        Text { text: "Library:" }
                        ComboBox {
                            id: libSourceCombo
                            objectName: "libSourceCombo"
                            model: ["filtered", "raw"]
                            currentIndex: 0
                            onActivated: (index) => annotationsSection.libSource = model[index]
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Text {
                        objectName: "mirrorPlotEmptyLabel"
                        visible: !annotationsSection.selectedHit
                        text: "No hit selected."
                        color: "gray"
                    }

                    WebEngineView {
                        id: mirrorPlotView
                        objectName: "mirrorPlotView"
                        visible: !!annotationsSection.selectedHit
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                    }
                }
            }
        }
    }
}

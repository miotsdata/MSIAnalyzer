import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

// "Analyses > Re-annotate…" (ADR 63): re-run the MS2 stages of a finished
// analysis (group -> purity -> annotate -> consensus) with new settings,
// without re-running the analysis. Every annotation state is kept in the
// History tab and can be restored or loaded back into the settings.
Dialog {
    id: reannotateDialog
    objectName: "reannotateDialog"
    title: "Re-annotate"
    modal: true
    width: Math.min(820, (parent ? parent.width : 900) - 60)
    height: Math.min(680, (parent ? parent.height : 760) - 60)
    anchors.centerIn: parent
    standardButtons: Dialog.Close

    readonly property var groupKeys: ["group_ms2", "purity", "annotate", "consensus"]
    readonly property var groups: ConfigSchema.groups.filter(function (g) {
        return groupKeys.indexOf(g.key) >= 0
    })

    property var analysis: null
    property var settings: ({})          // {group: {field: value}}
    property var history: []             // AnalysisBridge.getAnnotationHistory
    property int pendingDelete: -1       // history id awaiting confirmation
    property bool running: false
    property int progressDone: 0
    property string statusText: ""
    property bool statusError: false

    function openFor(analysisModel) {
        analysis = analysisModel
        statusText = ""
        statusError = false
        pendingDelete = -1
        refresh()
        tabs.currentIndex = 0
        open()
    }

    function refresh() {
        var db = analysis.analysisDbPath
        history = AnalysisBridge.getAnnotationHistory(db)
        settings = AnalysisBridge.getAnnotationSettings(db, analysis.runConfig)
    }

    function collect() {
        var out = {}
        for (var i = 0; i < formRepeater.count; i++) {
            var form = formRepeater.itemAt(i).form
            out[form.group.key] = form.collect()
        }
        return out
    }

    function start(label) {
        running = true
        progressDone = 0
        statusText = label
        statusError = false
    }

    function reannotate() {
        start("Re-annotating…")
        AnalysisBridge.reannotate(analysis.analysisDbPath, analysis.runId, collect(),
                                  analysis.runConfig, analysis.startDate)
    }

    function restore(entryId) {
        start("Restoring annotation #" + entryId + "…")
        AnalysisBridge.restoreAnnotation(analysis.analysisDbPath, entryId,
                                         analysis.runConfig, analysis.startDate)
    }

    function deleteEntry(entryId) {
        pendingDelete = -1
        var error = AnalysisBridge.deleteAnnotationHistoryEntry(analysis.analysisDbPath, entryId)
        statusText = error ? "Could not delete: " + error : "Annotation #" + entryId + " deleted."
        statusError = !!error
        refresh()
    }

    function kindLabel(e) {
        if (e.kind === "pipeline") return "Analysis run"
        if (e.kind === "restore") return "Restored #" + e.restored_from
        return "Re-annotation"
    }

    // The few settings that tell annotations apart, on one line.
    function summary(s) {
        var a = s.annotate || {}, g = s.group_ms2 || {}
        var libs = a.library_path ? [].concat(a.library_path) : []
        var names = libs.map(function (p) { return String(p).split(/[\\/]/).pop() })
        return (names.length ? names.join(", ") : "no library")
               + "  ·  candidate " + a.candidate_ppm + " ppm"
               + "  ·  min shared fragments " + a.min_matched_peaks
               + "  ·  flat CV ≤ " + g.flat_fragmentation_cv_threshold
    }

    Connections {
        target: AnalysisBridge
        function onDatasetTaskProgress(done, total, step) {
            if (reannotateDialog.running) reannotateDialog.progressDone = done
        }
        function onAnnotationTaskFinished(message) {
            if (!reannotateDialog.running) return
            reannotateDialog.running = false
            reannotateDialog.refresh()
            // Keep the run's saved config describing what's in the analysis.
            var error = reannotateDialog.analysis.project
                ? reannotateDialog.analysis.project.updateRunConfigSections(
                      reannotateDialog.analysis.runId, reannotateDialog.settings)
                : ""
            reannotateDialog.statusText = message
                + (error ? " (The project file could not be updated: " + error + ")" : "")
            reannotateDialog.statusError = !!error
        }
        function onAnnotationTaskFailed(message) {
            if (!reannotateDialog.running) return
            reannotateDialog.running = false
            reannotateDialog.refresh()
            reannotateDialog.statusText = message
            reannotateDialog.statusError = true
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        Label {
            text: "Re-runs MS2 grouping, precursor purity, library annotation and consensus "
                  + "with the settings below. Features, images and normalization stay as they are. "
                  + "The current annotation is kept in History and can be restored."
            color: Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        TabBar {
            id: tabs
            objectName: "reannotateTabs"
            Layout.fillWidth: true
            Repeater {
                model: reannotateDialog.groups
                TabButton { text: modelData.title; width: implicitWidth }
            }
            TabButton {
                objectName: "reannotateHistoryTab"
                text: "History (" + reannotateDialog.history.length + ")"
                width: implicitWidth
            }
        }

        StackLayout {
            currentIndex: tabs.currentIndex
            Layout.fillWidth: true
            Layout.fillHeight: true
            enabled: !reannotateDialog.running

            Repeater {
                id: formRepeater
                model: reannotateDialog.groups
                delegate: ScrollView {
                    property alias form: groupForm
                    clip: true
                    contentWidth: availableWidth
                    ConfigGroupForm {
                        id: groupForm
                        width: parent.width
                        group: modelData
                        values: reannotateDialog.settings[modelData.key] || {}
                        onHelpRequested: (title, body) => helpDialog.show(title, body)
                    }
                }
            }

            ColumnLayout {
                spacing: 6
                Label {
                    visible: reannotateDialog.history.length === 0
                    text: "No re-annotation yet. The analysis' own annotation is recorded here "
                          + "the first time you re-annotate."
                    color: Theme.mutedTextColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                ListView {
                    id: historyList
                    objectName: "reannotateHistoryList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: reannotateDialog.history.length
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    // Newest first.
                    delegate: Rectangle {
                        property var entry: reannotateDialog.history[reannotateDialog.history.length - 1 - index]
                        objectName: "reannotateHistoryRow_" + entry.id
                        width: historyList.width
                        height: rowLayout.implicitHeight + 12
                        radius: 4
                        color: entry.current ? Theme.secondaryColor : "transparent"
                        border.color: palette.mid

                        ColumnLayout {
                            id: rowLayout
                            anchors.fill: parent
                            anchors.margins: 6
                            spacing: 2
                            RowLayout {
                                Label {
                                    text: "#" + entry.id + "  ·  " + entry.created_at.replace("T", " ")
                                          + "  ·  " + reannotateDialog.kindLabel(entry)
                                          + "  ·  " + entry.n_annotated_features + " matched"
                                          + (entry.current ? "  ·  current" : "")
                                    font.bold: entry.current
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Button {
                                    objectName: "reannotateLoadSettings_" + entry.id
                                    text: "Load settings"
                                    flat: true
                                    onClicked: {
                                        reannotateDialog.settings = entry.settings
                                        tabs.currentIndex = 0
                                        reannotateDialog.statusText = "Settings of annotation #" + entry.id
                                            + " loaded. Click Re-annotate to apply them."
                                        reannotateDialog.statusError = false
                                    }
                                }
                                Button {
                                    objectName: "reannotateRestore_" + entry.id
                                    text: "Restore"
                                    enabled: !entry.current
                                    onClicked: reannotateDialog.restore(entry.id)
                                }
                                Button {
                                    objectName: "reannotateDelete_" + entry.id
                                    text: "Delete"
                                    flat: true
                                    enabled: !entry.current
                                    onClicked: reannotateDialog.pendingDelete = entry.id
                                }
                            }
                            Label {
                                text: reannotateDialog.summary(entry.settings)
                                color: Theme.mutedTextColor
                                font.pixelSize: Theme.captionPixelSize
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
                RowLayout {
                    visible: reannotateDialog.pendingDelete >= 0
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: Theme.errorColor
                        text: "Delete annotation #" + reannotateDialog.pendingDelete
                              + "? It can't be restored afterwards."
                    }
                    Button {
                        objectName: "reannotateConfirmDelete"
                        text: "Delete"
                        onClicked: reannotateDialog.deleteEntry(reannotateDialog.pendingDelete)
                    }
                    Button {
                        text: "Cancel"
                        flat: true
                        onClicked: reannotateDialog.pendingDelete = -1
                    }
                }
            }
        }

        ProgressBar {
            objectName: "reannotateProgressBar"
            visible: reannotateDialog.running
            Layout.fillWidth: true
            from: 0
            to: 4
            value: reannotateDialog.progressDone
        }
        Label {
            objectName: "reannotateStatusLabel"
            visible: reannotateDialog.statusText !== ""
            text: reannotateDialog.statusText
            color: reannotateDialog.statusError ? Theme.errorColor : Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        RowLayout {
            Item { Layout.fillWidth: true }
            Button {
                objectName: "reannotateRunButton"
                text: "Re-annotate"
                highlighted: true
                enabled: !reannotateDialog.running
                onClicked: reannotateDialog.reannotate()
            }
        }
    }

    Dialog {
        id: helpDialog
        modal: true
        standardButtons: Dialog.Close
        anchors.centerIn: parent
        width: Math.min(520, reannotateDialog.width - 80)
        height: Math.min(420, reannotateDialog.height - 80)
        property string body: ""

        function show(title, text) {
            helpDialog.title = title
            helpDialog.body = text
            open()
        }

        ScrollView {
            anchors.fill: parent
            clip: true
            Text {
                width: helpDialog.availableWidth
                textFormat: Text.MarkdownText
                wrapMode: Text.Wrap
                text: helpDialog.body
                color: Theme.textColor
            }
        }
    }
}

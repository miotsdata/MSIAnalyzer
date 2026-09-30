import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "qrc:/Style"

// "Analyses > Import analyzed .h5ad…" (ADR 49): re-import a notebook-
// analyzed .h5ad (the analysis' merged file or one sample's file) into
// the open analysis as a linked dataset. Flow: pick a file, then
// AnalysisBridge.inspectH5ad reads its metadata in the background (backed
// mode, never X). Next, choose a name and map each source sample to an
// analysis sample. AnalysisBridge.importH5ad then splits it per sample
// in the background, with per-sample progress.
//
// Sample mapping rows are a Repeater over a plain *integer* count,
// indexing into the `sourceLabels`/`mapping` arrays, not a Repeater over
// an array of objects. This codebase's test harness has a confirmed
// fragility with those (see Main.qml's recentRun comment).
Dialog {
    id: importDialog
    objectName: "importH5adDialog"
    title: "Import analyzed .h5ad"
    modal: true
    closePolicy: importing ? Popup.NoAutoClose : (Popup.CloseOnEscape | Popup.CloseOnPressOutside)
    width: Math.min(640, (parent ? parent.width : 800) - 80)
    height: Math.min(640, (parent ? parent.height : 700) - 80)
    anchors.centerIn: parent

    property var analysis: null
    // Analysis sample names, the right-hand side of every mapping row.
    property var analysisSamples: []

    property string sourcePath: ""
    property var inspection: null   // H5adInspection.to_dict(), or null
    property bool inspecting: false
    property bool importing: false
    property string errorText: ""
    property string sampleColumn: ""  // "" = single-sample file
    property var sourceLabels: []     // mapping rows, left-hand side
    property var mapping: []          // analysis sample per row, "" = skip
    property int progressDone: 0
    property int progressTotal: 0

    signal imported(int datasetId)

    function resetState() {
        sourcePath = ""
        inspection = null
        inspecting = false
        importing = false
        errorText = ""
        sampleColumn = ""
        sourceLabels = []
        mapping = []
        progressDone = 0
        progressTotal = 0
        nameField.text = ""
    }

    function openFor(analysisModel) {
        resetState()
        analysis = analysisModel
        var samples = AnalysisBridge.getSamples(analysisModel.analysisDbPath)
        analysisSamples = samples.map(function (s) { return s.name })
        open()
    }

    function fileStem(path) {
        var name = path.split(/[\\/]/).pop()
        return name.replace(/\.h5ad$/i, "")
    }

    // Rebuild mapping rows for the current sample column: one per
    // distinct value, auto-matched to an analysis sample of the exact
    // same name, else "" (skipped until the user picks one).
    function rebuildMapping() {
        if (!inspection) {
            sourceLabels = []
            mapping = []
            return
        }
        var labels
        if (sampleColumn !== "") {
            labels = inspection.sample_values[sampleColumn] || []
        } else {
            // Single-sample file: one row. Pre-select the sample the
            // pipeline recorded in uns["spatial"], if it's ours.
            var key = inspection.spatial_keys.length > 0 ? inspection.spatial_keys[0] : ""
            labels = [key !== "" ? key : fileStem(sourcePath)]
        }
        var m = []
        for (var i = 0; i < labels.length; i++)
            m.push(analysisSamples.indexOf(labels[i]) >= 0 ? labels[i] : "")
        sourceLabels = labels
        mapping = m
    }

    function setMapping(row, value) {
        var m = mapping.slice()
        m[row] = value
        mapping = m
    }

    readonly property int mappedCount: {
        var n = 0
        for (var i = 0; i < mapping.length; i++)
            if (mapping[i] !== "") n++
        return n
    }

    readonly property bool hasDuplicateTargets: {
        var seen = {}
        for (var i = 0; i < mapping.length; i++) {
            if (mapping[i] === "") continue
            if (seen[mapping[i]]) return true
            seen[mapping[i]] = true
        }
        return false
    }

    readonly property bool analysisIdMismatch: inspection !== null && analysis !== null
                                               && inspection.analysis_id !== null
                                               && inspection.analysis_id !== undefined
                                               && inspection.analysis_id !== analysis.runId

    readonly property bool canImport: inspection !== null && !importing && !inspecting
                                      && nameField.text.trim() !== "" && mappedCount > 0
                                      && !hasDuplicateTargets && inspection.has_mz

    function startImport() {
        var map = {}
        for (var i = 0; i < mapping.length; i++)
            if (mapping[i] !== "") map[sourceLabels[i]] = mapping[i]
        errorText = ""
        importing = true
        progressDone = 0
        progressTotal = mappedCount
        AnalysisBridge.importH5ad(analysis.analysisDbPath, sourcePath,
                                  nameField.text.trim(), sampleColumn, map)
    }

    function openHelp(title, body) {
        importHelpDialog.helpTitle = title
        importHelpDialog.helpBody = body
        importHelpDialog.open()
    }

    Connections {
        target: AnalysisBridge
        function onH5adInspected(info) {
            if (!importDialog.inspecting) return
            importDialog.inspecting = false
            importDialog.inspection = info
            importDialog.sampleColumn = info.sample_column ? info.sample_column : ""
            if (nameField.text === "")
                nameField.text = importDialog.fileStem(importDialog.sourcePath)
            importDialog.rebuildMapping()
        }
        function onH5adInspectFailed(message) {
            if (!importDialog.inspecting) return
            importDialog.inspecting = false
            importDialog.errorText = message
        }
        function onDatasetTaskProgress(done, total, sampleName) {
            if (!importDialog.importing) return
            importDialog.progressDone = done
            importDialog.progressTotal = total
        }
        function onDatasetImportFinished(datasetId, message) {
            if (!importDialog.importing) return
            importDialog.importing = false
            importDialog.imported(datasetId)
            importDialog.close()
        }
        function onDatasetImportFailed(message) {
            if (!importDialog.importing) return
            importDialog.importing = false
            importDialog.errorText = message
        }
    }

    FileDialog {
        id: h5adFileDialog
        objectName: "importH5adFileDialog"
        fileMode: FileDialog.OpenFile
        nameFilters: ["AnnData files (*.h5ad)", "All files (*)"]
        options: Qt.platform.pluginName === "offscreen" ? FileDialog.DontUseNativeDialog : 0
        onAccepted: {
            importDialog.sourcePath = Router.toLocalPath(selectedFile)
            importDialog.inspection = null
            importDialog.errorText = ""
            importDialog.inspecting = true
            nameField.text = ""
            AnalysisBridge.inspectH5ad(importDialog.sourcePath)
        }
    }

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 10

            // --- File -----------------------------------------------------
            RowLayout {
                Layout.fillWidth: true
                Label { text: "File"; font.bold: true }
                HelpButton {
                    onClicked: importDialog.openHelp("File",
                        "The analyzed `.h5ad` to bring back: either this analysis' "
                        + "**merged.h5ad** or one sample's `.h5ad`, after your own "
                        + "processing (QC, dimensionality reduction, clustering, ...).\n\n"
                        + "Pixels you filtered out and features you dropped stay out. "
                        + "New `obs` columns (e.g. `leiden`), `obsm` (e.g. `X_umap`) and "
                        + "layers are kept. Every pixel is matched back to its sample "
                        + "by pixel id (or, failing that, by x/y), so the H&E "
                        + "registration of each sample applies automatically.\n\n"
                        + "Only the `obs` of your file is ever written to: when you "
                        + "draw an ROI on this dataset, its `roi_<name>` column is "
                        + "added there too. The file is read once and split into a "
                        + "per-sample copy inside the analysis folder.\n\n"
                        + "**Interaction:** None.")
                }
            }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    objectName: "importSourcePathField"
                    Layout.fillWidth: true
                    readOnly: true
                    placeholderText: "Choose a .h5ad file…"
                    text: importDialog.sourcePath
                }
                Button {
                    objectName: "importBrowseButton"
                    text: "Browse…"
                    enabled: !importDialog.importing
                    onClicked: h5adFileDialog.open()
                }
            }

            BusyIndicator {
                visible: importDialog.inspecting
                running: visible
                Layout.alignment: Qt.AlignHCenter
            }

            Label {
                objectName: "importSummaryLabel"
                visible: importDialog.inspection !== null
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: importDialog.inspection === null ? "" :
                      (importDialog.inspection.kind === "merged" ? "Merged file" : "Single-sample file")
                      + " — " + importDialog.inspection.n_obs + " pixels, "
                      + importDialog.inspection.n_vars + " features"
                      + (importDialog.inspection.layers.length > 0
                         ? ", layers: " + importDialog.inspection.layers.join(", ") : "")
            }
            Label {
                objectName: "importAnalysisMismatchLabel"
                visible: importDialog.analysisIdMismatch
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.warningColor
                text: "This file was produced by a different analysis ("
                      + (importDialog.inspection ? importDialog.inspection.analysis_id : "")
                      + "). Pixels will still be matched, but its features may not line up with this analysis."
            }
            Label {
                visible: importDialog.inspection !== null && !importDialog.inspection.has_mz
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.errorColor
                text: "This file's var has no 'mz' column (and its var names aren't mz_<value>), "
                      + "so its features can't be matched to m/z values."
            }

            // --- Name -----------------------------------------------------
            RowLayout {
                visible: importDialog.inspection !== null
                Label { text: "Dataset name"; font.bold: true }
                HelpButton {
                    onClicked: importDialog.openHelp("Dataset name",
                        "How this import is listed in the workspace's **Dataset** "
                        + "switcher. It must be unique within this analysis.\n\n"
                        + "Importing the same file again under a new name creates a "
                        + "second, independent dataset (e.g. to compare two clustering "
                        + "runs).\n\n**Interaction:** None.")
                }
            }
            TextField {
                id: nameField
                objectName: "importNameField"
                visible: importDialog.inspection !== null
                Layout.fillWidth: true
                enabled: !importDialog.importing
            }

            // --- Sample column (merged only) ------------------------------
            RowLayout {
                visible: importDialog.inspection !== null && importDialog.sampleColumn !== ""
                Label { text: "Sample column"; font.bold: true }
                HelpButton {
                    onClicked: importDialog.openHelp("Sample column",
                        "The `obs` column that says which sample each pixel belongs "
                        + "to. MSIAnalyzer's own merged file calls it **sample**.\n\n"
                        + "Pick another column only if you renamed or rebuilt it in "
                        + "your notebook.\n\n**Interaction:** changes the rows of the "
                        + "sample mapping below.")
                }
            }
            ComboBox {
                id: sampleColumnCombo
                objectName: "importSampleColumnCombo"
                visible: importDialog.inspection !== null && importDialog.sampleColumn !== ""
                Layout.fillWidth: true
                enabled: !importDialog.importing
                model: importDialog.inspection ? Object.keys(importDialog.inspection.sample_values) : []
                currentIndex: model.indexOf(importDialog.sampleColumn)
                onActivated: (index) => {
                    importDialog.sampleColumn = model[index]
                    importDialog.rebuildMapping()
                }
            }

            // --- Mapping --------------------------------------------------
            RowLayout {
                visible: importDialog.inspection !== null
                Label {
                    text: importDialog.sampleColumn !== "" ? "Samples" : "Sample"
                    font.bold: true
                }
                HelpButton {
                    onClicked: importDialog.openHelp("Sample mapping",
                        "Which sample of this analysis each part of the file belongs "
                        + "to. Rows whose name matches an analysis sample are pre-filled; "
                        + "set the others by hand, or leave them on **(skip)** to not "
                        + "import them.\n\n"
                        + "Every pixel must belong to the sample it's mapped to "
                        + "(checked by pixel id, then by x/y coordinates). A wrong "
                        + "mapping makes the import stop with an error instead of "
                        + "importing misplaced pixels.\n\n"
                        + "**Interaction:** two rows can't map to the same analysis sample.")
                }
            }
            GridLayout {
                visible: importDialog.inspection !== null
                Layout.fillWidth: true
                columns: 3
                columnSpacing: 8

                Repeater {
                    model: importDialog.sourceLabels.length * 3
                    delegate: Loader {
                        readonly property int row: Math.floor(index / 3)
                        readonly property int col: index % 3
                        Layout.fillWidth: col === 2
                        sourceComponent: col === 0 ? sourceLabelCell
                                         : (col === 1 ? arrowCell : targetCell)
                        onLoaded: item.row = row
                    }
                }
            }
            Label {
                visible: importDialog.hasDuplicateTargets
                color: Theme.errorColor
                text: "Two rows are mapped to the same analysis sample."
            }

            // --- Progress / errors -----------------------------------------
            ProgressBar {
                objectName: "importProgressBar"
                visible: importDialog.importing
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, importDialog.progressTotal)
                value: importDialog.progressDone
                indeterminate: importDialog.progressDone === 0
            }
            Label {
                visible: importDialog.importing
                text: "Importing… " + importDialog.progressDone + " / " + importDialog.progressTotal + " samples"
            }
            Label {
                objectName: "importErrorLabel"
                visible: importDialog.errorText !== ""
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.errorColor
                text: importDialog.errorText
            }
        }
    }

    Component {
        id: sourceLabelCell
        Label {
            property int row: 0
            text: importDialog.sourceLabels[row] !== undefined ? importDialog.sourceLabels[row] : ""
            elide: Text.ElideRight
            Layout.maximumWidth: 200
        }
    }
    Component {
        id: arrowCell
        Label { property int row: 0; text: "→" }
    }
    Component {
        id: targetCell
        ComboBox {
            property int row: 0
            objectName: "importMappingCombo_" + row
            enabled: !importDialog.importing
            model: ["(skip)"].concat(importDialog.analysisSamples)
            currentIndex: {
                var v = importDialog.mapping[row]
                return v ? Math.max(0, model.indexOf(v)) : 0
            }
            onActivated: (index) => importDialog.setMapping(row, index === 0 ? "" : model[index])
        }
    }

    footer: DialogButtonBox {
        Button {
            objectName: "importConfirmButton"
            text: "Import"
            enabled: importDialog.canImport
            DialogButtonBox.buttonRole: DialogButtonBox.ApplyRole
            onClicked: importDialog.startImport()
        }
        Button {
            text: importDialog.importing ? "Close" : "Cancel"
            enabled: !importDialog.importing
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            onClicked: importDialog.close()
        }
    }

    // Same shared, read-only Markdown help window as NewAnalysisPage.qml's
    // fieldHelpDialog (ADR 44).
    Dialog {
        id: importHelpDialog
        objectName: "importHelpDialog"
        modal: true
        standardButtons: Dialog.Close
        anchors.centerIn: parent
        width: Math.min(520, importDialog.width - 40)
        height: Math.min(420, importDialog.height - 40)
        property string helpTitle: ""
        property string helpBody: ""
        title: helpTitle

        ScrollView {
            anchors.fill: parent
            clip: true
            Text {
                objectName: "importHelpDialogText"
                width: importHelpDialog.availableWidth
                textFormat: Text.MarkdownText
                wrapMode: Text.Wrap
                text: importHelpDialog.helpBody
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

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "qrc:/Style"

// "Analyses > Match target list…" (ADR 60): match a target list against a
// finished analysis without re-running it. Same inputs as the config's
// `target_list` section, pre-filled from this analysis' own config. "Find
// matches" fills a review table (nothing saved yet); the user keeps the
// rows whose target name should label the feature, and "Apply" saves
// them. Compounds no feature matched are listed greyed out: injecting
// them needs the raw data, which only a pipeline run reads.
//
// The table is a ListView over a plain *integer* count indexing `order`
// into `rows`, not over an array of objects (see ImportH5adDialog's
// comment on the test harness).
Dialog {
    id: targetDialog
    objectName: "targetListMatchDialog"
    title: "Match target list"
    modal: true
    width: Math.min(1100, (parent ? parent.width : 1200) - 60)
    height: Math.min(760, (parent ? parent.height : 800) - 60)
    anchors.centerIn: parent
    standardButtons: Dialog.Close

    property var analysis: null
    property var files: []
    property string polarity: "positive"
    property var adducts: []           // selected adduct labels
    property var rows: []               // preview rows (Python dicts)
    property var useFlags: []           // per row of `rows`
    property var order: []              // indices into `rows`, filtered + sorted
    property string sortKey: "name"
    property bool sortAscending: true
    property string statusText: ""
    property bool statusError: false
    property int postRunCount: 0

    readonly property var adductOptions: !ConfigSchema ? []
                                         : targetDialog.polarity === "negative"
                                         ? ConfigSchema.negativeAdducts : ConfigSchema.positiveAdducts
    readonly property int selectedCount: targetDialog.useFlags.filter(function (u) { return u }).length

    function openFor(analysisModel) {
        analysis = analysisModel
        var cfg = analysisModel.targetListConfig || {}
        polarity = cfg.polarity === "negative" ? "negative" : "positive"
        adducts = (cfg.adducts && cfg.adducts.length) ? cfg.adducts.slice() : adductOptions.slice()
        ppmField.text = String(cfg.match_ppm !== undefined ? cfg.match_ppm : 10)
        files = []
        rows = []
        useFlags = []
        order = []
        statusText = ""
        statusError = false
        refreshPostRunCount()
        open()
    }

    function refreshPostRunCount() {
        postRunCount = analysis ? AnalysisBridge.countPostRunTargetList(analysis.analysisDbPath) : 0
    }

    function setPolarity(value) {
        if (value === polarity) return
        polarity = value
        adducts = adductOptions.slice()   // the other polarity's adducts don't apply
    }

    function toggleAdduct(label, on) {
        var rest = adducts.filter(function (a) { return a !== label })
        adducts = on ? rest.concat([label]) : rest
    }

    function setStatus(text, isError) {
        statusText = text
        statusError = !!isError
    }

    function findMatches() {
        var result = AnalysisBridge.previewPostRunTargetList(
            analysis.analysisDbPath, files, polarity, adducts, Number(ppmField.text))
        if (result.error) {
            rows = []
            useFlags = []
            order = []
            setStatus(result.error, true)
            return
        }
        rows = result.rows
        useFlags = rows.map(function (r) { return !!r.use })
        var detected = rows.filter(function (r) { return r.detected }).length
        setStatus(detected + " match(es), " + (rows.length - detected) + " compound(s) not detected.", false)
        refreshOrder()
    }

    function refreshOrder() {
        var q = searchField.text.trim().toLowerCase()
        var idx = []
        for (var i = 0; i < rows.length; i++) {
            if (!q || rows[i].name.toLowerCase().indexOf(q) !== -1
                    || (rows[i].current_name || "").toLowerCase().indexOf(q) !== -1)
                idx.push(i)
        }
        var key = sortKey
        var dir = sortAscending ? 1 : -1
        idx.sort(function (a, b) {
            var ra = rows[a], rb = rows[b]
            // Not-detected rows always last: nothing to sort them by.
            if (ra.detected !== rb.detected) return ra.detected ? -1 : 1
            var va = key === "ppm_diff" ? Math.abs(ra.ppm_diff) : ra[key]
            var vb = key === "ppm_diff" ? Math.abs(rb.ppm_diff) : rb[key]
            if (typeof va === "string") return dir * va.localeCompare(vb)
            return dir * (va - vb)
        })
        order = idx
    }

    function sortBy(key) {
        if (sortKey === key) sortAscending = !sortAscending
        else { sortKey = key; sortAscending = true }
        refreshOrder()
    }

    // One target name per feature: ticking a row unticks the others on
    // the same feature, or the feature would show both names joined.
    function setUse(rowIndex, on) {
        var flags = useFlags.slice()
        if (on) {
            for (var i = 0; i < rows.length; i++) {
                if (i !== rowIndex && rows[i].feature_id === rows[rowIndex].feature_id)
                    flags[i] = false
            }
        }
        flags[rowIndex] = on
        useFlags = flags
    }

    function setAllUse(on) {
        var flags = rows.map(function () { return false })
        if (on) {
            var taken = {}
            // Visible rows only, closest match per feature wins.
            var visible = order.filter(function (i) { return rows[i].detected })
            visible.sort(function (a, b) { return Math.abs(rows[a].ppm_diff) - Math.abs(rows[b].ppm_diff) })
            visible.forEach(function (i) {
                if (!taken[rows[i].feature_id]) {
                    taken[rows[i].feature_id] = true
                    flags[i] = true
                }
            })
        }
        useFlags = flags
    }

    function apply() {
        var kept = rows.filter(function (r, i) { return useFlags[i] })
        var error = AnalysisBridge.applyPostRunTargetList(
            analysis.analysisDbPath, analysis.runId, kept,
            {"paths": files, "polarity": polarity, "adducts": adducts,
             "match_ppm": Number(ppmField.text)})
        if (error) {
            setStatus("Could not save: " + error, true)
            return
        }
        refreshPostRunCount()
        findMatches()   // "Current name" now shows what was saved
        setStatus(kept.length + " name(s) saved to the analysis.", false)
    }

    function fileName(path) { return path.split(/[\\/]/).pop() }
    function fmt(value, digits) { return Number(value).toFixed(digits) }

    readonly property var columns: [
        {"key": "use", "title": "Use", "width": 44},
        {"key": "name", "title": "Target", "width": 190},
        {"key": "formula", "title": "Formula", "width": 110},
        {"key": "adduct", "title": "Adduct", "width": 90},
        {"key": "theoretical_mz", "title": "Theor. m/z", "width": 95},
        {"key": "feature_id", "title": "Feature", "width": 65},
        {"key": "feature_mz", "title": "Feature m/z", "width": 95},
        {"key": "ppm_diff", "title": "ppm", "width": 60},
        {"key": "current_name", "title": "Current name", "width": 200},
    ]

    FileDialog {
        id: targetFileDialog
        objectName: "targetListFileDialog"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Target lists (*.csv *.txt *.tsv)", "All files (*)"]
        options: Qt.platform.pluginName === "offscreen" ? FileDialog.DontUseNativeDialog : 0
        onAccepted: {
            var added = targetDialog.files.slice()
            selectedFiles.forEach(function (url) {
                var path = Router.toLocalPath(url)
                if (added.indexOf(path) < 0) added.push(path)
            })
            targetDialog.files = added
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        // --- Search settings (same as the config's target_list) ------
        RowLayout {
            spacing: 8
            Label { text: "Target lists:"; font.bold: true }
            Repeater {
                model: targetDialog.files.length
                delegate: RowLayout {
                    spacing: 2
                    Label { text: targetDialog.fileName(targetDialog.files[index]) }
                    ToolButton {
                        objectName: "targetListRemoveFile_" + index
                        text: "✕"
                        implicitWidth: 22
                        implicitHeight: 22
                        onClicked: targetDialog.files = targetDialog.files.filter(
                                       function (f, i) { return i !== index })
                    }
                }
            }
            Button {
                objectName: "targetListAddFilesButton"
                text: "Add files…"
                onClicked: targetFileDialog.open()
            }
            Item { Layout.fillWidth: true }
        }

        Label {
            objectName: "targetListColumnsHint"
            text: "Columns: name, then formula and/or mz (inchikey optional). "
                  + "A row with an m/z is searched at that m/z only, even if it also has a formula."
            color: Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: 8
            Label { text: "Polarity:" }
            ComboBox {
                objectName: "targetListPolarityCombo"
                model: ["positive", "negative"]
                currentIndex: targetDialog.polarity === "negative" ? 1 : 0
                onActivated: (index) => targetDialog.setPolarity(model[index])
            }
            Label { text: "Tolerance (ppm):" }
            TextField {
                id: ppmField
                objectName: "targetListPpmField"
                implicitWidth: 70
                validator: DoubleValidator { bottom: 0.01; top: 1000 }
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "targetListFindButton"
                text: "Find matches"
                highlighted: true
                enabled: targetDialog.files.length > 0 && targetDialog.adducts.length > 0
                         && ppmField.acceptableInput
                onClicked: targetDialog.findMatches()
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 4
            Label { text: "Adducts:"; height: 28; verticalAlignment: Text.AlignVCenter }
            Repeater {
                model: targetDialog.adductOptions.length
                delegate: AppCheckBox {
                    property string label: targetDialog.adductOptions[index]
                    objectName: "targetListAdduct_" + label
                    text: label
                    checked: targetDialog.adducts.indexOf(label) >= 0
                    onToggled: {
                        targetDialog.toggleAdduct(label, checked)
                        checked = Qt.binding(function () { return targetDialog.adducts.indexOf(label) >= 0 })
                    }
                }
            }
        }

        Label {
            objectName: "targetListStatusLabel"
            visible: targetDialog.statusText !== ""
            text: targetDialog.statusText
            color: targetDialog.statusError ? Theme.errorColor : Theme.mutedTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        // --- Review table ---------------------------------------------
        RowLayout {
            visible: targetDialog.rows.length > 0
            spacing: 8
            TextField {
                id: searchField
                objectName: "targetListSearchField"
                placeholderText: "Search name…"
                implicitWidth: 220
                onTextChanged: targetDialog.refreshOrder()
            }
            Button {
                objectName: "targetListUseAllButton"
                text: "All"
                flat: true
                onClicked: targetDialog.setAllUse(true)
            }
            Button {
                objectName: "targetListUseNoneButton"
                text: "None"
                flat: true
                onClicked: targetDialog.setAllUse(false)
            }
            Label {
                text: targetDialog.selectedCount + " selected"
                color: Theme.mutedTextColor
            }
            Item { Layout.fillWidth: true }
        }

        Row {
            visible: targetDialog.rows.length > 0
            Repeater {
                model: targetDialog.columns.length
                delegate: Label {
                    property var column: targetDialog.columns[index]
                    width: column.width
                    text: column.title + (targetDialog.sortKey === column.key
                                          ? (targetDialog.sortAscending ? " ▲" : " ▼") : "")
                    font.bold: true
                    elide: Text.ElideRight
                    TapHandler {
                        enabled: column.key !== "use"
                        onTapped: targetDialog.sortBy(column.key)
                    }
                }
            }
        }

        ListView {
            id: matchList
            objectName: "targetListMatchList"
            visible: targetDialog.rows.length > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: targetDialog.order.length
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: Row {
                id: matchRow
                property int rowIndex: targetDialog.order[index]
                property var row: targetDialog.rows[rowIndex]
                objectName: "targetListRow_" + index
                height: 30
                opacity: row.detected ? 1 : 0.45

                Item {
                    width: targetDialog.columns[0].width
                    height: parent.height
                    AppCheckBox {
                        objectName: "targetListUse_" + index
                        anchors.verticalCenter: parent.verticalCenter
                        visible: matchRow.row.detected
                        checked: !!targetDialog.useFlags[matchRow.rowIndex]
                        onToggled: {
                            targetDialog.setUse(matchRow.rowIndex, checked)
                            checked = Qt.binding(function () {
                                return !!targetDialog.useFlags[matchRow.rowIndex]
                            })
                        }
                    }
                }
                Repeater {
                    model: targetDialog.columns.length - 1
                    delegate: Label {
                        property string key: targetDialog.columns[index + 1].key
                        width: targetDialog.columns[index + 1].width
                        height: matchRow.height
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        text: {
                            var r = matchRow.row
                            if (!r.detected && key !== "name" && key !== "formula")
                                return key === "adduct" ? "not detected" : ""
                            switch (key) {
                            case "theoretical_mz":
                            case "feature_mz": return targetDialog.fmt(r[key], 4)
                            case "ppm_diff": return targetDialog.fmt(r[key], 1)
                            default: return String(r[key] === undefined ? "" : r[key])
                            }
                        }
                        ToolTip.visible: hover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: hover }
                    }
                }
            }
        }

        Item { Layout.fillHeight: true; visible: targetDialog.rows.length === 0 }

        // --- Actions ----------------------------------------------------
        RowLayout {
            spacing: 8
            Button {
                objectName: "targetListRemovePostRunButton"
                text: "Remove post-run matches (" + targetDialog.postRunCount + ")"
                enabled: targetDialog.postRunCount > 0
                onClicked: {
                    var n = AnalysisBridge.removePostRunTargetList(targetDialog.analysis.analysisDbPath)
                    targetDialog.refreshPostRunCount()
                    if (targetDialog.rows.length > 0) targetDialog.findMatches()
                    targetDialog.setStatus(n + " post-run match(es) removed.", false)
                }
                ToolTip.visible: hovered
                ToolTip.text: "Undo every name saved here. Matches from the analysis run itself are kept."
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "targetListApplyButton"
                text: "Apply (" + targetDialog.selectedCount + ")"
                highlighted: true
                enabled: targetDialog.selectedCount > 0
                onClicked: targetDialog.apply()
            }
        }
    }
}

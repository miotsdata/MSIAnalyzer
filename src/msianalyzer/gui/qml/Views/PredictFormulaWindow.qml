import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "qrc:/Style"

// On-demand formula prediction (ADR 27): the user picks a subset of
// features (any mix of unannotated and annotated-but-unconvincing — a
// real score is fine, they just pick the rows) and adducts/settings, then
// runs msbuddy against exactly that selection. A separate top-level
// Window, Loader-spawned from AnnotationsSection.qml's "Predict
// formula…" button — same pattern as RoiDesignWindow.qml/
// MirrorPlotDetailWindow.qml.
Window {
    id: predictWindow
    objectName: "predictFormulaWindow"
    title: "Predict formula"
    // Window defaults to visible: true — without this, one popped up
    // immediately whenever the Loader that owns it gets constructed, same
    // fix as RoiDesignWindow.qml/MirrorPlotDetailWindow.qml.
    visible: false
    width: 900
    height: 650
    minimumWidth: 700
    minimumHeight: 450

    // A separate top-level Window doesn't inherit Main.qml's palette
    // override — see MirrorPlotDetailWindow.qml's identical block/comment
    // for why. Mirrors it exactly.
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
    property var features: []  // [{feature_id, mz, compound_name, best_score}]
    property var selectedFeatureIds: []
    property bool predicting: false

    // Polarity is local to this window — unlike the New Analysis target-
    // list tab, there's nothing else on this window it needs to be
    // shared with.
    property string polarity: "positive"
    property var selectedAdducts: []
    property int topN: 5
    property real errorPpm: 10.0
    property bool halogen: false

    // Emitted after a successful run so AnnotationsSection.qml can
    // refresh its table/top-hits (a predicted formula can change a
    // feature's representative identity — see analysis_db's 3-tier
    // precedence, ADR 27).
    signal predictionFinished()

    function toggleFeature(featureId, checked) {
        var arr = predictWindow.selectedFeatureIds.slice()
        var idx = arr.indexOf(featureId)
        if (checked && idx === -1)
            arr.push(featureId)
        else if (!checked && idx !== -1)
            arr.splice(idx, 1)
        predictWindow.selectedFeatureIds = arr
    }

    function toggleAdduct(label, checked) {
        var arr = predictWindow.selectedAdducts.slice()
        var idx = arr.indexOf(label)
        if (checked && idx === -1)
            arr.push(label)
        else if (!checked && idx !== -1)
            arr.splice(idx, 1)
        predictWindow.selectedAdducts = arr
    }

    readonly property var unannotatedFeatureIds: {
        var ids = []
        for (var i = 0; i < predictWindow.features.length; i++) {
            if (!predictWindow.features[i].compound_name)
                ids.push(predictWindow.features[i].feature_id)
        }
        return ids
    }
    // Drives the button's own label — "select" vs "deselect" — so it's
    // clear which way one more click will go.
    readonly property bool allUnannotatedSelected:
        predictWindow.unannotatedFeatureIds.length > 0
        && predictWindow.unannotatedFeatureIds.every(function (id) {
            return predictWindow.selectedFeatureIds.indexOf(id) !== -1
        })

    // A toggle, not a one-way action — a mis-click used to have no way
    // back short of unchecking every row by hand. Adds/removes exactly
    // the unannotated ids (a union/subtraction, not a wholesale
    // replace), so any feature picked by hand elsewhere in the list
    // survives either direction.
    function toggleSelectAllUnannotated() {
        var targetIds = predictWindow.unannotatedFeatureIds
        if (predictWindow.allUnannotatedSelected) {
            predictWindow.selectedFeatureIds = predictWindow.selectedFeatureIds.filter(
                function (id) { return targetIds.indexOf(id) === -1 })
        } else {
            var merged = predictWindow.selectedFeatureIds.slice()
            for (var i = 0; i < targetIds.length; i++) {
                if (merged.indexOf(targetIds[i]) === -1)
                    merged.push(targetIds[i])
            }
            predictWindow.selectedFeatureIds = merged
        }
    }

    // Single entry point — one window reused across clicks, refreshed
    // with the current feature list every time it's (re)opened.
    function showFor(dbPath) {
        predictWindow.analysisDbPath = dbPath
        predictWindow.selectedFeatureIds = []
        predictWindow.errorLabelText = ""
        predictWindow.features = dbPath ? AnalysisBridge.getFeaturesForPrediction(dbPath) : []
        predictWindow.visible = true
        predictWindow.raise()
        predictWindow.requestActivate()
    }

    property string errorLabelText: ""

    Connections {
        target: AnalysisBridge
        function onFormulaPredictionFinished(dbPath) {
            if (dbPath === predictWindow.analysisDbPath) {
                predictWindow.predicting = false
                // Refreshes the picker's own list too, not just
                // AnnotationsSection's — otherwise a feature just
                // predicted for still shows "(unannotated)" here until
                // the window is closed and reopened.
                predictWindow.features = AnalysisBridge.getFeaturesForPrediction(dbPath)
                predictWindow.predictionFinished()
            }
        }
        function onFormulaPredictionFailed(dbPath, message) {
            if (dbPath === predictWindow.analysisDbPath) {
                predictWindow.predicting = false
                predictWindow.errorLabelText = message
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        GroupBox {
            title: "Settings"
            Layout.fillWidth: true

            ColumnLayout {
                anchors.fill: parent
                spacing: 6

                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "Polarity:"; Layout.preferredWidth: 100 }
                    ComboBox {
                        id: polarityCombo
                        objectName: "predictPolarityCombo"
                        model: ["positive", "negative"]
                        currentIndex: 0
                        // The two adduct label sets are disjoint, so a
                        // selection made under the old polarity is never
                        // valid under the new one — same reasoning as the
                        // New Analysis target-list tab.
                        onCurrentTextChanged: {
                            predictWindow.polarity = currentText
                            predictWindow.selectedAdducts = []
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "Adducts:"; Layout.preferredWidth: 100 }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 4
                        Repeater {
                            model: predictWindow.polarity === "negative"
                                   ? ConfigSchema.negativeAdducts : ConfigSchema.positiveAdducts
                            delegate: CheckBox {
                                objectName: "predictAdduct_" + modelData
                                text: modelData
                                checked: predictWindow.selectedAdducts.indexOf(modelData) !== -1
                                onToggled: predictWindow.toggleAdduct(modelData, checked)

                                HoverHandler {
                                    cursorShape: Qt.PointingHandCursor
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "Top N:" }
                    TextField {
                        id: topNField
                        objectName: "predictTopNField"
                        text: String(predictWindow.topN)
                        Layout.preferredWidth: 60
                    }
                    Label { text: "Error (ppm):" }
                    TextField {
                        id: errorPpmField
                        objectName: "predictErrorPpmField"
                        text: String(predictWindow.errorPpm)
                        Layout.preferredWidth: 60
                    }
                    CheckBox {
                        id: halogenCheckBox
                        objectName: "predictHalogenCheckBox"
                        text: "Allow halogens (F/Cl/Br/I)"
                        checked: predictWindow.halogen
                        onToggled: predictWindow.halogen = checked

                        HoverHandler {
                            cursorShape: Qt.PointingHandCursor
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Label {
                objectName: "predictSelectedCountLabel"
                text: "Features (" + predictWindow.selectedFeatureIds.length
                      + " of " + predictWindow.features.length + " selected)"
                font.bold: true
                Layout.fillWidth: true
            }
            Button {
                objectName: "predictSelectAllUnannotatedButton"
                text: predictWindow.allUnannotatedSelected
                      ? "Deselect all unannotated" : "Select all unannotated"
                enabled: predictWindow.unannotatedFeatureIds.length > 0
                onClicked: predictWindow.toggleSelectAllUnannotated()

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        ScrollView {
            objectName: "predictFeatureScrollView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: predictWindow.width - 40
                spacing: 0

                Repeater {
                    model: predictWindow.features
                    delegate: RowLayout {
                        objectName: "predictFeatureRow_" + modelData.feature_id
                        Layout.fillWidth: true

                        CheckBox {
                            objectName: "predictFeatureCheck_" + modelData.feature_id
                            checked: predictWindow.selectedFeatureIds.indexOf(modelData.feature_id) !== -1
                            onToggled: predictWindow.toggleFeature(modelData.feature_id, checked)

                            HoverHandler {
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        Label {
                            text: Number(modelData.mz).toFixed(4)
                            Layout.preferredWidth: 90
                        }
                        Label {
                            text: modelData.compound_name || "(unannotated)"
                            color: modelData.compound_name ? Theme.textColor : Theme.mutedTextColor
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Label {
                            text: (modelData.best_score === null || modelData.best_score === undefined)
                                  ? "" : Number(modelData.best_score).toFixed(3)
                            Layout.preferredWidth: 70
                        }
                    }
                }
            }
        }

        Label {
            id: errorLabel
            objectName: "predictErrorLabel"
            visible: text !== ""
            text: predictWindow.errorLabelText
            color: "#900"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button {
                objectName: "predictCloseButton"
                text: "Close"
                enabled: !predictWindow.predicting
                onClicked: predictWindow.visible = false

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
            Button {
                objectName: "predictRunButton"
                text: "Run"
                enabled: predictWindow.selectedFeatureIds.length > 0
                         && predictWindow.selectedAdducts.length > 0
                         && !predictWindow.predicting
                onClicked: {
                    predictWindow.errorLabelText = ""
                    predictWindow.predicting = true
                    AnalysisBridge.predictFormulas(
                        predictWindow.analysisDbPath,
                        predictWindow.selectedFeatureIds,
                        {
                            "adducts": predictWindow.selectedAdducts,
                            "error_ppm": parseFloat(errorPpmField.text),
                            "top_n": parseInt(topNField.text, 10),
                            "halogen": predictWindow.halogen
                        })
                }

                HoverHandler {
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }
    }

    // Blocks interaction with this window while the background thread
    // runs — the first invocation on a machine can mean a real,
    // multi-second ~420MB download (see ADR 27), not a frozen app.
    LoadingOverlay {
        objectName: "predictLoadingOverlay"
        anchors.fill: parent
        visible: predictWindow.predicting
    }
}

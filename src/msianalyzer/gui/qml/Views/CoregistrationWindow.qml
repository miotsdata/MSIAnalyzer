import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Dialogs
import "qrc:/Style"

// H&E/brightfield image coregistration: attach a microscopy image to a
// sample's RAW database (see core/registration/), then click matching
// landmark pairs against that sample's MSI heatmap to fit a transform
// mapping one onto the other (see ADR 45/46). A separate top-level
// Window, Loader-spawned from Visual Inspection's "Coregister H&E Image"
// button — same singleton-popup shape as RoiDesignWindow (one Loader, one
// openFor() entry point, see developer/architecture/gui.md).
//
// Landmark placement: tap a point on either pane; the next tap on the
// OTHER pane completes that pair. Tapping the same pane again before
// pairing just moves the pending point (cheap "I misclicked" recovery
// with no separate undo step needed for that case).
Window {
    id: win
    objectName: "coregistrationWindow"
    title: "H&E Coregistration" + (win.selectedSample ? " — " + win.selectedSample.name : "")
    // Same "starts hidden" reasoning as RoiDesignWindow.qml — a Loader-
    // constructed Window defaulting to visible would pop up unasked and
    // throw off layout measurements under the offscreen QPA tests use.
    visible: false
    width: 1200
    height: 760
    minimumWidth: 900
    minimumHeight: 600

    // Same palette-forwarding block as RoiDesignWindow.qml/
    // MirrorPlotDetailWindow.qml — a separate top-level Window doesn't
    // inherit Main.qml's palette override.
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

    readonly property bool useNativeDialogs: Qt.platform.pluginName !== "offscreen"

    property var analysis: null
    property var samples: []
    // The HeatmapControlsPanel instance Visual Inspection is already
    // showing — same reuse as RoiDesignWindow.controls: this window
    // renders the MSI-side pane with whatever feature/obs/colormap is
    // already selected there, no duplicate controls of its own.
    property var controls: null

    property int selectedSampleIndex: 0
    readonly property var selectedSample: (win.samples.length > win.selectedSampleIndex)
                                           ? win.samples[win.selectedSampleIndex] : null

    function _emptyRegistrationInfo() {
        return {
            hasImage: false, width: 0, height: 0, format: "",
            hasFit: false, transformType: "", rmse: 0.0, landmarks: [],
        }
    }
    property var registrationInfo: win._emptyRegistrationInfo()
    // Bumped after a successful attach, and folded into the H&E pane's
    // image:// source id purely as a cache-buster — an unchanged
    // Image.source string never re-requests, so replacing an image on
    // the SAME sample while this window stays open would otherwise keep
    // showing the old one.
    property int heImageRevision: 0

    // "affine" or "similarity" — see core/registration/transform.py.
    property string transformType: "affine"
    readonly property int minLandmarks: win.transformType === "similarity" ? 2 : 3
    readonly property bool canFit: win.landmarks.length >= win.minLandmarks

    // The working draft: [{heX, heY, gridX, gridY}, ...]. Seeded from any
    // already-saved registration on open/sample-switch (see
    // refreshRegistrationInfo) so re-fitting with a different transform
    // type, or adding a few more points, doesn't require re-clicking
    // everything from scratch.
    property var landmarks: []
    // Per-landmark reprojection error from the last successful fit,
    // parallel to `landmarks` — cleared whenever the draft changes so a
    // stale error is never shown against a since-edited point.
    property var landmarkErrors: []

    // A single point clicked on one pane, awaiting its pair on the other
    // — "" (neither), "he", or "grid".
    property string pendingSource: ""
    property var pendingPoint: null

    property string attachError: ""
    property string saveError: ""
    property string saveStatus: ""

    function resetDraftState() {
        win.pendingSource = ""
        win.pendingPoint = null
        win.attachError = ""
        win.saveError = ""
        win.saveStatus = ""
    }

    function refreshRegistrationInfo() {
        var sample = (win.samples.length > win.selectedSampleIndex)
                     ? win.samples[win.selectedSampleIndex] : null
        if (win.analysis && win.analysis.analysisDbPath && sample) {
            win.registrationInfo = AnalysisBridge.getRegistrationInfo(
                win.analysis.analysisDbPath, sample.name)
        } else {
            win.registrationInfo = win._emptyRegistrationInfo()
        }
        win.landmarks = (win.registrationInfo.landmarks || []).map(function (lm) {
            return {heX: lm.heX, heY: lm.heY, gridX: lm.gridX, gridY: lm.gridY}
        })
        win.landmarkErrors = (win.registrationInfo.landmarks || []).map(function (lm) {
            return lm.error
        })
        if (win.registrationInfo.transformType) {
            win.transformType = win.registrationInfo.transformType
        }
    }

    onSelectedSampleIndexChanged: {
        win.resetDraftState()
        win.refreshRegistrationInfo()
    }
    onVisibleChanged: {
        if (win.visible) {
            win.selectedSampleIndex = 0
            win.resetDraftState()
            win.refreshRegistrationInfo()
        }
    }

    // Single entry point, reused across "Coregister H&E Image" clicks —
    // same shape/reasoning as RoiDesignWindow.openFor.
    function openFor(analysisModel, sampleList, controlsPanel) {
        var wasVisible = win.visible
        win.analysis = analysisModel
        win.samples = sampleList
        win.controls = controlsPanel
        win.visible = true
        win.raise()
        win.requestActivate()
        if (wasVisible) {
            win.selectedSampleIndex = 0
            win.resetDraftState()
            win.refreshRegistrationInfo()
        }
    }

    function attachImage(localPath) {
        if (!win.analysis || !win.selectedSample || !localPath) return
        var result = AnalysisBridge.attachHeImage(
            win.analysis.analysisDbPath, win.selectedSample.name, localPath)
        if (result.ok) {
            win.heImageRevision += 1
            win.attachError = ""
            win.refreshRegistrationInfo()
        } else {
            win.attachError = result.error
        }
    }

    function addLandmark(heX, heY, gridX, gridY) {
        win.landmarks = win.landmarks.concat([{heX: heX, heY: heY, gridX: gridX, gridY: gridY}])
        win.landmarkErrors = []
        win.saveStatus = ""
    }
    function removeLandmark(index) {
        var next = win.landmarks.slice()
        next.splice(index, 1)
        win.landmarks = next
        win.landmarkErrors = []
        win.saveStatus = ""
    }
    function undoLastLandmark() {
        if (win.landmarks.length === 0) return
        win.removeLandmark(win.landmarks.length - 1)
    }
    function clearPending() {
        win.pendingSource = ""
        win.pendingPoint = null
    }

    // Shared handler for a tap on either pane — `side` is "he" or "grid".
    function handleTap(side, x, y) {
        if (win.pendingSource === "" || win.pendingSource === side) {
            // Nothing pending yet, or re-tapping the same pane before
            // pairing (moves the pending point rather than starting a
            // stray cross-pane pair).
            win.pendingSource = side
            win.pendingPoint = {x: x, y: y}
            return
        }
        var hePoint = side === "he" ? {x: x, y: y} : win.pendingPoint
        var gridPoint = side === "grid" ? {x: x, y: y} : win.pendingPoint
        win.addLandmark(hePoint.x, hePoint.y, gridPoint.x, gridPoint.y)
        win.clearPending()
    }

    function fitAndSave() {
        if (!win.canFit || !win.analysis || !win.selectedSample) return
        var result = AnalysisBridge.saveRegistration(
            win.analysis.analysisDbPath, win.selectedSample.name,
            win.landmarks, win.transformType)
        if (result.ok) {
            win.saveError = ""
            win.saveStatus = "Saved — RMSE " + result.rmse.toFixed(3) + " grid units"
            win.landmarks = result.landmarks.map(function (lm) {
                return {heX: lm.heX, heY: lm.heY, gridX: lm.gridX, gridY: lm.gridY}
            })
            win.landmarkErrors = result.landmarks.map(function (lm) { return lm.error })
        } else {
            win.saveStatus = ""
            win.saveError = result.error
        }
    }

    FileDialog {
        id: attachImageDialog
        objectName: "attachImageDialog"
        options: win.useNativeDialogs ? 0 : FileDialog.DontUseNativeDialog
        nameFilters: ["Images (*.png *.jpg *.jpeg *.tif *.tiff)", "All files (*)"]
        onAccepted: win.attachImage(Router.toLocalPath(selectedFile))
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Label { text: "Sample:" }
            ComboBox {
                id: sampleCombo
                objectName: "coregSampleCombo"
                Layout.fillWidth: true
                Layout.maximumWidth: 220
                model: win.samples.map(function (s) { return s.name })
                currentIndex: win.selectedSampleIndex
                onActivated: (index) => win.selectedSampleIndex = index
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            Button {
                id: attachImageButton
                objectName: "attachImageButton"
                text: win.registrationInfo.hasImage ? "Replace Image..." : "Attach Image..."
                onClicked: attachImageDialog.open()
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
            Label {
                objectName: "attachedImageStatusLabel"
                text: win.registrationInfo.hasImage
                      ? (win.registrationInfo.format + " " + win.registrationInfo.width
                         + "x" + win.registrationInfo.height)
                      : "No image attached"
                color: Theme.mutedTextColor
            }
            Item { Layout.fillWidth: true }
        }

        Label {
            objectName: "coregInstructionsLabel"
            visible: win.registrationInfo.hasImage
            text: "Click a point on one image, then the matching point on the "
                  + "other, to place a landmark pair. Needs at least "
                  + win.minLandmarks + " pairs for a " + win.transformType + " fit."
            color: Theme.mutedTextColor
            font.pixelSize: Theme.captionPixelSize
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            Layout.preferredWidth: 1
        }

        Label {
            objectName: "attachErrorLabel"
            visible: win.attachError.length > 0
            text: win.attachError
            color: Theme.errorColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            Layout.preferredWidth: 1
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 4

                Label { text: "H&E / Brightfield"; font.bold: true }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    ZoomableImage {
                        id: heZoomImage
                        objectName: "heZoomableImage"
                        anchors.fill: parent
                        source: (win.selectedSample && win.registrationInfo.hasImage)
                                ? "image://he_image/" + win.selectedSample.name
                                  + "|" + win.heImageRevision
                                : ""

                        LandmarkOverlay {
                            id: heOverlay
                            objectName: "heLandmarkOverlay"
                            anchors.fill: parent
                            effectiveScale: parent.effectiveScale
                            points: win.landmarks.map(function (lm, i) {
                                return {x: lm.heX, y: lm.heY, index: i}
                            })
                            pendingPoint: win.pendingSource === "he" ? win.pendingPoint : null
                        }

                        TapHandler {
                            objectName: "heTapHandler"
                            enabled: win.registrationInfo.hasImage
                            onTapped: (eventPoint) => {
                                win.handleTap(
                                    "he",
                                    eventPoint.position.x / heZoomImage.effectiveScale,
                                    eventPoint.position.y / heZoomImage.effectiveScale)
                            }
                        }
                    }

                    Label {
                        objectName: "noImageLabel"
                        visible: !win.registrationInfo.hasImage
                        anchors.centerIn: parent
                        text: "No image attached.\nClick “Attach Image…” to begin."
                        horizontalAlignment: Text.AlignHCenter
                        color: Theme.mutedTextColor
                    }
                }

                RowLayout {
                    Label { text: "Zoom:" }
                    Button {
                        objectName: "heZoomOutButton"
                        text: "−"; implicitWidth: 32
                        onClicked: heZoomImage.zoom /= 1.3
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    Button {
                        objectName: "heZoomResetButton"
                        text: "Reset"
                        onClicked: heZoomImage.zoom = 1.0
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    Button {
                        objectName: "heZoomInButton"
                        text: "+"; implicitWidth: 32
                        onClicked: heZoomImage.zoom *= 1.3
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 4

                Label { text: "MSI Heatmap"; font.bold: true }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    // A Flickable (ZoomableImage) needs to sit inside a
                    // plain Item that carries the Layout.fillWidth/
                    // fillHeight, with anchors.fill on the Flickable
                    // itself — not Layout.fillWidth/fillHeight directly
                    // on the Flickable. Putting them directly on it
                    // leaves it geometrically correctly sized (confirmed:
                    // width/height report right) but its TapHandler never
                    // receives press events at all under this app's
                    // QtQuick.Layouts + Flickable combination — same
                    // wrapping RoiDrawingCanvas.qml's root Item already
                    // uses for its own ZoomableImage, and the H&E pane
                    // above.
                    ZoomableImage {
                        id: gridZoomImage
                        objectName: "gridZoomableImage"
                        anchors.fill: parent
                        source: (win.selectedSample && win.controls)
                                ? win.controls.tileSource(win.selectedSample.name) : ""

                        LandmarkOverlay {
                            id: gridOverlay
                            objectName: "gridLandmarkOverlay"
                            anchors.fill: parent
                            effectiveScale: parent.effectiveScale
                            points: win.landmarks.map(function (lm, i) {
                                return {x: lm.gridX, y: lm.gridY, index: i}
                            })
                            pendingPoint: win.pendingSource === "grid" ? win.pendingPoint : null
                        }

                        TapHandler {
                            objectName: "gridTapHandler"
                            enabled: win.selectedSample !== null
                            onTapped: (eventPoint) => {
                                win.handleTap(
                                    "grid",
                                    eventPoint.position.x / gridZoomImage.effectiveScale,
                                    eventPoint.position.y / gridZoomImage.effectiveScale)
                            }
                        }
                    }
                }

                RowLayout {
                    Label { text: "Zoom:" }
                    Button {
                        objectName: "gridZoomOutButton"
                        text: "−"; implicitWidth: 32
                        onClicked: gridZoomImage.zoom /= 1.3
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    Button {
                        objectName: "gridZoomResetButton"
                        text: "Reset"
                        onClicked: gridZoomImage.zoom = 1.0
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    Button {
                        objectName: "gridZoomInButton"
                        text: "+"; implicitWidth: 32
                        onClicked: gridZoomImage.zoom *= 1.3
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
            }

            ColumnLayout {
                id: landmarkPanel
                objectName: "landmarkPanel"
                Layout.preferredWidth: 260
                Layout.minimumWidth: 260
                Layout.maximumWidth: 260
                Layout.fillHeight: true

                Label { text: "Transform:" }
                ComboBox {
                    id: transformTypeCombo
                    objectName: "transformTypeCombo"
                    Layout.fillWidth: true
                    model: ["affine", "similarity"]
                    currentIndex: model.indexOf(win.transformType)
                    onActivated: (index) => {
                        win.transformType = model[index]
                        win.saveStatus = ""
                    }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }

                Label { text: "Landmarks"; font.bold: true }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    ColumnLayout {
                        width: landmarkPanel.width
                        spacing: 2

                        Repeater {
                            id: landmarkListRepeater
                            objectName: "landmarkListRepeater"
                            model: win.landmarks

                            delegate: RowLayout {
                                objectName: "landmarkRow_" + index
                                Layout.fillWidth: true
                                Rectangle {
                                    width: 10; height: 10; radius: 5
                                    color: heOverlay._colorFor(index)
                                }
                                Label {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    font.pixelSize: Theme.captionPixelSize
                                    text: "#" + (index + 1)
                                          + (win.landmarkErrors[index] !== undefined
                                             ? " (err " + win.landmarkErrors[index].toFixed(2) + ")"
                                             : "")
                                }
                                ToolButton {
                                    objectName: "landmarkDeleteButton_" + index
                                    text: "✕"
                                    onClicked: win.removeLandmark(index)
                                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Button {
                        objectName: "landmarkUndoButton"
                        text: "Undo"
                        enabled: win.landmarks.length > 0
                        onClicked: win.undoLastLandmark()
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    Button {
                        objectName: "landmarkClearPendingButton"
                        text: "Clear pending"
                        enabled: win.pendingSource.length > 0
                        onClicked: win.clearPending()
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }

                Button {
                    id: fitAndSaveButton
                    objectName: "fitAndSaveButton"
                    text: "Fit && Save Registration"
                    Layout.fillWidth: true
                    enabled: win.canFit
                    onClicked: win.fitAndSave()
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }

                Label {
                    objectName: "saveStatusLabel"
                    visible: win.saveStatus.length > 0
                    text: win.saveStatus
                    color: Theme.textColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                }
                Label {
                    objectName: "saveErrorLabel"
                    visible: win.saveError.length > 0
                    text: win.saveError
                    color: Theme.errorColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                }

                Item { Layout.fillHeight: true }
            }
        }
    }
}

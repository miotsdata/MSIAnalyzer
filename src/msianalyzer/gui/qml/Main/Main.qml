import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

ApplicationWindow {
    id: window

    visible: true
    // Bigger than the old 900x650 default — several sections (the 13-tab
    // config Flow, Visual Inspection's control panel + heatmap grid) need
    // real room; minimumWidth/Height stop the window being resized below
    // where that content stops fitting at all.
    width: 1280
    height: 850
    minimumWidth: 1000
    minimumHeight: 700
    title: "MSIAnalyzer"

    // Explicit light palette, not just "Fusion instead of Material" —
    // Fusion (like Basic) still follows the host desktop's own palette
    // via GTK platform-theme integration, so it stayed dark on a system
    // with GNOME's dark theme on, same as the very first report. `palette`
    // is a QQuickItem-inherited property (not style-specific — Fusion,
    // Basic, Material, Universal all read from it), so setting it once
    // here cascades to every control in the app regardless of the host
    // theme, without going back to Material (whose native-FileDialog
    // combination froze the app).
    palette {
        window: "#f5f5f5"
        windowText: "#202020"
        base: "#ffffff"
        alternateBase: "#eeeeee"
        text: "#202020"
        button: "#e6e6e6"
        buttonText: "#202020"
        toolTipBase: "#ffffe1"
        toolTipText: "#202020"
        placeholderText: "#808080"
        highlight: "#0078d4"
        highlightedText: "#ffffff"
        light: "#ffffff"
        midlight: "#f0f0f0"
        mid: "#c0c0c0"
        dark: "#a0a0a0"
        shadow: "#000000"
    }

    StackView {
        id: stackView
        objectName: "stackView"
        anchors.fill: parent

        initialItem: "qrc:/Views/StartPage.qml"
    }

    Connections {
        target: Router
        function onShowProjectHomeRequested(project) {
            stackView.push("qrc:/Views/ProjectHomePage.qml", {"project": project})
        }

        function onCreateProjectPageRequested() {
            stackView.push("qrc:/Views/CreateProjectPage.qml")
        }

        function onNewAnalysisPageRequested(project) {
            stackView.push("qrc:/Views/NewAnalysisPage.qml", {"project": project})
        }

        function onShowRunningPageRequested(project, runId) {
            stackView.push("qrc:/Views/RunningAnalysisPage.qml", {"project": project, "runId": runId})
        }

        function onShowAnalysisRequested(analysis) {
            stackView.push("qrc:/Views/AnalysisPage.qml", {"analysis": analysis})
        }

        function onShowErrorRequested(message) {
            errorDialog.text = message
            errorDialog.open()
        }
    }

    MessageDialog {
        id: errorDialog
        objectName: "errorDialog"
        buttons: MessageDialog.Ok
        modality: Qt.ApplicationModal
    }
}

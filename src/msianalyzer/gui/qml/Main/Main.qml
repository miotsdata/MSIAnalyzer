import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import "qrc:/Style"

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

    // ApplicationWindow's own background fill is `color`, a separate
    // property from `palette` below (which only governs how *controls*
    // render themselves) — left unset, it stayed whatever the platform's
    // own default surface color is regardless of the palette override,
    // which is the most likely reason the window still looked dark after
    // that alone.
    color: palette.window

    // Professional dark-neutral palette — replaces the earlier flat light
    // palette. `palette` is a QQuickItem-inherited property (not
    // style-specific — Fusion, Basic, Material, Universal all read from
    // it), so setting it once here cascades to every control in the app
    // regardless of the host theme; see the comment this replaced for why
    // an explicit palette is needed at all (Fusion otherwise follows the
    // host desktop's GTK theme).
    //
    // Deliberately mid-dark neutral gray (VS Code/Adobe-panel territory,
    // #2d2d30 window / #252526 base), not the near-black tried earlier —
    // that one was reported "too dark". `base`/`alternateBase` sit a shade
    // darker than `window` so input fields and table rows read as
    // recessed against the surrounding chrome, and `light`/`midlight`/
    // `mid`/`dark` step in even increments so borders and pressed/hover
    // states have real visual depth instead of everything sitting at the
    // same flat gray. `highlight` is a restrained blue rather than an
    // Adobe-brand color — this app isn't Adobe-branded, just aiming for
    // the same "serious creative/analysis tool" register.
    //
    // window/alternateBase/highlight read from Style/Theme.qml's
    // backgroundColor/secondaryColor/primaryColor — those three are the
    // ones anything outside this block also needs to reference by name
    // (e.g. a card background), so Theme is their source of truth, not
    // this block. Every other role below has no outside consumer yet, so
    // it's still just a literal here.
    palette {
        window: Theme.backgroundColor
        windowText: "#e0e0e0"
        base: "#252526"
        alternateBase: Theme.secondaryColor
        text: "#e0e0e0"
        button: "#3c3c3c"
        buttonText: "#e0e0e0"
        toolTipBase: "#3c3c3c"
        toolTipText: "#e0e0e0"
        placeholderText: "#8a8a8a"
        highlight: Theme.primaryColor
        highlightedText: "#ffffff"
        light: "#4a4a4a"
        midlight: "#414141"
        mid: "#2a2a2a"
        dark: "#1e1e1e"
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

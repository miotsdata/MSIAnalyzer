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

    // The project currently open, tracked here (not just passed
    // page-to-page as a property) so the menu bar — visible on every
    // page — knows whether a project is open at all, and can read its
    // `runsList` for the "Analyses > Open" submenu, regardless of which
    // page happens to be on top of `stackView` right now.
    property var currentProject: null

    // The Nth-most-recent run of the current project, or null — used to
    // fill the "Analyses > Open" submenu's fixed 5 slots. A plain
    // function over a Repeater: this codebase's test harness has a
    // confirmed fragility with Repeater models that are arrays of plain
    // objects (see Style/Theme.qml/gui-workspace-status memory), and 5 is
    // a small, fixed count anyway.
    function recentRun(index) {
        if (!currentProject)
            return null
        var runs = currentProject.runsList
        return index < runs.length ? runs[index] : null
    }

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
    // Every role below reads from Style/Theme.qml — this block itself no
    // longer carries a single literal color (2026-09-15; it used to, for
    // every role beyond window/alternateBase/highlight). Theme is now the
    // one place the app's whole color scheme lives, which is also what
    // makes a future light/dark toggle cheap: it becomes a property on
    // Theme, this block never needs to change again. See Theme.qml's own
    // comments for what each token means and the exact hex values.
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

        // The "Disabled" QPalette group — left unset, Qt derives it from
        // the "Active" colors above using an algorithm tuned for a light
        // palette, which read poorly (too close to the enabled color, or
        // just wrong-looking) once the app went dark. Explicit here so a
        // disabled control (e.g. "Analyses" with no project open) reads
        // as clearly unavailable rather than merely a bit dimmer.
        disabled.windowText: Theme.disabledTextColor
        disabled.buttonText: Theme.disabledTextColor
        disabled.text: Theme.disabledTextColor
    }

    // In-window (not native/OS) menu bar — consistent across platforms,
    // and this app isn't macOS-only, where a native global menu bar would
    // otherwise be the more idiomatic choice.
    menuBar: MenuBar {
        objectName: "mainMenuBar"

        // A clear line under the whole bar, separating it from the page
        // content below — reported unclear otherwise ("menubar should be
        // clear... have maybe a line below it", 2026-09-14).
        background: Rectangle {
            color: palette.window
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: palette.mid
            }
        }

        // Each top-level entry (Project/Analyses/Help) gets its own
        // border too, same "clear" request — Fusion's default MenuBarItem
        // otherwise blends into the bar until hovered. Only `background`
        // is overridden; `contentItem` stays Fusion's own default, which
        // already renders `text` in the right enabled/disabled color once
        // the palette `disabled` group above is set.
        delegate: MenuBarItem {
            id: menuBarItem
            background: Rectangle {
                color: menuBarItem.highlighted ? palette.highlight : "transparent"
                border.color: palette.mid
                border.width: 1
            }
        }

        Menu {
            objectName: "projectMenu"
            title: "Project"

            AppMenuItem {
                objectName: "newProjectMenuItem"
                text: "New Project"
                onTriggered: Router.createProjectPageRequested()
            }
            AppMenuItem {
                objectName: "openProjectMenuItem"
                text: "Open Project…"
                onTriggered: openProjectDialog.open()
            }
            MenuSeparator {}
            AppMenuItem {
                objectName: "closeProjectMenuItem"
                text: "Close Project"
                enabled: window.currentProject !== null
                onTriggered: Router.closeProjectRequested()
            }
            MenuSeparator {}
            AppMenuItem {
                objectName: "exitMenuItem"
                text: "Exit"
                onTriggered: Qt.quit()
            }
        }

        Menu {
            objectName: "analysesMenu"
            title: "Analyses"
            // The whole top-level entry, not just its children — with no
            // project open there's nothing this menu can do at all, so it
            // shouldn't even open ("I actually want the entire menu item
            // disabled (also in color)", 2026-09-14).
            enabled: window.currentProject !== null

            AppMenuItem {
                objectName: "newAnalysisMenuItem"
                text: "New"
                enabled: window.currentProject !== null
                onTriggered: Router.newAnalysisPageRequested(window.currentProject)
            }
            Menu {
                objectName: "openAnalysisMenu"
                title: "Open"
                enabled: window.currentProject !== null
                         && window.currentProject.runsList.length > 0

                // A fixed 5 slots, not a Repeater over `runsList` — see
                // `recentRun`'s own comment for why. Each slot hides
                // itself once there's no Nth-most-recent run to show.
                AppMenuItem {
                    objectName: "openAnalysisMenuItem_0"
                    readonly property var run: window.recentRun(0)
                    visible: run !== null
                    text: run ? (run.start_date_display + "  —  " + run.out_dir_display) : ""
                    onTriggered: Router.analysisSelected(run.id)
                }
                AppMenuItem {
                    objectName: "openAnalysisMenuItem_1"
                    readonly property var run: window.recentRun(1)
                    visible: run !== null
                    text: run ? (run.start_date_display + "  —  " + run.out_dir_display) : ""
                    onTriggered: Router.analysisSelected(run.id)
                }
                AppMenuItem {
                    objectName: "openAnalysisMenuItem_2"
                    readonly property var run: window.recentRun(2)
                    visible: run !== null
                    text: run ? (run.start_date_display + "  —  " + run.out_dir_display) : ""
                    onTriggered: Router.analysisSelected(run.id)
                }
                AppMenuItem {
                    objectName: "openAnalysisMenuItem_3"
                    readonly property var run: window.recentRun(3)
                    visible: run !== null
                    text: run ? (run.start_date_display + "  —  " + run.out_dir_display) : ""
                    onTriggered: Router.analysisSelected(run.id)
                }
                AppMenuItem {
                    objectName: "openAnalysisMenuItem_4"
                    readonly property var run: window.recentRun(4)
                    visible: run !== null
                    text: run ? (run.start_date_display + "  —  " + run.out_dir_display) : ""
                    onTriggered: Router.analysisSelected(run.id)
                }
            }
        }

        Menu {
            objectName: "helpMenu"
            title: "Help"

            AppMenuItem {
                objectName: "aboutMenuItem"
                text: "About"
                onTriggered: aboutDialog.open()
            }
            AppMenuItem {
                objectName: "userGuideMenuItem"
                text: "User Guide"
                onTriggered: {
                    if (!Router.openUserGuide()) {
                        Router.showErrorRequested(
                            "User guide not found — build it first with `mkdocs build`.")
                    }
                }
            }
        }
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
            window.currentProject = project
            stackView.push("qrc:/Views/ProjectHomePage.qml", {"project": project})
        }

        function onCreateProjectPageRequested() {
            stackView.push("qrc:/Views/CreateProjectPage.qml")
        }

        function onNewAnalysisPageRequested(project) {
            window.currentProject = project
            stackView.push("qrc:/Views/NewAnalysisPage.qml", {"project": project})
        }

        function onShowRunningPageRequested(project, runId) {
            window.currentProject = project
            stackView.push("qrc:/Views/RunningAnalysisPage.qml", {"project": project, "runId": runId})
        }

        function onShowAnalysisRequested(analysis) {
            window.currentProject = analysis.project
            stackView.push("qrc:/Views/AnalysisPage.qml", {"analysis": analysis})
        }

        function onShowErrorRequested(message) {
            errorDialog.text = message
            errorDialog.open()
        }

        function onCloseProjectRequested() {
            window.currentProject = null
            stackView.push("qrc:/Views/StartPage.qml")
        }
    }

    MessageDialog {
        id: errorDialog
        objectName: "errorDialog"
        buttons: MessageDialog.Ok
        modality: Qt.ApplicationModal
    }

    Dialog {
        id: aboutDialog
        objectName: "aboutDialog"
        title: "About MSIAnalyzer"
        modal: true
        standardButtons: Dialog.Close
        anchors.centerIn: parent

        Column {
            spacing: 6
            Label {
                text: "MSIAnalyzer"
                font.bold: true
                font.pixelSize: Theme.headingPixelSize
            }
            Label {
                objectName: "aboutVersionLabel"
                text: "Version " + AppVersion
            }
        }
    }

    // Mirrors StartPage.qml's own "Load Project" FolderDialog — kept as a
    // separate instance rather than shared, since QML dialogs aren't
    // easily reused across two different declaring files, and this one
    // needs to be reachable from the menu bar regardless of which page
    // is currently on top of `stackView`.
    FolderDialog {
        id: openProjectDialog
        objectName: "openProjectDialog"
        options: Qt.platform.pluginName === "offscreen" ? FolderDialog.DontUseNativeDialog : 0
        onAccepted: Router.projectFolderChosen(Router.toLocalPath(selectedFolder))
    }
}

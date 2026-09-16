import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "qrc:/Style"

Page {
    id: analysisPage
    objectName: "analysisPage"

    property var analysis
    // Set by Annotations' "Inspect visually" right-click just before
    // switching to the Visual Inspection tab (see annotationsLoader.onLoaded
    // below) — a one-shot handoff, not persistent workspace state: cleared
    // back to NaN via visualLoader's pendingInspectHandled once applied, so
    // a later plain click on the Visual Inspection nav button doesn't
    // re-apply a stale feature selection from a previous "Inspect visually".
    property real pendingInspectMz: NaN

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Title, section nav and "back to project" all on one row — the
        // nav rail used to be a fixed 180px-wide left-hand column, taking
        // width away from every section's own content (which all need the
        // room). Moving it up here next to the title gives every section
        // the page's full width instead.
        RowLayout {
            objectName: "analysisPageHeader"
            Layout.fillWidth: true
            Layout.margins: 16
            spacing: 24

            ColumnLayout {
                id: analysisTitleBlock
                objectName: "analysisTitleBlock"
                spacing: 0

                Label {
                    objectName: "analysisPageTitle"
                    text: "Project: " + (analysis ? analysis.projectName : "")
                    font.pixelSize: 14
                    font.bold: true
                }
                Label {
                    objectName: "analysisPageRunId"
                    text: "Analysis: " + (analysis ? analysis.runId : "")
                    font.pixelSize: 11
                    color: Theme.mutedTextColor
                }
                Label {
                    objectName: "analysisPageDate"
                    text: "Date: " + (analysis ? analysis.startDateDisplay : "")
                    font.pixelSize: 11
                    color: Theme.mutedTextColor
                }
            }

            RowLayout {
                id: navRail
                objectName: "navRail"
                spacing: 4
                // "I don't want user to run back and forth while things
                // are loading" — also closes off a plausible trigger for
                // the rare crash found while testing the loading overlay
                // (see AnalysisPage.qml's own comment on it): nothing
                // stopped a click from landing on a nav button, or "Back
                // to project", while the *previous* tab's content
                // (MS1's WebEngineView especially) was still mid-load.
                enabled: analysisPage.currentSectionReady
                opacity: enabled ? 1.0 : 0.5

                Repeater {
                    id: navRepeater
                    objectName: "navRepeater"
                    model: ["Summary", "MS1 Spectra", "Annotations", "Visual Inspection"]

                    delegate: Button {
                        objectName: "navButton_" + index
                        text: modelData
                        checkable: true
                        checked: sectionStack.currentIndex === index
                        onClicked: sectionStack.currentIndex = index

                        // "the buttons in analyses (MS1, Annotation
                        // etc)... should make the user understand that
                        // they can be clickable" — Buttons don't get a
                        // pointing-hand cursor by default in Qt Quick
                        // Controls the way HTML buttons do.
                        HoverHandler {
                            objectName: "navButtonHover_" + index
                            cursorShape: Qt.PointingHandCursor
                        }
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Button {
                objectName: "backToProjectButton"
                text: "Back to project"
                enabled: analysisPage.currentSectionReady
                opacity: enabled ? 1.0 : 0.5
                onClicked: if (analysis) Router.showProjectHomeRequested(analysis.project)

                HoverHandler {
                    objectName: "backToProjectButtonHover"
                    cursorShape: Qt.PointingHandCursor
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: palette.mid
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 8

            StackLayout {
                id: sectionStack
                objectName: "sectionStack"
                currentIndex: 0
                anchors.fill: parent

                // Annotations/Visual Inspection are now lazy too (`active:
                // currentIndex === N`, matching MS1's existing pattern) —
                // opening the workspace used to eagerly construct *all
                // four* sections' full QML trees at once even though only
                // Summary was visible; now only the active tab's section
                // (plus Summary, the default) is ever constructed, cutting
                // the one-time cost of opening the workspace down to just
                // Summary's.
                //
                // Deliberately NOT `asynchronous: true` here, tempting as
                // it looks for smoothing over construction cost further:
                // tried it, and it reproducibly (~1 in 6 full-suite runs)
                // crashed with `Fatal Python error: Aborted`. Reverting it
                // made that SPECIFIC signature stop, but a low-frequency
                // crash (roughly 1 in 15-30 full-suite runs, vs. zero in
                // 16+ runs on the code before this change) still turned
                // up even with every Loader synchronous — its exact
                // trigger wasn't pinned down (didn't reproduce in any
                // standalone, non-pytest repro attempt; the one full
                // traceback caught landed in an unrelated later test,
                // pointing at a *deferred* effect rather than something
                // failing immediately). Left `asynchronous` off regardless
                // since it's one clearly-implicated variable removed at
                // no real cost — construction is small and
                // precompiled-QML-cache-backed already. See
                // [[gui-real-device-testing-workflow]]-style memory notes
                // for the fuller investigation if this resurfaces.
                Loader {
                    id: summaryLoader
                    objectName: "summarySectionLoader"
                    source: "qrc:/Views/SummarySection.qml"
                    // Qt.binding, not a one-time assignment: a plain
                    // `item.analysis = analysisPage.analysis` only runs once,
                    // right when this Loader finishes — for a page reached
                    // via StackView.push({"analysis": ...}), analysisPage's
                    // own `analysis` property can still be unset at that
                    // exact moment, permanently leaving the section's copy
                    // null.
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }

                Loader {
                    id: ms1Loader
                    objectName: "ms1SectionLoader"
                    // Lazy (unlike Summary): this section owns a
                    // WebEngineView, and StackLayout keeps every page as a
                    // permanent sibling rather than destroying hidden ones
                    // — an eagerly-loaded WebEngineView would sit in the
                    // tree even while another tab is active, and any
                    // childItems()-based search from `root` for that
                    // *other* tab's content would recurse into it looking
                    // for a non-match, which is unsafe (see find_visual_child).
                    active: sectionStack.currentIndex === 1
                    source: "qrc:/Views/MS1SpectraSection.qml"
                    onLoaded: item.analysis = Qt.binding(function () { return analysisPage.analysis })
                }
                Loader {
                    id: annotationsLoader
                    objectName: "annotationsSectionLoader"
                    active: sectionStack.currentIndex === 2
                    source: "qrc:/Views/AnnotationsSection.qml"
                    onLoaded: {
                        item.analysis = Qt.binding(function () { return analysisPage.analysis })
                        // "Inspect visually" — set the handoff before
                        // switching tabs; visualLoader below picks it up
                        // via its own pendingInspectMz binding once
                        // sectionStack.currentIndex flips its `active` on.
                        item.inspectVisuallyRequested.connect(function (mz) {
                            analysisPage.pendingInspectMz = mz
                            sectionStack.currentIndex = 3
                        })
                    }
                }
                Loader {
                    id: visualLoader
                    objectName: "visualSectionLoader"
                    active: sectionStack.currentIndex === 3
                    source: "qrc:/Views/VisualInspectionSection.qml"
                    onLoaded: {
                        item.analysis = Qt.binding(function () { return analysisPage.analysis })
                        // Connected before pendingInspectMz is assigned
                        // below, not after — assigning a binding evaluates
                        // it immediately, and (when a request is actually
                        // pending) that synchronously emits
                        // pendingInspectHandled from inside
                        // VisualInspectionSection's own onPendingInspectMzChanged
                        // before this function would otherwise get back to
                        // its next line — connecting first ensures this
                        // handler already exists to catch it.
                        item.pendingInspectHandled.connect(function () {
                            analysisPage.pendingInspectMz = NaN
                        })
                        item.pendingInspectMz = Qt.binding(function () { return analysisPage.pendingInspectMz })
                    }
                }
            }

            // "whenever I change a page there is a small delay/page
            // construction step that is ugly. Can we have like a loading
            // element... and when the whole page is ready (plots
            // included) it is displayed?" — covers the currently-active
            // tab's own Loader (incrementally constructing, see above)
            // and, for MS1 specifically, the spectrum plot's own load
            // ("plots included").
            LoadingOverlay {
                id: sectionLoadingOverlay
                objectName: "sectionLoadingOverlay"
                anchors.fill: parent
                visible: !analysisPage.currentSectionReady
            }
        }
    }

    readonly property bool currentSectionReady: {
        switch (sectionStack.currentIndex) {
        case 0: return summaryLoader.status === Loader.Ready
        case 1: return ms1Loader.status === Loader.Ready
                       && ms1Loader.item && !ms1Loader.item.plotLoading
        case 2: return annotationsLoader.status === Loader.Ready
        case 3: return visualLoader.status === Loader.Ready
        default: return true
        }
    }
}

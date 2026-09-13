from pathlib import Path

from PySide6.QtCore import QObject, Signal, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication


class Router(QObject):
    createProjectPageRequested = Signal()
    createProjectRequested = Signal(str, str)
    projectFolderChosen = Signal(str)
    showProjectHomeRequested = Signal(QObject)
    showErrorRequested = Signal(str)
    newAnalysisPageRequested = Signal(QObject)
    analysisSelected = Signal(str)
    runAnalysisRequested = Signal(QObject, dict)
    showRunningPageRequested = Signal(QObject, str)
    showAnalysisRequested = Signal(QObject)
    # Menu bar > Project > Close Project. `Application` resets its own
    # project/project_model/project_folder state on this; Main.qml's own
    # Connections handler does the actual navigation back to StartPage.
    closeProjectRequested = Signal()

    @Slot(QUrl, result=str)
    def toLocalPath(self, url: QUrl) -> str:
        return url.toLocalFile()

    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        """Copies `text` to the system clipboard — e.g. Project Home's
        "Copy path" context-menu action, for the full absolute path behind
        a project-relative display string."""
        QGuiApplication.clipboard().setText(text)

    @Slot(result=bool)
    def openUserGuide(self) -> bool:
        """Menu bar > Help > User Guide — opens the built mkdocs site
        (`mkdocs build`'s default `site/` output, at the repo root) in the
        system's default browser.

        Assumes a dev/repo-checkout deployment — this app isn't packaged/
        distributed to end users yet, so there's no installed-package
        location to resolve docs from. Revisit (a bundled PDF, or a
        hosted docs URL opened directly) once that's actually decided.

        Returns:
            Whether `site/index.html` exists and was handed off to the
            OS successfully. `False` means the caller should tell the
            user the docs haven't been built (`mkdocs build`) rather than
            silently doing nothing.
        """
        index_html = Path(__file__).resolve().parents[4] / "site" / "index.html"
        if not index_html.exists():
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(index_html)))

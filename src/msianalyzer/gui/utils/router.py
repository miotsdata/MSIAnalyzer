from PySide6.QtCore import QObject, Signal, QUrl, Slot
from PySide6.QtGui import QGuiApplication


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

    @Slot(QUrl, result=str)
    def toLocalPath(self, url: QUrl) -> str:
        return url.toLocalFile()

    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        """Copies `text` to the system clipboard — e.g. Project Home's
        "Copy path" context-menu action, for the full absolute path behind
        a project-relative display string."""
        QGuiApplication.clipboard().setText(text)

from PySide6.QtCore import QObject, Signal, QUrl, Slot


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

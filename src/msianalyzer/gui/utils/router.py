from PySide6.QtCore import QObject, Signal


class Router(QObject):
    createProjectPageRequested = Signal()
    createProjectRequested = Signal(str, str)
    projectFolderChosen = Signal(str)
    showProjectHomeRequested = Signal(QObject)

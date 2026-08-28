from PySide6.QtCore import QObject, Signal


class Router(QObject):
    createProjectRequested = Signal()
    projectFolderChosen = Signal(str)

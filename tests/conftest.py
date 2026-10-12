import multiprocessing


def pytest_configure(config):
    # Same start method as the GUI (gui/main.py): the test process runs Qt
    # and logging threads, and forking it can deadlock pool workers.
    multiprocessing.set_start_method("spawn", force=True)

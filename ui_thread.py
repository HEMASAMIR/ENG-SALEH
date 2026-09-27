import sys
from threading import Thread
from ui.main import BlueSquareApp

class UIThread(Thread):
    def __init__(self, plc_comm=None):
        super().__init__()
        self.app = None
        self.plc_comm = plc_comm

    def run(self):
        print("[UIThread] Starting Blue Square UI...")
        self.app = BlueSquareApp(self.plc_comm)
        self.app.run()

    def stop(self):
        # PySide/Qt application exit is usually handled by the main thread or when window is closed.
        # However, for a clean shutdown from main.py, we might need to tell the app to quit.
        if self.app and self.app.app:
            self.app.app.quit()

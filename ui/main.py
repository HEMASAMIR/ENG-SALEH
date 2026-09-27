"""
BLUE SQUARE — Application Entry Point
Initializes QApplication, loads global QSS, and manages Login → MainWindow flow.
"""
import sys
import os

# Ensure project root is on the path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PySide6.QtWidgets import QApplication, QStackedWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.login_page import LoginPage
from ui.main_window import MainWindow
from core.database import get_session
from services.backup_service import BackupService
from PySide6.QtWidgets import QMessageBox


class BlueSquareApp:
    """
    Application controller that manages the transition between
    the Login page and the Main Window.
    """

    def __init__(self, plc_comm=None):
        self.app = QApplication(sys.argv)
        self.plc_comm = plc_comm
        self.app.setApplicationName("BLUE SQUARE - CMS")
        self.app.setApplicationDisplayName("BLUE SQUARE - CMS — Industrial Inspection System")
        self.app.aboutToQuit.connect(self._on_app_about_to_quit)

        # Set default font
        font = QFont("Segoe UI", 11)
        self.app.setFont(font)

        # Load global QSS
        self._load_global_qss()

        # Root container
        self.container = QStackedWidget()
        self.container.setWindowTitle("BLUE SQUARE - CMS — Industrial Inspection System")
        self.container.setMinimumSize(1280, 800)

        # Login page
        self.login_page = LoginPage()
        self.login_page.login_success.connect(self._on_login_success)
        self.container.addWidget(self.login_page)

        # Main window placeholder (created on login)
        self.main_window = None

        self.container.setCurrentIndex(0)

    def _load_global_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'global.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.app.setStyleSheet(f.read())

    def _on_login_success(self, session_id, user_id: int, user_name: str, role_name: str, role_id: int):
        """Handle successful login: create MainWindow and switch to it."""
        # Remove old main window if exists
        if self.main_window:
            self.container.removeWidget(self.main_window)
            self.main_window.deleteLater()

        self.main_window = MainWindow(session_id, user_id, user_name, role_name, role_id=role_id, plc_comm=self.plc_comm)
        self.main_window.request_logout.connect(self._on_logout)
        self.container.addWidget(self.main_window)
        self.container.setCurrentWidget(self.main_window)

    def _on_logout(self):
        """Handle logout: switch back to login page."""
        if self.main_window:
            self.container.removeWidget(self.main_window)
            self.main_window.deleteLater()
            self.main_window = None

        self.login_page.reset()
        self.container.setCurrentWidget(self.login_page)

    def _on_app_about_to_quit(self):
        """
        If the UI is being closed directly (window close), perform the same
        full shutdown flow as Exit, but without machine-state checks.
        """
        try:
            if self.main_window:
                self.main_window.force_app_shutdown()
        except Exception:
            pass

    def _check_monthly_backup(self):
        """Checks if a monthly auto-backup is due and triggers it."""
        try:
            with get_session() as session:
                backup_service = BackupService(session)
                backup_path = backup_service.check_and_trigger_auto_backup()
                
                if backup_path:
                    # Notify user if backup was actually performed
                    filename = os.path.basename(backup_path)
                    QMessageBox.information(
                        self.container, "Automatic Backup",
                        f"A scheduled monthly database backup was performed successfully.\n"
                        f"File: {filename}\nLocation: /backup"
                    )
        except Exception as e:
            print(f"[AutoBackup] Error during startup check: {e}")

    def run(self):
        self._check_monthly_backup()
        self.container.showFullScreen()
        return self.app.exec()


if __name__ == "__main__":
    app = BlueSquareApp()
    sys.exit(app.run())

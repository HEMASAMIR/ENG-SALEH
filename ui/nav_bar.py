"""
BLUE SQUARE — Vertical Navigation Bar
Persistent narrow sidebar with rotated labels for page navigation and actions.
"""
import sys
import os
import subprocess
from datetime import datetime, timedelta
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QSpacerItem, QSizePolicy, QMessageBox, QFileDialog
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont, QPainter

from core.database import get_session
from services.backup_service import BackupService
from services.report_service import ReportService
from repositories.audit_repository import AuditRepository
from models.audit.audit_trail import AuditActionType
from ui.auth_dialog import AuthDialog


class RotatedButton(QPushButton):
    """
    A QPushButton that draws its text rotated 90 degrees counter-clockwise.
    """

    def __init__(self, text: str, parent=None):
        super().__init__("", parent)
        self._text = text
        self.setFixedWidth(42)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.palette().buttonText().color())
        painter.setFont(self.font())
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        painter.drawText(
            -self.height() // 2, -self.width() // 2,
            self.height(), self.width(),
            Qt.AlignCenter, self._text
        )
        painter.end()


class NavBar(QWidget):
    """
    Vertical navigation bar on the left side of the main window.
    Emits navigation signals and handles export/logout actions directly.
    """

    navigate_home = Signal()
    navigate_recipes = Signal()
    navigate_users = Signal()
    navigate_audit_trail = Signal()
    navigate_setting = Signal()
    navigate_user_policy = Signal()
    request_logout = Signal()
    request_exit = Signal()

    def __init__(self, user_id: int, user_name: str, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.user_name = user_name
        self.setFixedWidth(46)
        self.setStyleSheet("""
            NavBar {
                background-color: #1e1e1e;
                border-right: 2px solid #3e3e42;
            }
        """)
        self._active_index = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 6, 2, 6)
        layout.setSpacing(4)

        btn_style_base = """
            QPushButton {{
                background-color: {bg};
                border: none;
                border-radius: 4px;
                color: {fg};
                font-size: 10px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: #007acc;
                color: #ffffff;
            }}
        """

        # Navigation buttons
        self.buttons = []

        nav_items = [
            ("Home", self._on_home),
            ("Batches", self._on_recipes),
            ("Users", self._on_users),
            ("Audit Trail", self._on_audit_trail),
            ("Setting", self._on_setting),
            ("User Policy", self._on_user_policy),
            ("Export Backup", self._on_export_backup),
            ("Import Backup", self._on_import_backup),
        ]

        for i, (label, callback) in enumerate(nav_items):
            btn = RotatedButton(label, self)
            btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
            btn.clicked.connect(callback)
            layout.addWidget(btn)
            self.buttons.append(btn)

        # Apply visibility rules based on the 4-tier role hierarchy
        if hasattr(self, 'parent') and hasattr(self.parent(), '_can_perform'):
            mw = self.parent()
            
            # Index 1: Batches (Visible to all based on current plan)
            if not mw._can_perform('SHOW RECIPE') and self.user_name != 'operator':
                 # Keep it visible for navigation but restrict inside.
                 pass

            # Index 2: Users (Administrator, Supervisor, Root)
            if not mw._can_perform('SHOW USER'):
                self.buttons[2].setVisible(False)
                
            # Index 3: Audit Trail (Administrator, Supervisor, Root)
            if not mw._can_perform('SHOW AUDIT REPORTS'):
                self.buttons[3].setVisible(False)
                
            # Index 4: Setting (Administrator, Supervisor, Root)
            if not mw._can_perform('EDIT RECIPE'):
                self.buttons[4].setVisible(False)

            # Index 5: User Policy (Admin and Root only)
            if hasattr(self, 'parent') and hasattr(self.parent(), 'role_name'):
                role_key = str(self.parent().role_name).lower().strip()
                if role_key not in ['root', 'admin', 'administrator']:
                    self.buttons[5].setVisible(False)

            # Index 6: Export Backup (Administrator, Root)
            if not mw._can_perform('CREATE BACKUP'):
                self.buttons[6].setVisible(False)
            
            # Index 7: Import Backup (Administrator, Root)
            if not mw._can_perform('RESTORE BACKUP'):
                self.buttons[7].setVisible(False)

        # Spacer to push logout to the bottom
        layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # Logout button near bottom
        self.logout_btn = RotatedButton("Logout", self)
        self.logout_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.logout_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #be3a3a;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #be3a3a;
                color: #252526;
            }
        """)
        self.logout_btn.clicked.connect(self._on_logout)
        layout.addWidget(self.logout_btn)

        # Exit button at the very bottom
        self.exit_btn = RotatedButton("Exit", self)
        self.exit_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.exit_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #f39c12;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #f39c12;
                color: #252526;
            }
        """)
        self.exit_btn.clicked.connect(self._on_exit)
        layout.addWidget(self.exit_btn)

        # Restrict exit button to root and admin roles
        if hasattr(self, 'parent') and hasattr(self.parent(), 'role_name'):
            role_key = str(self.parent().role_name).lower().strip()
            if role_key not in ['root', 'admin', 'administrator']:
                self.exit_btn.setVisible(False)

        self._update_active(0)

    def _update_active(self, index):
        self._active_index = index
        for i, btn in enumerate(self.buttons):
            if i == index:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #007acc;
                        border: none;
                        border-radius: 4px;
                        color: #ffffff;
                        font-size: 10px;
                        font-weight: 700;
                    }
                    QPushButton:hover {
                        background-color: #0062a3;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        border: none;
                        border-radius: 4px;
                        color: #808080;
                        font-size: 10px;
                        font-weight: 700;
                    }
                    QPushButton:hover {
                        background-color: #007acc;
                        color: #ffffff;
                    }
                """)

    # --- Navigation Handlers ---

    def _on_home(self):
        self._update_active(0)
        self.navigate_home.emit()
        if hasattr(self.parent(), "reset_idle_timer"):
            self.parent().reset_idle_timer()

    def _on_recipes(self):
        self._update_active(1)
        self.navigate_recipes.emit()
        if hasattr(self.parent(), "reset_idle_timer"):
            self.parent().reset_idle_timer()

    def _on_users(self):
        self._update_active(2)
        self.navigate_users.emit()
        if hasattr(self.parent(), "reset_idle_timer"):
            self.parent().reset_idle_timer()

    def _on_audit_trail(self):
        self._update_active(3)
        self.navigate_audit_trail.emit()
        if hasattr(self.parent(), "reset_idle_timer"):
            self.parent().reset_idle_timer()

    def _on_setting(self):
        self._update_active(4)
        self.navigate_setting.emit()
        if hasattr(self.parent(), "reset_idle_timer"):
            self.parent().reset_idle_timer()

    def _on_user_policy(self):
        self._update_active(5)
        self.navigate_user_policy.emit()
        if hasattr(self.parent(), "reset_idle_timer"):
            self.parent().reset_idle_timer()

    # --- Export Handlers ---
    def _reset_parent_timer(self):
        if hasattr(self.parent(), "reset_idle_timer"):
            self.parent().reset_idle_timer()

    def _on_export_audit(self):
        self._reset_parent_timer()
        try:
            with get_session() as session:
                report_service = ReportService(session)
                end = datetime.now()
                start = end - timedelta(days=365)
                
                # Prompt for save location (Browse)
                default_filename = f"Audit_Trail_{end.strftime('%Y%H%M%S')}.pdf"
                file_path, _ = QFileDialog.getSaveFileName(
                    self, "Save Audit Trail PDF", default_filename, "PDF Files (*.pdf)"
                )
                
                if not file_path:
                    return

                actual_path = report_service.generate_audit_report(self.user_name, start, end, target_path=file_path)

                # Log the export action
                audit_repo = AuditRepository(session)
                audit_repo.create_audit(
                    user_id=self.user_id,
                    action_type=AuditActionType.EXPORT,
                    location_screen="Navigation Bar",
                    reason=f"Exported Audit Trail Report to PDF: {os.path.basename(actual_path)}"
                )
                session.flush()

                confirm = QMessageBox.information(
                    self, "Report Generated",
                    f"Report saved to:\n{actual_path}\n\nWould you like to view it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                
                if confirm == QMessageBox.Yes:
                    if sys.platform == "win32":
                        os.startfile(actual_path)
                    else:
                        import subprocess
                        opener = "open" if sys.platform == "darwin" else "xdg-open"
                        subprocess.call([opener, actual_path])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export audit trail:\n{str(e)}")

    def _get_app_base_path(self):
        # Resolve base directory (handles PyInstaller frozen state)
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _on_export_backup(self):
        self._reset_parent_timer()
        try:
            with get_session() as session:
                backup_service = BackupService(session)
                backup_file = backup_service.perform_backup(self.user_id, reason="Manual Export Backup")

                if backup_file:
                    QMessageBox.information(
                        self, "Export Complete",
                        f"Database backup exported successfully.\nFile: {os.path.basename(backup_file)}"
                    )
                else:
                    raise Exception("Backup service failed to create file.")
        except Exception as e:
            print(e)
            QMessageBox.critical(self, "Export Error", f"Failed to export backup:\n{str(e)}")

    def _on_import_backup(self):
        self._reset_parent_timer()
        try:
            # File dialog to select backup file
            base_path = self._get_app_base_path()
            backup_dir = os.path.join(base_path, "backup")
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir, exist_ok=True)
                
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select Backup File", backup_dir, "SQLite Backup Files (*.db);;All Files (*)"
            )
            
            if not file_path:
                return

            confirm = QMessageBox.question(
                self, "Confirm Import",
                "Importing a backup will OVERWRITE the current database and require a RESTART.\nAre you sure you want to proceed?",
                QMessageBox.Yes | QMessageBox.No
            )
            if confirm != QMessageBox.Yes:
                return

            # Perform restore via Service
            with get_session() as session:
                backup_service = BackupService(session)
                success = backup_service.restore_backup(self.user_id, file_path, reason="Manual Import Restore")
                if success:
                    QMessageBox.information(
                        self, "Import Complete",
                        "Database restored successfully.\nPlease RESTART the application to reflect the changes."
                    )
                else:
                    raise Exception("Backup service failed to restore file.")

        except Exception as e:
            print(f"[NavBar] Import Error: {e}")
            QMessageBox.critical(self, "Import Error", f"Failed to import backup:\n{str(e)}")

    def _on_logout(self):
        self.request_logout.emit()

    def _on_exit(self):
        self.request_exit.emit()

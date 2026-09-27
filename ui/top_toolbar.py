"""
BLUE SQUARE — Top Toolbar
Persistent toolbar across all pages (except Login).
Shows date/time, software name, user info, and change password button.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QFont, QColor

from ui.change_password_dialog import ChangePasswordDialog


class TopToolbar(QWidget):
    """
    Top toolbar widget showing date/time (left), software name (center),
    and user info with change password button (right).
    """

    def __init__(self, user_name: str, role_name: str, user_id: int, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.role_name = role_name
        self.setMinimumHeight(48)
        self.setStyleSheet("""
            TopToolbar {
                background-color: #2d2d30;
                border-bottom: 2px solid #3e3e42;
            }
        """)
        self._build_ui(user_name, role_name)

        # Timer to update date/time every second
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_datetime)
        self.timer.start(1000)
        self._update_datetime()

    def _build_ui(self, user_name: str, role_name: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        # Left — Date & Time
        self.datetime_label = QLabel()
        self.datetime_label.setFont(QFont("Segoe UI", 12))
        self.datetime_label.setStyleSheet("color: #cccccc; background: transparent;")
        layout.addWidget(self.datetime_label, 0, Qt.AlignLeft)

        # PLC Status Indicator
        self.plc_label = QLabel("PLC: [OFF]")
        self.plc_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.plc_label.setStyleSheet("color: #808080; background: transparent;")
        self.plc_label.setToolTip("PLC Connection Status")
        layout.addWidget(self.plc_label, 0, Qt.AlignLeft)

        # PLC Signal LED — small circular indicator
        self.plc_led = QLabel()
        self.plc_led.setFixedSize(14, 14)
        self.plc_led.setStyleSheet("""
            QLabel {
                background-color: #2d2d30;
                border-radius: 7px;
                border: 1px solid #3e3e42;
            }
        """)
        self.plc_led.setToolTip("PLC Signal Indicator")
        layout.addWidget(self.plc_led, 0, Qt.AlignLeft)

        layout.addStretch()

        # Inactivity Timer (Visible for root only)
        self.inactivity_label = QLabel("")
        self.inactivity_label.setFont(QFont("Consolas", 11))
        self.inactivity_label.setStyleSheet("color: #ff9900; background: transparent;")
        self.inactivity_label.setVisible(role_name == 'root')
        layout.addWidget(self.inactivity_label, 0, Qt.AlignCenter)

        # Timeout Modify Button (root only)
        if role_name == 'root':
            self.timeout_btn = QPushButton("SET")
            self.timeout_btn.setToolTip("Modify Session Timeout")
            self.timeout_btn.setFixedSize(28, 28)
            self.timeout_btn.setStyleSheet("""
                QPushButton { background: transparent; border: none; font-size: 16px; color: #808080; }
                QPushButton:hover { color: #007acc; }
            """)
            self.timeout_btn.clicked.connect(self._on_modify_timeout)
            layout.addWidget(self.timeout_btn, 0, Qt.AlignCenter)

        layout.addStretch()

        # Center — Software Name
        title_label = QLabel("BLUE SQUARE - CMS")
        title_label.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title_label.setStyleSheet("color: #007acc; letter-spacing: 4px; background: transparent;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label, 0, Qt.AlignCenter)

        layout.addStretch()

        # Right — User Info
        user_info = QLabel(f"USER: {user_name}  |  {role_name.upper()}")
        user_info.setFont(QFont("Segoe UI", 11))
        user_info.setStyleSheet("color: #808080; background: transparent;")
        layout.addWidget(user_info, 0, Qt.AlignRight)

        # Change Password Button
        change_pwd_btn = QPushButton("PWD")
        change_pwd_btn.setToolTip("Change Password")
        change_pwd_btn.setFixedSize(36, 32)
        change_pwd_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #3e3e42;
                border-radius: 4px;
                font-size: 14px;
                color: #cccccc;
            }
            QPushButton:hover {
                background-color: #454545;
                border-color: #007acc;
            }
        """)
        change_pwd_btn.clicked.connect(self._on_change_password)
        layout.addWidget(change_pwd_btn, 0, Qt.AlignRight)

    def _update_datetime(self):
        now = QDateTime.currentDateTime()
        self.datetime_label.setText(now.toString("yyyy-MM-dd  hh:mm:ss"))

    def update_inactivity_timer(self, idle_seconds, timeout_seconds):
        """Update the inactivity label with formatted time."""
        if self.role_name != 'root':
            return
            
        def fmt(s):
            m = int(s // 60)
            sec = int(s % 60)
            return f"{m:02d}:{sec:02d}"

        self.inactivity_label.setText(f"IDLE: {fmt(idle_seconds)} / {fmt(timeout_seconds)}")

    def _on_modify_timeout(self):
        """Allow root to modify inactivity timeout."""
        from PySide6.QtWidgets import QInputDialog
        from core.config import settings
        
        val, ok = QInputDialog.getInt(
            self, "Session Timeout",
            "Enter new timeout in minutes (1-60):",
            settings.AUTH_SESSION_TIMEOUT_MINUTES, 1, 60, 1
        )
        if ok:
            settings.AUTH_SESSION_TIMEOUT_MINUTES = val
            QMessageBox.information(self, "Updated", f"Session timeout set to {val} minutes.")

    def _on_change_password(self):
        dlg = ChangePasswordDialog(self.user_id, self)
        dlg.exec()

    def update_plc_status(self, is_connected: bool, message: str = ""):
        """Update the visual PLC connection status."""
        if is_connected:
            self.plc_label.setText("PLC: [ON]")
            self.plc_label.setStyleSheet("color: #2ECC71; background: transparent;")
        else:
            self.plc_label.setText("PLC: [OFF]")
            self.plc_label.setStyleSheet("color: #E74C3C; background: transparent;")

    def flash_plc_led(self):
        """Flash the PLC signal LED green for 100ms, then revert to background."""
        self.plc_led.setStyleSheet("""
            QLabel {
                background-color: #2ECC71;
                border-radius: 7px;
                border: 1px solid #27AE60;
            }
        """)
        QTimer.singleShot(100, self._reset_plc_led)

    def _reset_plc_led(self):
        """Reset PLC LED to default (background) color."""
        self.plc_led.setStyleSheet("""
            QLabel {
                background-color: #2d2d30;
                border-radius: 7px;
                border: 1px solid #3e3e42;
            }
        """)

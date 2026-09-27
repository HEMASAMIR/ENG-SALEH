"""
BLUE SQUARE — Re-Authorization Dialog
Used for sensitive actions on Users, Recipe, and Audit Trail pages.
Requires the user to re-enter their credentials before proceeding.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.database import get_session
from services.auth_service import AuthService


class AuthDialog(QDialog):
    """
    Modal dialog that requires the user to authenticate before performing a sensitive action.
    Returns True from exec() if authentication succeeds.
    """

    def __init__(self, title="Authorization Required", message="Enter your credentials to proceed.", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.message = message
        self.setMinimumSize(400, 320)
        self.resize(400, 320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.authenticated_user_id = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header = QLabel("🔒  Authorize Action")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet("color: #007acc;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        desc = QLabel(self.message)
        desc.setStyleSheet("color: #808080; font-size: 12px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        layout.addSpacing(8)

        # Username
        lbl_user = QLabel("Username")
        lbl_user.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl_user)
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter username")
        layout.addWidget(self.username_input)

        # Password
        lbl_pass = QLabel("Password")
        lbl_pass.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl_pass)
        pass_layout = QHBoxLayout()
        pass_layout.setContentsMargins(0, 0, 0, 0)
        pass_layout.setSpacing(5)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter password")
        self.password_input.setEchoMode(QLineEdit.Password)
        pass_layout.addWidget(self.password_input)

        self.toggle_pass_btn = QPushButton("👁")
        self.toggle_pass_btn.setFixedSize(30, 30)
        self.toggle_pass_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_pass_btn.setCheckable(True)
        self.toggle_pass_btn.toggled.connect(
            lambda checked: self.password_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        pass_layout.addWidget(self.toggle_pass_btn)

        layout.addLayout(pass_layout)

        layout.addSpacing(8)

        # Buttons
        btn_layout = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.auth_btn = QPushButton("Authorize")
        self.auth_btn.setObjectName("accent_button")
        self.auth_btn.clicked.connect(self._on_authorize)
        btn_layout.addWidget(self.auth_btn)
        layout.addLayout(btn_layout)

        # Enter key triggers authorize
        self.password_input.returnPressed.connect(self._on_authorize)

    def _on_authorize(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()

        if not username or not password:
            QMessageBox.warning(self, "Validation", "Please enter both username and password.")
            return

        try:
            with get_session() as session:
                auth_service = AuthService(session)
                user, msg = auth_service.verify_credentials(
                    username, password, location_screen=self.windowTitle() or "Authorization Dialog"
                )
                if user:
                    self.authenticated_user_id = user.id
                    self.accept()
                else:
                    QMessageBox.warning(self, "Authorization Failed", msg)
                    self.password_input.clear()
                    self.password_input.setFocus()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Authorization error: {str(e)}")

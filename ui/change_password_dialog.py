"""
BLUE SQUARE — Change Password Dialog
Allows the current user to change their own password with verification.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.database import get_session
from services.auth_service import AuthService


class ChangePasswordDialog(QDialog):
    """
    Dialog for a user to change their own password.
    Enforces clinical password complexity requirements.
    """

    def __init__(self, user_id: int, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.setWindowTitle("Change Password (v2)")
        self.setMinimumSize(440, 600)
        self.setMaximumSize(440, 750)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header = QLabel("🔑  Change Password (v2)")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet("color: #007acc;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        desc = QLabel(
            "Password must be at least 8 characters with uppercase,\n"
            "lowercase, digit, and special character."
        )
        desc.setStyleSheet("color: #808080; font-size: 11px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        layout.addSpacing(6)

        # Helper logic for creating togglable password fields inline
        def create_password_field(placeholder_text):
            pass_layout = QHBoxLayout()
            pass_layout.setContentsMargins(0, 0, 0, 0)
            pass_layout.setSpacing(5)

            line_edit = QLineEdit()
            line_edit.setPlaceholderText(placeholder_text)
            line_edit.setEchoMode(QLineEdit.Password)
            pass_layout.addWidget(line_edit)

            toggle_btn = QPushButton("👁")
            toggle_btn.setFixedSize(30, 30)
            toggle_btn.setCursor(Qt.PointingHandCursor)
            toggle_btn.setCheckable(True)
            toggle_btn.toggled.connect(
                lambda checked, le=line_edit: le.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
            )
            pass_layout.addWidget(toggle_btn)
            return line_edit, pass_layout

        # Current Password
        lbl1 = QLabel("Current Password")
        lbl1.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl1)
        self.current_password, layout1 = create_password_field("Enter current password")
        layout.addLayout(layout1)

        # New Password
        lbl2 = QLabel("New Password")
        lbl2.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl2)
        self.new_password, layout2 = create_password_field("Enter new password")
        layout.addLayout(layout2)

        # Complexity indicators
        self.indicators = {}
        reqs = [
            ("length", "At least 8 characters"),
            ("uppercase", "Uppercase letter (A-Z)"),
            ("lowercase", "Lowercase letter (a-z)"),
            ("digit", "Numeric digit (0-9)"),
            ("special", "Special character (!@#_...)")
        ]
        
        indicator_layout = QVBoxLayout()
        indicator_layout.setSpacing(6)
        indicator_layout.setContentsMargins(15, 4, 15, 4)
        for key, text in reqs:
            lbl = QLabel(f"•  {text}")
            lbl.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 500;")
            lbl.setFixedHeight(20)
            indicator_layout.addWidget(lbl)
            self.indicators[key] = lbl
        layout.addLayout(indicator_layout)

        self.new_password.textChanged.connect(self._update_complexity)

        # Confirm Password
        lbl3 = QLabel("Confirm New Password")
        lbl3.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl3)
        self.confirm_password, layout3 = create_password_field("Re-enter new password")
        layout.addLayout(layout3)

        layout.addSpacing(8)

        # Buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save Password")
        save_btn.setObjectName("accent_button")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        self.confirm_password.returnPressed.connect(self._on_save)

    def _update_complexity(self, text):
        try:
            status = AuthService.get_password_complexity_status(text)
            
            for key, is_ok in status.items():
                lbl = self.indicators.get(key)
                if not lbl: continue
                
                if is_ok:
                    lbl.setStyleSheet("color: #2ecc71; font-weight: bold; font-size: 12px;")
                    lbl.setText(f"✔  {lbl.text().replace('•  ', '').replace('✔  ', '')}")
                else:
                    lbl.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 500;")
                    lbl.setText(f"•  {lbl.text().replace('•  ', '').replace('✔  ', '')}")
        except Exception as e:
            print(f"UI Update Error: {e}")

    def _on_save(self):
        current = self.current_password.text()
        new = self.new_password.text()
        confirm = self.confirm_password.text()

        if not current or not new or not confirm:
            QMessageBox.warning(self, "Validation", "All fields are required.")
            return

        if new != confirm:
            QMessageBox.warning(self, "Mismatch", "New passwords do not match.")
            return

        try:
            with get_session() as session:
                auth_service = AuthService(session)
                success, msg = auth_service.change_password(self.user_id, current, new)
                if success:
                    QMessageBox.information(self, "Success", "Password changed successfully.")
                    self.accept()
                else:
                    QMessageBox.warning(self, "Failed", msg)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Password change error: {str(e)}")

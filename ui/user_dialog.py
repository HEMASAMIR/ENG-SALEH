"""
BLUE SQUARE — User Add/Edit Dialog
Dialog for creating or editing a user with all required fields.
In edit mode, also allows managing user permissions.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QMessageBox, QCheckBox, QScrollArea,
    QWidget, QGroupBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.database import get_session
from repositories.role_repository import RoleRepository


class UserDialog(QDialog):
    """
    Dialog for adding or editing a user.
    mode='add' — new user creation with password field.
    mode='edit' — editing existing user (no password field, password kept).
                  Also allows managing user permissions.
    """

    def __init__(self, parent=None, mode='add', user_data=None):
        super().__init__(parent)
        self.mode = mode
        self.user_data = user_data or {}
        self.result_data = None
        self.setWindowTitle("Add User (v2)" if mode == 'add' else "Edit User (v2)")
        self.setMinimumSize(440, 600)
        self.setMaximumSize(440, 750)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._roles = []
        self.actor_role_id = getattr(parent, 'role_id', 99) if parent else 99
        self._load_roles()
        self._build_ui()

    def _load_roles(self):
        try:
            # Rank mapping: lower is higher priority
            rank_map = {1: 0, 2: 1, 3: 2, 4: 3} # root=0, admin=1, supervisor=2, operator=3
            actor_rank = rank_map.get(self.actor_role_id, 99)

            with get_session() as session:
                role_repo = RoleRepository(session)
                all_roles = role_repo.all()
                self._roles = []
                for r in all_roles:
                    r_rank = rank_map.get(r.role_id, 99)
                    if r_rank >= actor_rank:
                        self._roles.append((r.role_id, r.role_name))
        except Exception:
            self._roles = []

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel("👤  Add New User (v2)" if self.mode == 'add' else "✏️  Edit User (v2)")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet("color: #007acc;" if self.mode == 'add' else "color: #007acc;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        layout.addSpacing(8)

        # Username
        lbl_user = QLabel("Username")
        lbl_user.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl_user)
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter username")
        if self.user_data.get('user_name'):
            self.username_input.setText(self.user_data['user_name'])
            if self.mode == 'edit':
                self.username_input.setReadOnly(True)
                self.username_input.setStyleSheet("background-color: #1e1e1e; color: #808080;")
        layout.addWidget(self.username_input)

        # Full Name
        lbl_name = QLabel("Full Name")
        lbl_name.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl_name)
        self.fullname_input = QLineEdit()
        self.fullname_input.setPlaceholderText("Enter full name")
        if self.user_data.get('full_name'):
            self.fullname_input.setText(self.user_data['full_name'])
        layout.addWidget(self.fullname_input)

        # Password (for add mode, and optional for edit mode)
        if self.mode == 'add' or self.mode == 'edit':
            lbl_pass = QLabel("Password" if self.mode == 'add' else "New Password (Optional)")
            lbl_pass.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
            layout.addWidget(lbl_pass)
            pass_layout = QHBoxLayout()
            pass_layout.setContentsMargins(0, 0, 0, 0)
            pass_layout.setSpacing(5)

            self.password_input = QLineEdit()
            if self.mode == 'add':
                self.password_input.setPlaceholderText("Min 8 chars: uppercase, lowercase, digit, special")
            else:
                self.password_input.setPlaceholderText("Leave blank to keep current password")
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
                # Use a larger, very clear font and white color
                lbl.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 500;")
                lbl.setFixedHeight(20) # Ensure enough vertical space to prevent distortion
                indicator_layout.addWidget(lbl)
                self.indicators[key] = lbl
            layout.addLayout(indicator_layout)

            from services.auth_service import AuthService
            self.password_input.textChanged.connect(self._update_complexity)

        # Role
        lbl_role = QLabel("Role")
        lbl_role.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl_role)
        self.role_combo = QComboBox()
        for role_id, role_name in self._roles:
            self.role_combo.addItem(role_name, role_id)
        if self.user_data.get('role_id'):
            idx = self.role_combo.findData(self.user_data['role_id'])
            if idx >= 0:
                self.role_combo.setCurrentIndex(idx)
        layout.addWidget(self.role_combo)

        # Disabled checkbox (edit mode only)
        if self.mode == 'edit':
            self.disabled_check = QCheckBox("Account Disabled")
            self.disabled_check.setChecked(self.user_data.get('is_disabled', False))
            layout.addWidget(self.disabled_check)

        # Reason for adding/editing user
        lbl_reason = QLabel("Reason")
        lbl_reason.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl_reason)
        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText("Enter reason for adding/editing this user (Audited)")
        layout.addWidget(self.reason_input)

        layout.addSpacing(12)

        # Buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Create User" if self.mode == 'add' else "Save Changes")
        save_btn.setObjectName("accent_button")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def _update_complexity(self, text):
        try:
            if self.mode == 'edit' and not text:
                for lbl in self.indicators.values():
                    lbl.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 500;")
                    clean = lbl.text().replace("✔  ", "").replace("•  ", "")
                    lbl.setText(f"•  {clean}")
                return

            from services.auth_service import AuthService
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
        username = self.username_input.text().strip()
        fullname = self.fullname_input.text().strip()
        role_id = self.role_combo.currentData()

        reason = self.reason_input.text().strip()
        if not reason:
            QMessageBox.warning(self, "Validation", "Reason is required for auditing purposes.")
            return

        self.result_data = {
            'user_name': username,
            'full_name': fullname,
            'role_id': role_id,
            'reason': reason,
        }

        if self.mode == 'add':
            password = self.password_input.text()
            if not password:
                QMessageBox.warning(self, "Validation", "Password is required.")
                return
            self.result_data['password'] = password

        if self.mode == 'edit':
            self.result_data['is_disabled'] = self.disabled_check.isChecked()
            password = self.password_input.text()
            if password:
                from services.auth_service import AuthService
                status = AuthService.get_password_complexity_status(password)
                if not all(status.values()):
                    QMessageBox.warning(self, "Validation", "New password does not meet complexity requirements.")
                    return
                self.result_data['password'] = password

        self.accept()


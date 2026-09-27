"""
BLUE SQUARE — Permissions Dialog
Dialog for granting/revoking individual permissions for a user.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QScrollArea, QWidget, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.database import get_session
from repositories.permission_repository import PermissionRepository
from models.auth.user_permission import UserPermission
from sqlalchemy import select, delete


class PermissionsDialog(QDialog):
    """
    Dialog showing all available permissions as checkboxes.
    Pre-checks permissions the user already has.
    """

    def __init__(self, user_id: int, user_name: str, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.setWindowTitle(f"Permissions — {user_name}")
        self.setFixedSize(460, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._checkboxes = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel("🔐  Manage Permissions")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet("color: #007acc;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        layout.addSpacing(8)

        # Scroll area for permissions
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        perm_widget = QWidget()
        perm_layout = QVBoxLayout(perm_widget)
        perm_layout.setSpacing(6)

        try:
            with get_session() as session:
                perm_repo = PermissionRepository(session)
                all_perms = perm_repo.all()

                # Get current user permissions
                query = select(UserPermission.permission_id).filter(UserPermission.user_id == self.user_id)
                current_perms = set(session.execute(query).scalars().all())

                for perm in all_perms:
                    cb = QCheckBox(f"{perm.permission_name}")
                    cb.setToolTip(perm.description or "")
                    cb.setChecked(perm.permission_id in current_perms)
                    cb.setProperty("permission_id", perm.permission_id)
                    perm_layout.addWidget(cb)
                    self._checkboxes.append(cb)
        except Exception as e:
            err = QLabel(f"Error loading permissions: {str(e)}")
            err.setStyleSheet("color: #be3a3a;")
            perm_layout.addWidget(err)

        perm_layout.addStretch()
        scroll.setWidget(perm_widget)
        layout.addWidget(scroll)

        layout.addSpacing(8)

        # Buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save Permissions")
        save_btn.setObjectName("accent_button")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def selected_permission_ids(self) -> list:
        """Return a list of IDs for all currently checked permissions."""
        selected = []
        for cb in self._checkboxes:
            if cb.isChecked():
                selected.append(cb.property("permission_id"))
        return selected

    def _on_save(self):
        """Close the dialog and signal success; saving is handled by the worker."""
        self.accept()

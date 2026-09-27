import os
import sys
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QFrame, QPushButton, QMessageBox, QSpacerItem, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.user_settings import load_user_settings, save_user_settings
from core.config import settings
from core.database import get_session
from repositories.audit_repository import AuditRepository
from models.audit.audit_trail import AuditActionType


class UserPolicyPage(QWidget):
    """
    User Policy page allowing root and admin users to configure security settings:
    - Auto Logout (Minutes)
    - Password Expiry (Days)
    - Login Continuous Errors (Failed attempts lockout)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("user_policy_page")
        self.main_window = parent
        self._load_qss()
        self._build_ui()
        self._load_data()

    def _load_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'user_policy.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r', encoding='utf-8') as f:
                self.setStyleSheet(f.read())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # Page Title
        title = QLabel("USER POLICY SETTINGS")
        title.setObjectName("page_title")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        layout.addWidget(title)

        # Policy Config Card
        self.card = QFrame()
        self.card.setObjectName("policy_card")
        self.card.setMaximumWidth(500)
        card_layout = QVBoxLayout(self.card)
        card_layout.setSpacing(15)

        lbl_card_title = QLabel("Clinical Security Configuration")
        lbl_card_title.setObjectName("card_title")
        lbl_card_title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        card_layout.addWidget(lbl_card_title)

        # Auto Logout (Minutes)
        lbl_logout = QLabel("Auto Logout (Minutes)")
        lbl_logout.setObjectName("field_label")
        card_layout.addWidget(lbl_logout)
        
        self.logout_input = QLineEdit()
        self.logout_input.setObjectName("policy_input")
        self.logout_input.setPlaceholderText("Enter minutes (e.g. 5)")
        card_layout.addWidget(self.logout_input)

        # Password Expiry (Days)
        lbl_expiry = QLabel("Password Expiry (Days)")
        lbl_expiry.setObjectName("field_label")
        card_layout.addWidget(lbl_expiry)
        
        self.expiry_input = QLineEdit()
        self.expiry_input.setObjectName("policy_input")
        self.expiry_input.setPlaceholderText("Enter days (e.g. 90)")
        card_layout.addWidget(self.expiry_input)

        # Login Continuous Errors
        lbl_errors = QLabel("Login Continuous Errors (Lockout attempts)")
        lbl_errors.setObjectName("field_label")
        card_layout.addWidget(lbl_errors)
        
        self.errors_input = QLineEdit()
        self.errors_input.setObjectName("policy_input")
        self.errors_input.setPlaceholderText("Enter max attempts (e.g. 5)")
        card_layout.addWidget(self.errors_input)

        card_layout.addSpacing(10)

        # Submit button
        self.btn_submit = QPushButton("Submit")
        self.btn_submit.setObjectName("submit_button")
        self.btn_submit.setCursor(Qt.PointingHandCursor)
        self.btn_submit.clicked.connect(self._on_submit)
        card_layout.addWidget(self.btn_submit)

        layout.addWidget(self.card)
        layout.addStretch()

    def _load_data(self):
        """Load settings from user_settings.json and display them."""
        try:
            data = load_user_settings()
            
            # Use current config settings as fallbacks if not in JSON
            logout_min = data.get("auth_session_timeout_minutes", settings.AUTH_SESSION_TIMEOUT_MINUTES)
            expiry_days = data.get("auth_password_expiry_days", settings.AUTH_PASSWORD_EXPIRY_DAYS)
            max_errors = data.get("auth_max_failed_attempts", settings.AUTH_MAX_FAILED_ATTEMPTS)

            self.logout_input.setText(str(logout_min))
            self.expiry_input.setText(str(expiry_days))
            self.errors_input.setText(str(max_errors))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load user policy:\n{str(e)}")

    def _on_submit(self):
        txt_logout = self.logout_input.text().strip()
        txt_expiry = self.expiry_input.text().strip()
        txt_errors = self.errors_input.text().strip()

        if not txt_logout or not txt_expiry or not txt_errors:
            QMessageBox.warning(self, "Validation", "All fields are required.")
            return

        try:
            logout_min = int(txt_logout)
            expiry_days = int(txt_expiry)
            max_errors = int(txt_errors)

            if logout_min <= 0 or expiry_days <= 0 or max_errors <= 0:
                raise ValueError("Values must be greater than zero")
        except ValueError as e:
            QMessageBox.warning(self, "Validation", "Please enter positive integers for all fields.")
            return

        try:
            # Load, modify, and save settings JSON
            data = load_user_settings()
            data["auth_session_timeout_minutes"] = logout_min
            data["auth_password_expiry_days"] = expiry_days
            data["auth_max_failed_attempts"] = max_errors
            save_user_settings(data)

            # Update running settings in memory immediately
            settings.AUTH_SESSION_TIMEOUT_MINUTES = logout_min
            settings.AUTH_PASSWORD_EXPIRY_DAYS = expiry_days
            settings.AUTH_MAX_FAILED_ATTEMPTS = max_errors

            # Audit Trail logging
            actor_id = getattr(self.main_window, 'user_id', 1)
            try:
                with get_session() as session:
                    audit_repo = AuditRepository(session)
                    audit_repo.create_audit(
                        user_id=actor_id,
                        action_type=AuditActionType.EDIT,
                        location_screen="User Policy Settings",
                        reason=f"Adjusted User Policy: Auto Logout = {logout_min} min, Password Expiry = {expiry_days} days, Lockout Attempts = {max_errors}."
                    )
                    session.commit()
            except Exception as ae:
                print(f"[UserPolicyPage] Audit failed: {ae}")

            QMessageBox.information(self, "Success", "Success\nDone")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save user policy settings:\n{str(e)}")

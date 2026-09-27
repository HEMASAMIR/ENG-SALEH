"""
BLUE SQUARE — Login Page
First page of the application. Authentication with username/password.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QSpacerItem, QSizePolicy, QMessageBox, QDialog
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from core.database import get_session
from services.auth_service import AuthService


class LoginPage(QWidget):
    """
    Login page with BLUE SQUARE branding and credential form.
    Emits login_success with (session_id, user_id, user_name, role_name) on successful authentication.
    """

    login_success = Signal(object, int, str, str, int)  # session_id, user_id, user_name, role_name, role_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("login_page")
        self._load_qss()
        self._build_ui()

    def _load_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'login.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Center everything
        main_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        center_layout = QHBoxLayout()
        center_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # Login Card
        card = QFrame()
        card.setObjectName("login_card")
        card.setMinimumSize(420, 520)
        card.setMaximumSize(420, 600)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(10)
        card_layout.setContentsMargins(36, 36, 36, 36)

        # App Title
        title = QLabel("BLUE SQUARE - CMS")
        title.setObjectName("app_title")
        title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(title)

        subtitle = QLabel("INDUSTRIAL INSPECTION SYSTEM")
        subtitle.setObjectName("app_subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(subtitle)

        card_layout.addSpacing(24)

        # Separator
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        card_layout.addWidget(sep)

        card_layout.addSpacing(16)

        # Username
        lbl_user = QLabel("USERNAME")
        lbl_user.setObjectName("field_label")
        card_layout.addWidget(lbl_user)

        self.username_input = QLineEdit()
        self.username_input.setObjectName("login_input")
        self.username_input.setPlaceholderText("Enter your username")
        card_layout.addWidget(self.username_input)

        card_layout.addSpacing(8)

        # Password
        lbl_pass = QLabel("PASSWORD")
        lbl_pass.setObjectName("field_label")
        card_layout.addWidget(lbl_pass)

        pass_layout = QHBoxLayout()
        pass_layout.setContentsMargins(0, 0, 0, 0)
        pass_layout.setSpacing(5)
        
        self.password_input = QLineEdit()
        self.password_input.setObjectName("login_input")
        self.password_input.setPlaceholderText("Enter your password")
        self.password_input.setEchoMode(QLineEdit.Password)
        pass_layout.addWidget(self.password_input)
        
        self.toggle_pass_btn = QPushButton("👁")
        self.toggle_pass_btn.setObjectName("toggle_btn")
        self.toggle_pass_btn.setFixedSize(35, 35)
        self.toggle_pass_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_pass_btn.setCheckable(True)
        self.toggle_pass_btn.toggled.connect(
            lambda checked: self.password_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        pass_layout.addWidget(self.toggle_pass_btn)
        
        card_layout.addLayout(pass_layout)

        card_layout.addSpacing(8)

        # Error label
        self.error_label = QLabel("")
        self.error_label.setObjectName("error_label")
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setWordWrap(True)
        card_layout.addWidget(self.error_label)

        card_layout.addSpacing(8)

        # Login Button
        self.login_btn = QPushButton("LOGIN")
        self.login_btn.setObjectName("login_button")
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.clicked.connect(self._on_login)
        card_layout.addWidget(self.login_btn)

        card_layout.addStretch()

        card_layout.addStretch()

        center_layout.addWidget(card)
        center_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))
        main_layout.addLayout(center_layout)
        main_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # Enter key triggers login
        self.password_input.returnPressed.connect(self._on_login)
        self.username_input.returnPressed.connect(lambda: self.password_input.setFocus())

    def _on_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()

        if not username or not password:
            self.error_label.setText("Please enter both username and password.")
            return

        self.error_label.setText("")
        self.login_btn.setEnabled(False)
        self.login_btn.setText("AUTHENTICATING...")

        try:
            # Phase 1: Authenticate and gather data (close session before dialog)
            session_id = None
            user_id = None
            user_name_val = None
            role_name_val = None
            password_expired = False

            with get_session() as session:
                auth_service = AuthService(session)
                auth_session, msg = auth_service.authenticate(username, password)

                if auth_session:
                    from repositories.user_repository import UserRepository
                    from repositories.role_repository import RoleRepository
                    user_repo = UserRepository(session)
                    user = user_repo.get_by_id(auth_session.user_id)
                    role_repo = RoleRepository(session)
                    role = role_repo.get_by_id(user.role_id)

                    session_id = auth_session.session_id
                    user_id = user.id
                    user_name_val = user.user_name
                    role_name_val = role.role_name if role else "unknown"
                    role_id_val = user.role_id
                    is_first_login = getattr(user, 'is_first_login', False)

                    # Check if password needs changing
                    from datetime import datetime
                    from core.config import settings
                    if user.password_last_change:
                        days_since = (datetime.utcnow() - user.password_last_change).days
                        if days_since >= settings.AUTH_PASSWORD_EXPIRY_DAYS:
                            password_expired = True
                else:
                    self.error_label.setText(msg)
                    self.login_btn.setEnabled(True)
                    self.login_btn.setText("LOGIN")
                    return

            # Phase 2: Handle first login password change or password expiry
            if is_first_login:
                QMessageBox.information(
                    self, "First Login",
                    "This is your first login. You must change your password before proceeding."
                )
                from ui.change_password_dialog import ChangePasswordDialog
                dlg = ChangePasswordDialog(user_id, self)
                if dlg.exec() == QDialog.Accepted:
                    with get_session() as session:
                        from repositories.user_repository import UserRepository
                        user_repo = UserRepository(session)
                        db_user = user_repo.get_by_id(user_id)
                        if db_user:
                            db_user.is_first_login = False
                            session.commit()
                else:
                    self.login_btn.setEnabled(True)
                    self.login_btn.setText("LOGIN")
                    return
            elif password_expired:
                QMessageBox.warning(
                    self, "Password Expired",
                    "Your password has expired. Please change it now."
                )
                from ui.change_password_dialog import ChangePasswordDialog
                dlg = ChangePasswordDialog(user_id, self)
                if dlg.exec() != QDialog.Accepted:
                    self.login_btn.setEnabled(True)
                    self.login_btn.setText("LOGIN")
                    return

            # Phase 3: Emit success
            self.login_success.emit(session_id, user_id, user_name_val, role_name_val, role_id_val)
            self.username_input.clear()
            self.password_input.clear()
            self.error_label.setText("")
        except Exception as e:
            print(e)
            self.error_label.setText(f"System error: {str(e)}")
        finally:
            self.login_btn.setEnabled(True)
            self.login_btn.setText("LOGIN")

    def reset(self):
        """Reset the login page for next use."""
        self.username_input.clear()
        self.password_input.clear()
        self.error_label.setText("")
        self.username_input.setFocus()

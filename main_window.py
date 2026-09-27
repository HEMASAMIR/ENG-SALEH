"""
BLUE SQUARE — Main Window
Combines TopToolbar + NavBar + QStackedWidget with all pages.
Manages page switching, role-based access, and session timeout.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QMessageBox, QApplication
)
from PySide6.QtCore import Signal, QTimer, QEvent, Qt
from datetime import datetime, timedelta

from core.database import get_session
from core.config import settings
from services.auth_service import AuthService
from repositories.audit_repository import AuditRepository
from models.audit.audit_trail import AuditActionType
from models.auth.user import User
from models.auth.permission import Permission
from models.auth.role_permission import RolePermission
from models.auth.user_permission import UserPermission
from sqlalchemy import select
import redis

from ui.top_toolbar import TopToolbar
from ui.nav_bar import NavBar
from ui.home_page import HomePage
from ui.users_page import UsersPage
from ui.recipe_page import RecipePage
from ui.audit_trail_page import AuditTrailPage
from ui.setting_page import SettingPage
from ui.user_policy_page import UserPolicyPage


class MainWindow(QWidget):
    """
    Main application window after login.
    Contains the toolbar, navigation bar, and stacked pages.
    Session timeout only counts true idle time (no mouse/keyboard activity).
    """

    request_logout = Signal()

    # Events that count as real user activity
    _ACTIVITY_EVENTS = {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.KeyPress,
        QEvent.Type.KeyRelease,
        QEvent.Type.Wheel,
        QEvent.Type.MouseMove,
        QEvent.Type.HoverMove,
        QEvent.Type.TabletMove,
        QEvent.Type.TabletPress,
        QEvent.Type.TabletRelease,
    }

    def __init__(self, session_id, user_id, user_name, role_name, role_id=None, plc_comm=None):
        super().__init__()
        self.session_id = session_id
        self.user_id = user_id
        self.user_name = user_name
        self.role_name = role_name
        self.role_id = role_id
        self.plc_comm = plc_comm
        self._shutdown_in_progress = False

        # Track last real user activity locally
        self._last_user_activity = datetime.utcnow()

        self._build_ui()
        self._setup_session_timer()
        self._setup_plc_monitoring()
        self._setup_mismatch_poller()

        # Install event filter on the application to detect real user input
        QApplication.instance().installEventFilter(self)

    def _setup_plc_monitoring(self):
        """Connect PLC communication signals to UI components."""
        if self.plc_comm:
            self.plc_comm.connection_changed.connect(self.toolbar.update_plc_status)
            self.plc_comm.plc_signal_sent.connect(self.toolbar.flash_plc_led)
            self.plc_comm.plc_alarm_warning.connect(self._on_plc_alarm_warning)
            self.plc_comm.values_updated.connect(self._on_plc_values_updated)
            self.toolbar.update_plc_status(self.plc_comm.is_connected)

    def _on_plc_alarm_warning(self, message: str):
        """Show one warning per M10/M12 rising edge (handled in plc_service)."""
        QMessageBox.warning(self, "PLC Warning", message)

    def _on_plc_values_updated(self, data: dict):
        try:
            if hasattr(self, 'recipe_page') and self.recipe_page:
                self.recipe_page.update_plc_values(data)
            if hasattr(self, 'setting_page') and self.setting_page:
                self.setting_page.update_plc_values(data)
        except Exception:
            pass

    def _setup_mismatch_poller(self):
        """Poll Redis for immediate mismatch signal and send M20 to PLC."""
        self._mismatch_redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB
        )
        self._mismatch_timer = QTimer(self)
        self._mismatch_timer.timeout.connect(self._check_mismatch_signal)
        self._mismatch_timer.start(50)  # Check every 50ms for immediate response

    def _check_mismatch_signal(self):
        """If processing thread flagged a mismatch immediately, send M20 pulse to PLC."""
        try:
            val = self._mismatch_redis.get("plc_mismatch_signal")
            if val == b"true":
                self._mismatch_redis.set("plc_mismatch_signal", "false")
                if self.plc_comm and self.plc_comm.is_connected:
                    self.plc_comm.signal_mismatch()
                    print("[MainWindow] Sent M20 mismatch signal to PLC")
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """Detect real user interactions to track idle time."""
        if event.type() in self._ACTIVITY_EVENTS:
            self.reset_idle_timer()
        return False

    def reset_idle_timer(self):
        """Explicitly reset the idle activity timestamp."""
        self._last_user_activity = datetime.utcnow()

    def _has_permission(self, permission_name: str) -> bool:
        """Check if the current user has a specific permission via role or direct assignment."""
        if self.role_name == 'root':
            return True
        try:
            with get_session() as session:
                user = session.execute(select(User).filter(User.id == self.user_id)).scalar_one_or_none()
                if not user:
                    return False
                
                # Check user_permission
                user_perm = session.execute(
                    select(Permission)
                    .join(UserPermission, UserPermission.permission_id == Permission.permission_id)
                    .filter(UserPermission.user_id == self.user_id, Permission.permission_name == permission_name)
                ).scalar_one_or_none()
                if user_perm:
                    return True
                    
                # Check role_permission
                role_perm = session.execute(
                    select(Permission)
                    .join(RolePermission, RolePermission.permission_id == Permission.permission_id)
                    .filter(RolePermission.role_id == user.role_id, Permission.permission_name == permission_name)
                ).scalar_one_or_none()
                if role_perm:
                    return True

                return False
        except Exception:
            return False

    def _can_perform(self, permission_name: str, root_only: bool = False) -> bool:
        """
        Validates if the user is allowed to perform an action based on their role.
        Enforces strict hardcoded defaults for standard roles.
        """
        role_key = str(self.role_name).lower().strip()
        
        if role_key == 'root':
            return True
        if root_only:
            return False

        # Define explicit permission whitelist per role
        # Roles are checked case-insensitively.
        whitelist = {
            'administrator': {
                'CREATE USER', 'EDIT USER', 'SHOW USER',
                'SHOW RECIPE', 'START MACHINE', 'STOP MACHINE', 
                'CREATE BACKUP', 'SEND BACKUP', 
                'SHOW RECIPE REPORTS', 'CREATE RECIPE REPORTS', 
                'SHOW AUDIT REPORTS', 'CREATE AUDIT REPORTS', 'EXPORT AUDIT'
            },
            'admin': {  # Alias for administrator
                'CREATE USER', 'EDIT USER', 'SHOW USER',
                'SHOW RECIPE', 'START MACHINE', 'STOP MACHINE', 
                'CREATE BACKUP', 'SEND BACKUP', 
                'SHOW RECIPE REPORTS', 'CREATE RECIPE REPORTS', 
                'SHOW AUDIT REPORTS', 'CREATE AUDIT REPORTS', 'EXPORT AUDIT'
            },
            'supervisor': {
                'SHOW RECIPE', 'START MACHINE', 'STOP MACHINE',
                'SHOW RECIPE REPORTS', 'CREATE RECIPE REPORTS', 
                'SHOW AUDIT REPORTS', 'CREATE AUDIT REPORTS', 'EXPORT AUDIT'
            },
            'operator': {
                'SHOW RECIPE', 'START MACHINE', 'STOP MACHINE',
                'SHOW RECIPE REPORTS', 'CREATE RECIPE REPORTS'
            }
        }

        if role_key in whitelist:
            return permission_name.upper() in whitelist[role_key]

        # Fallback to DB check ONLY for non-standard, custom roles
        return self._has_permission(permission_name)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top Toolbar
        self.toolbar = TopToolbar(self.user_name, self.role_name, self.user_id, self)
        layout.addWidget(self.toolbar)

        # Content area: NavBar + Pages
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Navigation Bar
        self.nav_bar = NavBar(self.user_id, self.user_name, self)
        self.nav_bar.navigate_home.connect(lambda: self._switch_page(0))
        self.nav_bar.navigate_recipes.connect(lambda: self._switch_page(1))
        self.nav_bar.navigate_users.connect(lambda: self._switch_page(2))
        self.nav_bar.navigate_audit_trail.connect(lambda: self._switch_page(3))
        self.nav_bar.navigate_setting.connect(lambda: self._switch_page(4))
        self.nav_bar.navigate_user_policy.connect(lambda: self._switch_page(5))
        self.nav_bar.request_logout.connect(self._on_logout)
        self.nav_bar.request_exit.connect(self._on_exit_requested)
        content_layout.addWidget(self.nav_bar)

        # Stacked Widget for pages
        self.stack = QStackedWidget()

        self.home_page = HomePage(self)
        self.recipe_page = RecipePage(self.user_id, self)
        self.users_page = UsersPage(self.user_id, self.role_id, self)
        self.audit_trail_page = AuditTrailPage(self.user_id, self)
        self.setting_page = SettingPage(self)
        self.user_policy_page = UserPolicyPage(self)

        self.home_page.main_window = self
        self.recipe_page.main_window = self
        self.users_page.main_window = self
        self.audit_trail_page.main_window = self
        # Parent relation is already set by passing self in constructor, but set it explicitly just in case
        self.setting_page.main_window = self
        self.user_policy_page.main_window = self

        self.stack.addWidget(self.home_page)       # Index 0
        self.stack.addWidget(self.recipe_page)      # Index 1
        self.stack.addWidget(self.users_page)       # Index 2
        self.stack.addWidget(self.audit_trail_page) # Index 3
        self.stack.addWidget(self.setting_page)     # Index 4
        self.stack.addWidget(self.user_policy_page) # Index 5

        content_layout.addWidget(self.stack)
        layout.addLayout(content_layout)

        # Start on Home page
        self.stack.setCurrentIndex(0)

    def _setup_session_timer(self):
        """Periodic session validation for auto-logout compliance."""
        self.session_timer = QTimer(self)
        self.session_timer.timeout.connect(self._check_session)
        self.session_timer.start(1000)  # Check every 1 second for UI updates

    def _check_session(self):
        """
        Check if the user has been idle longer than the timeout.
        Updates the toolbar timer and only extends the session in DB periodically.
        """
        try:
            now = datetime.utcnow()
            idle_seconds = (now - self._last_user_activity).total_seconds()
            idle_minutes = idle_seconds / 60.0
            
            # Update toolbar timer
            if hasattr(self, 'toolbar'):
                self.toolbar.update_inactivity_timer(idle_seconds, settings.AUTH_SESSION_TIMEOUT_MINUTES * 60)

            if idle_minutes >= settings.AUTH_SESSION_TIMEOUT_MINUTES:
                # User is idle — expire the session
                self.session_timer.stop()
                try:
                    with get_session() as session:
                        auth_service = AuthService(session)
                        auth_session = auth_service.session_repo.get_by_id(self.session_id)
                        if auth_session and auth_session.is_active:
                            auth_session.is_active = False
                            audit_repo = AuditRepository(session)
                            audit_repo.create_audit(
                                user_id=self.user_id,
                                action_type=AuditActionType.LOG_OUT,
                                location_screen="System Background",
                                reason="Automatic logout due to user inactivity."
                            )
                            session.flush()
                except Exception as e:
                    print(f"[Session check] Error invalidating idle session: {e}")

                QMessageBox.warning(
                    self, "Session Expired",
                    "Your session has expired due to inactivity.\nPlease log in again."
                )
                self.request_logout.emit()
            else:
                # Periodic session extend in DB (every 30 seconds of activity)
                now_ts = int(now.timestamp())
                if not hasattr(self, '_last_db_extend'):
                    self._last_db_extend = 0
                
                # Only attempt DB write every 30s to reduce lock contention
                if now_ts - self._last_db_extend >= 30:
                    try:
                        with get_session() as session:
                            auth_session = AuthService(session).session_repo.get_by_id(self.session_id)
                            if auth_session and auth_session.is_active:
                                auth_session.last_activity = self._last_user_activity
                                auth_session.expires_at = self._last_user_activity + timedelta(
                                    minutes=settings.AUTH_SESSION_TIMEOUT_MINUTES
                                )
                                session.flush()
                                self._last_db_extend = now_ts
                            else:
                                self.session_timer.stop()
                                QMessageBox.warning(
                                    self, "Session Invalid",
                                    "Your session is no longer valid.\nPlease log in again."
                                )
                                self.request_logout.emit()
                    except Exception as e:
                        # Silently skip if DB is locked; another attempt will occur soon
                        if "locked" not in str(e).lower():
                            print(f"[Session check] Error extending session: {e}")
        except Exception:
            pass

    def _switch_page(self, index):
        """Switch to a page with role-based access control."""
        page_names = ["Home", "Batches", "Users", "Audit Trail", "Setting", "User Policy"]

        # Users page
        if index == 2:
            if not self._can_perform('SHOW USER'):
                QMessageBox.warning(self, "Access Denied", "You do not have permission to view the Users page.")
                return

        # Batches page
        if index == 1:
            if not self._can_perform('SHOW RECIPE'):
                # Handle cases where they can see reports but maybe not the config?
                # For Operator, we'll allow access but they will be restricted inside.
                if self.role_name != 'operator':
                    QMessageBox.warning(self, "Access Denied", "You do not have permission to view the Batches page.")
                    return

        # Audit Trail page
        if index == 3:
            if not self._can_perform('SHOW AUDIT REPORTS'):
                QMessageBox.warning(self, "Access Denied", "You do not have permission to view the Audit Trail page.")
                return

        # Setting page
        if index == 4:
            if not self._can_perform('EDIT RECIPE'):
                QMessageBox.warning(self, "Access Denied", "You do not have permission to view the Settings page.")
                return

        # User Policy page
        if index == 5:
            role_key = str(self.role_name).lower().strip()
            if role_key not in ['root', 'admin', 'administrator']:
                QMessageBox.warning(self, "Access Denied", "You do not have permission to view the User Policy page.")
                return

        self.stack.setCurrentIndex(index)

        # Load data on page entry
        if index == 1:
            self.recipe_page.load_data()
        elif index == 2:
            self.users_page.load_data()
        elif index == 3:
            self.audit_trail_page.load_data()

        # Log navigation
        try:
            with get_session() as session:
                audit_repo = AuditRepository(session)
                audit_repo.create_audit(
                    user_id=self.user_id,
                    action_type=AuditActionType.NAVIGATE,
                    location_screen=page_names[index],
                    reason=f"Navigated to {page_names[index]}"
                )
                session.flush()
        except Exception:
            pass  # Non-critical

    def _on_logout(self):
        """Handle logout: invalidate session, emit signal."""
        try:
            # Remove event filter
            QApplication.instance().removeEventFilter(self)
            with get_session() as session:
                auth_service = AuthService(session)
                auth_service.logout(self.session_id)
        except Exception:
            pass
        self.session_timer.stop()
        self.request_logout.emit()

    def _is_machine_running(self) -> bool:
        """
        Determine whether machine/pipeline is currently running.
        Uses both UI state and global Redis flag.
        """
        try:
            if getattr(self.home_page, "is_running", False):
                return True
        except Exception:
            pass

        try:
            r = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB
            )
            return r.get(settings.START_PIPELINE_KEY) == b"true"
        except Exception:
            return False

    def _on_exit_requested(self):
        """
        Exit application safely:
        - block if machine is running
        - otherwise logout and request graceful full shutdown
        """
        if self._is_machine_running():
            QMessageBox.warning(
                self,
                "Machine Running",
                "Cannot exit while machine is running.\nPlease stop the machine first."
            )
            return
        self.force_app_shutdown()

    def force_app_shutdown(self):
        """
        Full shutdown path used by Exit and app-close events.
        Does not check machine state; caller decides whether checks are needed.
        """
        if self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True

        try:
            # Signal process-level shutdown for non-UI worker threads.
            r = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB
            )
            r.set(settings.START_PIPELINE_KEY, "false")
            r.set(settings.SHUTDOWN_REQUEST_KEY, "true")
        except Exception:
            pass

        # Stop local UI polling timer before quit.
        try:
            if hasattr(self, "home_page") and self.home_page:
                self.home_page.persist_all_settings()
            if hasattr(self.home_page, "poll_timer") and self.home_page.poll_timer:
                self.home_page.poll_timer.stop()
        except Exception:
            pass

    def closeEvent(self, event):
        """Handle window close event (e.g. clicking the X button)."""
        if self._is_machine_running():
            QMessageBox.warning(
                self,
                "Machine Running",
                "Cannot exit while machine is running.\nPlease stop the machine first."
            )
            event.ignore()
            return
        self.force_app_shutdown()
        event.accept()

        # Log out current session in DB.
        try:
            QApplication.instance().removeEventFilter(self)
            with get_session() as session:
                AuthService(session).logout(self.session_id)
        except Exception:
            pass

        self.session_timer.stop()
        if hasattr(self, '_mismatch_timer'):
            self._mismatch_timer.stop()
        QApplication.instance().quit()

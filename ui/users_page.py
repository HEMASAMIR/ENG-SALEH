"""
BLUE SQUARE — Users Page
Full user management: list, add, edit, delete, give permissions.
Restricted to root users. All actions require re-authorization and are audited.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QMessageBox, QSizePolicy, QApplication
)
from PySide6.QtCore import Qt, QMetaObject, Signal, QThread
from PySide6.QtGui import QFont

from core.database import get_session
from repositories.user_repository import UserRepository
from repositories.role_repository import RoleRepository
from repositories.audit_repository import AuditRepository
from services.auth_service import AuthService
from models.audit.audit_trail import AuditActionType
from ui.auth_dialog import AuthDialog
from ui.user_dialog import UserDialog
from ui.permissions_dialog import PermissionsDialog


class UserActionWorker(QThread):
    """Worker thread for user management operations to prevent UI freezing."""
    finished = Signal(bool, str)

    def __init__(self, action_type, user_data, actor_id, actor_role_id, session_id=None):
        super().__init__()
        self.action_type = action_type
        self.user_data = user_data
        self.actor_id = actor_id
        self.actor_role_id = actor_role_id
        self.session_id = session_id

    def run(self):
        try:
            from core.database import get_session
            from services.auth_service import AuthService
            with get_session() as session:
                auth_service = AuthService(session)
                
                if self.action_type == 'ADD':
                    user, msg = auth_service.register_user(self.actor_id, self.user_data)
                    self.finished.emit(user is not None, msg)
                
                else:
                    # ROOT PROTECTION: Only root (ID 1) can modify/delete root (ID 1).
                    target_id = self.user_data.get('user_id') or self.user_data.get('target_id')
                    
                    # Hierarchy check prep
                    rank_map = {1: 0, 2: 1, 3: 2, 4: 3}
                    actor_rank = rank_map.get(self.actor_role_id, 99) if self.actor_role_id else 99
                    
                    if target_id == 1 and self.actor_id != 1:
                        self.finished.emit(False, "Security Violation: Only root can modify the root account.")
                        return

                    if self.action_type == 'EDIT':
                        user_id = self.user_data.get('user_id')
                        user = auth_service.user_repo.get_by_id(user_id)
                        if not user:
                            self.finished.emit(False, "User not found.")
                            return
                        
                        target_rank = rank_map.get(user.role_id, 99)
                        if target_rank < actor_rank:
                            self.finished.emit(False, "Security Violation: Cannot modify a higher-rank user.")
                            return
                        
                        user.full_name = self.user_data.get('full_name')
                        user.role_id = self.user_data.get('role_id')
                        
                        # Reset failed attempts if enabling a disabled user
                        was_disabled = user.is_disabled
                        user.is_disabled = self.user_data.get('is_disabled', False)
                        if was_disabled and not user.is_disabled:
                            user.failed_attempts = 0
                        
                        # Optional password change
                        pwd = self.user_data.get('password')
                        if pwd:
                            errors = auth_service.get_password_errors(pwd)
                            if errors:
                                self.finished.emit(False, "Password requirements not met.")
                                return
                            user.password_hash = auth_service._hash_password(pwd)
                        
                        # Audit user edit profile
                        from repositories.audit_repository import AuditRepository
                        audit_repo = AuditRepository(session)
                        audit_repo.create_audit(
                            user_id=self.actor_id,
                            action_type=AuditActionType.EDIT,
                            location_screen="User Administration",
                            reason=f"Edited user profile: {user.user_name}. Reason: {self.user_data.get('reason', '')}"
                        )
                        
                        session.flush()
                        self.finished.emit(True, "User updated successfully.")
                    
                    elif self.action_type == 'DELETE':
                        # Audit deletion first
                        from repositories.audit_repository import AuditRepository
                        target_id = self.user_data.get('target_id')
                        target_name = self.user_data.get('target_username')
                        audit_repo = AuditRepository(session)
                        audit_repo.create_audit(
                            user_id=self.actor_id,
                            action_type=AuditActionType.DELETE,
                            location_screen="User Administration",
                            reason=f"Permanently deleted user: {target_name}"
                        )
                        
                        success = auth_service.user_repo.delete_soft(target_id)
                        self.finished.emit(success, "User deleted." if success else "Delete failed.")
                    
                    elif self.action_type == 'PERMISSIONS':
                        user_id = self.user_data.get('user_id')
                        perm_ids = self.user_data.get('perm_ids')
                        
                        # Get user and clear current permissions
                        user = auth_service.user_repo.get_by_id(user_id)
                        if not user:
                            self.finished.emit(False, "User not found.")
                            return
                        
                        # This is simplified for the worker
                        auth_service.user_repo.update_permissions(user_id, perm_ids)
                        
                        from repositories.audit_repository import AuditRepository
                        audit_repo = AuditRepository(session)
                        audit_repo.create_audit(
                            user_id=self.actor_id,
                            action_type=AuditActionType.EDIT,
                            location_screen="Permissions Management",
                            reason=f"Updated permissions for user ID {user_id}"
                        )
                    session.flush()
                    self.finished.emit(True, "Permissions updated.")
        except Exception as e:
            self.finished.emit(False, str(e))


class UsersPage(QWidget):
    """
    Users management page — root access only.
    Shows all users, allows CRUD and permission management.
    """

    def __init__(self, user_id: int, role_id: int, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.role_id = role_id
        self.setObjectName("users_page")
        self._load_qss()
        self._build_ui()

    def _load_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'users.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Page Title
        title = QLabel("USER MANAGEMENT")
        title.setObjectName("page_title")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        layout.addWidget(title)

        # Action Bar
        action_bar = QFrame()
        action_bar.setObjectName("action_bar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(12, 8, 12, 8)
        action_layout.setSpacing(12)

        self.add_btn = QPushButton("Add User")
        self.add_btn.setObjectName("users_button")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._on_add_user)
        self.add_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_layout.addWidget(self.add_btn)

        self.edit_btn = QPushButton("Edit User")
        self.edit_btn.setObjectName("users_button")
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.clicked.connect(self._on_edit_user)
        self.edit_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_layout.addWidget(self.edit_btn)

        self.delete_btn = QPushButton("Delete User")
        self.delete_btn.setObjectName("users_button")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.clicked.connect(self._on_delete_user)
        self.delete_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_layout.addWidget(self.delete_btn)



        layout.addWidget(action_bar)

        # Users Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        headers = [
            "ID", "Username", "Full Name", "Role", "Last Login",
            "Password Changed", "Failed Attempts", "Disabled", "Created By", "Created At"
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout.addWidget(self.table)
        
    def showEvent(self, event):
        """Update button visibility/enabled state based on permissions."""
        super().showEvent(event)
        if hasattr(self, 'main_window'):
            mw = self.main_window
            
            # Use central permission engine for consistency
            self.add_btn.setEnabled(mw._can_perform('CREATE USER'))
            self.edit_btn.setEnabled(mw._can_perform('EDIT USER'))
            self.delete_btn.setVisible(mw._can_perform('DELETE USER', root_only=True))
            


    def _force_refresh(self):
        """Force the table to refresh and update the display."""
        self.load_data()
        self.table.viewport().update()
        self.table.update()
        QApplication.processEvents()  # Force UI to process pending events

    def load_data(self):
        """Load all users into the table."""
        try:
            # Clear existing data
            self.table.setRowCount(0)
            self.table.clearContents()
            
            with get_session() as session:
                user_repo = UserRepository(session)
                role_repo = RoleRepository(session)
                users = user_repo.all()
                roles_cache = {r.role_id: r.role_name for r in role_repo.all()}
                users_cache = {u.id: u.user_name for u in users}

                self.table.setRowCount(len(users))
                for row, user in enumerate(users):
                    self.table.setItem(row, 0, QTableWidgetItem(str(user.id)))
                    self.table.setItem(row, 1, QTableWidgetItem(user.user_name))
                    self.table.setItem(row, 2, QTableWidgetItem(user.full_name))
                    self.table.setItem(row, 3, QTableWidgetItem(roles_cache.get(user.role_id, "—")))
                    self.table.setItem(row, 4, QTableWidgetItem(
                        user.last_login.strftime("%Y-%m-%d %H:%M") if user.last_login else "—"
                    ))
                    self.table.setItem(row, 5, QTableWidgetItem(
                        user.password_last_change.strftime("%Y-%m-%d %H:%M") if user.password_last_change else "—"
                    ))
                    self.table.setItem(row, 6, QTableWidgetItem(str(user.failed_attempts or 0)))
                    self.table.setItem(row, 7, QTableWidgetItem("Yes" if user.is_disabled else "No"))
                    self.table.setItem(row, 8, QTableWidgetItem(
                        users_cache.get(user.created_by, "—") if user.created_by else "—"
                    ))
                    self.table.setItem(row, 9, QTableWidgetItem(
                        user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "—"
                    ))

                # Table uses Stretch mode — no manual resize needed
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load users:\n{str(e)}")

    def _get_selected_user_id(self):
        """Get the user ID from the selected row."""
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Selection", "Please select a user first.")
            return None
        row = selected[0].row()
        return int(self.table.item(row, 0).text())


    def _on_add_user(self):
        """Register a new user via background worker."""
        actor_id = self.user_id

        dlg = UserDialog(self, mode='add')
        if dlg.exec() == UserDialog.Accepted and dlg.result_data:
            # Re-authorize before adding
            auth_dlg = AuthDialog("Authorization Required", "Re-authorize to add new user.", self)
            if auth_dlg.exec() == AuthDialog.Accepted:
                self.add_btn.setEnabled(False)
                self.add_btn.setText("Processing...")
                
                self.worker = UserActionWorker('ADD', dlg.result_data, self.user_id, self.role_id)
                self.worker.finished.connect(self._on_action_finished)
                self.worker.start()

    def _on_edit_user(self):
        """Update existing user via background worker."""
        user_id = self._get_selected_user_id()
        if user_id is None:
            return

        actor_id = self.user_id
        # Rank mapping: lower is higher priority
        rank_map = {1: 0, 2: 1, 3: 2, 4: 3}
        actor_rank = rank_map.get(self.role_id, 99)
        
        # ROOT PROTECTION: ID 1 is the immutable root.
        if user_id == 1 and actor_id != 1:
            QMessageBox.warning(self, "Security Restriction", "Root account can only be edited by the root user.")
            return

        try:
            with get_session() as session:
                user_repo = UserRepository(session)
                target_user = user_repo.get_by_id(user_id)
                if not target_user:
                    QMessageBox.warning(self, "Not Found", "User not found.")
                    return
                
                target_rank = rank_map.get(target_user.role_id, 99)
                if target_rank < actor_rank:
                    QMessageBox.warning(self, "Security Restriction", "You cannot edit a user with higher permissions than yours.")
                    return

                dlg = UserDialog(self, mode='edit', user_data={
                    'user_name': target_user.user_name,
                    'full_name': target_user.full_name,
                    'role_id': target_user.role_id,
                    'is_disabled': target_user.is_disabled or False
                })

                if dlg.exec() == UserDialog.Accepted and dlg.result_data:
                    auth_dlg = AuthDialog("Authorization Required", "Re-authorize to edit user profile.", self)
                    if auth_dlg.exec() == AuthDialog.Accepted:
                        self.edit_btn.setEnabled(False)
                        self.edit_btn.setText("Processing...")
                        
                        data = dlg.result_data
                        data['user_id'] = user_id
                        
                        self.worker = UserActionWorker('EDIT', data, self.user_id, self.role_id)
                        self.worker.finished.connect(self._on_action_finished)
                        self.worker.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to prepare edit:\n{str(e)}")

    def _on_delete_user(self):
        """Remove user via background worker."""
        user_id = self._get_selected_user_id()
        if user_id is None:
            return

        actor_id = self.user_id
        
        if user_id == 1:
            QMessageBox.critical(self, "Error", "The root account cannot be deleted.")
            return
            
        # Hierarchical check
        rank_map = {1: 0, 2: 1, 3: 2, 4: 3}
        actor_rank = rank_map.get(self.role_id, 99)
        try:
            with get_session() as session:
                target_user = UserRepository(session).get_by_id(user_id)
                if target_user and rank_map.get(target_user.role_id, 99) < actor_rank:
                    QMessageBox.warning(self, "Security Restriction", "You cannot delete a user with higher permissions than yours.")
                    return
        except: pass

        confirm = QMessageBox.question(
            self, "Confirm Delete",
            "Are you sure you want to delete this user?\nThis action will be logged.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        auth_dlg = AuthDialog("Authorization Required", "Re-authorize to delete user.", self)
        if auth_dlg.exec() == AuthDialog.Accepted:
            self.delete_btn.setEnabled(False)
            self.delete_btn.setText("Deleting...")
            
            # Simple metadata for worker
            data = {'target_id': user_id, 'target_username': "Unknown"}
            try:
                with get_session() as session:
                    u = UserRepository(session).get_by_id(user_id)
                    if u: data['target_username'] = u.user_name
            except: pass
            
            self.worker = UserActionWorker('DELETE', data, self.user_id, self.role_id)
            self.worker.finished.connect(self._on_action_finished)
            self.worker.start()

    def _on_permissions(self):
        """Update permissions via background worker."""
        user_id = self._get_selected_user_id()
        if user_id is None:
            return
        
        row = self.table.currentRow()
        # Hierarchical check
        rank_map = {1: 0, 2: 1, 3: 2, 4: 3}
        actor_rank = rank_map.get(self.role_id, 99)
        try:
            with get_session() as session:
                target_user = UserRepository(session).get_by_id(user_id)
                if target_user and rank_map.get(target_user.role_id, 99) < actor_rank:
                    QMessageBox.warning(self, "Security Restriction", "You cannot manage permissions for a user with higher permissions than yours.")
                    return
        except: pass
        
        row = self.table.currentRow()
        username = self.table.item(row, 1).text()

        dlg = PermissionsDialog(user_id, username, self)
        if dlg.exec() == PermissionsDialog.Accepted:
            self.perm_btn.setEnabled(False)
            self.perm_btn.setText("Updating...")
            
            data = {'user_id': user_id, 'perm_ids': dlg.selected_permission_ids()}
            self.worker = UserActionWorker('PERMISSIONS', data, self.user_id, self.role_id)
            self.worker.finished.connect(self._on_action_finished)
            self.worker.start()

    def _on_action_finished(self, success, message):
        """Restore UI and show result of background operation."""
        # Restore button states
        self.add_btn.setEnabled(True)
        self.add_btn.setText("Add User")
        self.edit_btn.setEnabled(True)
        self.edit_btn.setText("Edit User")
        self.delete_btn.setEnabled(True)
        self.delete_btn.setText("Delete User")

        
        if success:
            QMessageBox.information(self, "Success", message)
            self.load_data()
        else:
            QMessageBox.critical(self, "Error", message)

    def _on_give_permissions(self):
        # Deprecated: replaced by _on_permissions via background worker
        self._on_permissions()

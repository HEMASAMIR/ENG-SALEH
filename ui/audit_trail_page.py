"""
BLUE SQUARE — Audit Trail Page
Full audit trail viewing and export with filtering.
Restricted to root and admin users.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QMessageBox,
    QLineEdit, QComboBox, QDateEdit, QSizePolicy, QFileDialog
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont

from core.database import get_session
from repositories.audit_repository import AuditRepository
from repositories.user_repository import UserRepository
from services.report_service import ReportService
from services.audit_log_service import AuditLogService
from models.audit.audit_trail import AuditActionType
from ui.auth_dialog import AuthDialog

import json
from datetime import datetime, timedelta


class AuditTrailPage(QWidget):
    """
    Audit Trail page — displays all audit entries and allows PDF export.
    Accessible to root and admin users only.
    Provides filters for User Name, Action, and Timestamp Range.
    """

    def __init__(self, user_id: int, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.setObjectName("audit_trail_page")
        self._load_qss()
        self._build_ui()

    def _load_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'audit_trail.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Page Title
        title = QLabel("AUDIT TRAIL")
        title.setObjectName("page_title")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        layout.addWidget(title)

        # Filter Bar
        filter_frame = QFrame()
        filter_frame.setObjectName("action_bar")
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.setSpacing(10)

        # User Name filter
        user_lbl = QLabel("User:")
        user_lbl.setStyleSheet("color: #cccccc; font-weight: 600; font-size: 11px;")
        filter_layout.addWidget(user_lbl)
        self.filter_user = QLineEdit()
        self.filter_user.setPlaceholderText("Filter by username...")
        self.filter_user.setMaximumWidth(150)
        filter_layout.addWidget(self.filter_user)

        # Action filter
        action_lbl = QLabel("Action:")
        action_lbl.setStyleSheet("color: #cccccc; font-weight: 600; font-size: 11px;")
        filter_layout.addWidget(action_lbl)
        self.filter_action = QComboBox()
        self.filter_action.addItem("All", None)
        for action in AuditActionType:
            self.filter_action.addItem(action.value, action)
        self.filter_action.setMaximumWidth(150)
        filter_layout.addWidget(self.filter_action)

        # Start Date filter
        start_lbl = QLabel("From:")
        start_lbl.setStyleSheet("color: #cccccc; font-weight: 600; font-size: 11px;")
        filter_layout.addWidget(start_lbl)
        self.filter_start_date = QDateEdit()
        self.filter_start_date.setCalendarPopup(True)
        self.filter_start_date.setDate(QDate.currentDate().addDays(-30))
        self.filter_start_date.setDisplayFormat("yyyy-MM-dd")
        self.filter_start_date.setMaximumWidth(130)
        filter_layout.addWidget(self.filter_start_date)

        # End Date filter
        end_lbl = QLabel("To:")
        end_lbl.setStyleSheet("color: #cccccc; font-weight: 600; font-size: 11px;")
        filter_layout.addWidget(end_lbl)
        self.filter_end_date = QDateEdit()
        self.filter_end_date.setCalendarPopup(True)
        self.filter_end_date.setDate(QDate.currentDate())
        self.filter_end_date.setDisplayFormat("yyyy-MM-dd")
        self.filter_end_date.setMaximumWidth(130)
        filter_layout.addWidget(self.filter_end_date)

        # Apply Filter Button
        apply_btn = QPushButton("🔍  Apply")
        apply_btn.setObjectName("refresh_btn")
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.clicked.connect(self._on_apply_filter)
        filter_layout.addWidget(apply_btn)

        # Clear Filter Button
        clear_btn = QPushButton("✕  Clear")
        clear_btn.setObjectName("refresh_btn")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear_filter)
        filter_layout.addWidget(clear_btn)

        filter_layout.addStretch()
        layout.addWidget(filter_frame)

        # Action Bar
        action_bar = QFrame()
        action_bar.setObjectName("action_bar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(12, 8, 12, 8)
        action_layout.setSpacing(8)

        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.setObjectName("refresh_btn")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self.load_data)
        action_layout.addWidget(refresh_btn)

        export_btn = QPushButton("📄  Export Audit Trail (PDF)")
        export_btn.setObjectName("export_audit_btn")
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.clicked.connect(self._on_export)
        action_layout.addWidget(export_btn)

        action_layout.addStretch()
        layout.addWidget(action_bar)

        # Audit Trail Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        headers = [
            "Timestamp", "USERNAME", "Action",
            "Location Screen", "Old Value", "New Value", "Action", "Reason of Action"
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout.addWidget(self.table)

    def load_data(self):
        """Load all audit trail entries into the table (unfiltered)."""
        try:
            with get_session() as session:
                audit_repo = AuditRepository(session)
                user_repo = UserRepository(session)

                audits = audit_repo.list_all(limit=500)
                users = user_repo.all()
                users_cache = {u.id: u.user_name for u in users}

                self._populate_table(audits, users_cache)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load audit trail:\n{str(e)}")

    def _on_apply_filter(self):
        """Apply filters and reload the table."""
        try:
            with get_session() as session:
                audit_log_service = AuditLogService(session)
                user_repo = UserRepository(session)
                users = user_repo.all()
                users_cache = {u.id: u.user_name for u in users}
                users_by_name = [(u.user_name.lower(), u.id) for u in users]

                # Parse filters
                user_name_filter = self.filter_user.text().strip().lower()
                action_filter = self.filter_action.currentData()

                start_qdate = self.filter_start_date.date()
                start_date = datetime(
                    start_qdate.year(), start_qdate.month(), start_qdate.day()
                )
                end_qdate = self.filter_end_date.date()
                end_date = datetime(
                    end_qdate.year(), end_qdate.month(), end_qdate.day(),
                    23, 59, 59
                )

                # Resolve user_name to user_id for filtering
                filter_user_ids = None
                if user_name_filter:
                    # Include all users that match the partial user-name filter.
                    matching_ids = [uid for name, uid in users_by_name if user_name_filter in name]
                    if matching_ids:
                        filter_user_ids = matching_ids
                    else:
                        # No matching user — show empty
                        self._populate_table([], users_cache)
                        return

                audits = audit_log_service.search_logs(
                    user_ids=filter_user_ids,
                    action_type=action_filter,
                    start_date=start_date,
                    end_date=end_date,
                    limit=500
                )

                self._populate_table(audits, users_cache)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply filter:\n{str(e)}")

    def _on_clear_filter(self):
        """Clear all filters and reload."""
        self.filter_user.clear()
        self.filter_action.setCurrentIndex(0)
        self.filter_start_date.setDate(QDate.currentDate().addDays(-30))
        self.filter_end_date.setDate(QDate.currentDate())
        self.load_data()

    def _populate_table(self, audits, users_cache):
        """Populate the table with audit entries."""
        self.table.setRowCount(len(audits))
        for row, audit in enumerate(audits):
            self.table.setItem(row, 0, QTableWidgetItem(
                audit.audit_timestamp.strftime("%Y-%m-%d %H:%M:%S") if audit.audit_timestamp else "—"
            ))
            self.table.setItem(row, 1, QTableWidgetItem(users_cache.get(audit.user_id, "—")))
            self.table.setItem(row, 2, QTableWidgetItem(
                audit.action_type.value if audit.action_type else "—"
            ))
            self.table.setItem(row, 3, QTableWidgetItem(audit.location_screen or "—"))

            # Format JSONB values
            old_val = ""
            if audit.old_value:
                old_val = json.dumps(audit.old_value, default=str)[:80] if isinstance(audit.old_value, dict) else str(audit.old_value)[:80]
            self.table.setItem(row, 4, QTableWidgetItem(old_val or "—"))

            new_val = ""
            if audit.new_value:
                new_val = json.dumps(audit.new_value, default=str)[:80] if isinstance(audit.new_value, dict) else str(audit.new_value)[:80]
            self.table.setItem(row, 5, QTableWidgetItem(new_val or "—"))

            self.table.setItem(row, 6, QTableWidgetItem(
                (audit.reason[:60] + "...") if audit.reason and len(audit.reason) > 60 else (audit.reason or "—")
            ))
            self.table.setItem(row, 7, QTableWidgetItem(audit.signature_meaning or "—"))

    def _on_export(self):
        """Export the audit trail to PDF."""
        try:
            with get_session() as session:
                # Get user name from current session
                user_repo = UserRepository(session)
                actor = user_repo.get_by_id(self.user_id)
                actor_name = actor.user_name if actor else "Unknown"

                # Reuse current UI filters so export matches selected options.
                users = user_repo.all()
                users_by_name = [(u.user_name.lower(), u.id) for u in users]
                user_name_filter = self.filter_user.text().strip().lower()
                action_filter = self.filter_action.currentData()

                start_qdate = self.filter_start_date.date()
                start = datetime(
                    start_qdate.year(), start_qdate.month(), start_qdate.day()
                )
                end_qdate = self.filter_end_date.date()
                end = datetime(
                    end_qdate.year(), end_qdate.month(), end_qdate.day(),
                    23, 59, 59
                )

                filter_user_ids = None
                if user_name_filter:
                    matching_ids = [
                        uid for name, uid in users_by_name
                        if user_name_filter in name
                    ]
                    if matching_ids:
                        filter_user_ids = matching_ids
                    else:
                        QMessageBox.warning(
                            self, "Export",
                            "No matching user for the selected User Name filter."
                        )
                        return

                report_service = ReportService(session)
                
                # Prompt for save location
                default_filename = f"Audit_Trail_{datetime.now().strftime('%Y%H%M%S')}.pdf"
                file_path, _ = QFileDialog.getSaveFileName(
                    self, "Save Audit Trail PDF", default_filename, "PDF Files (*.pdf)"
                )
                
                if not file_path:
                    return

                actual_path = report_service.generate_audit_report(
                    actor_name,
                    start,
                    end,
                    user_ids=filter_user_ids,
                    action_type=action_filter,
                    target_path=file_path
                )

                # Log the export action
                audit_repo = AuditRepository(session)
                audit_repo.create_audit(
                    user_id=self.user_id,
                    action_type=AuditActionType.EXPORT,
                    location_screen="Audit Trail Page",
                    reason=f"Exported Audit Trail to PDF: {os.path.basename(actual_path)}"
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
            QMessageBox.critical(self, "Error", f"Failed to export audit trail:\n{str(e)}")


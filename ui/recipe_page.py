"""
BLUE SQUARE — Recipe Page
Full recipe management: list, create, edit, delete, version, export.
Restricted to root and admin users.
All actions require re-authorization and are audited.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import date, datetime, timezone
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QMessageBox,
    QGridLayout, QGroupBox, QSplitter, QFileDialog, QSizePolicy, QDialog
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont

from core.database import get_session
from repositories.recipe_repository import RecipeRepository
from repositories.recipe_parameter_repository import RecipeParameterRepository
from repositories.audit_repository import AuditRepository
from models.recipe.recipe import RecipeStatus
from models.audit.audit_trail import AuditActionType
from services.recipe_service import RecipeService
from services.report_service import ReportService
from ui.auth_dialog import AuthDialog
from ui.recipe_dialog import RecipeDialog


# Canonical parameter names for recipe fields stored in recipe_parameters
PARAM_MEDICINE_NAME = "medicine_name"
PARAM_BATCH_NUMBER = "batch_number"
PARAM_BATCH_SUPERVISOR = "batch_supervisor"
PARAM_BATCH_SIZE = "batch_size"
PARAM_BATCH_DATE = "batch_date"
PARAM_NUM_CARTONS = "num_cartons"
PARAM_WRONG_COUNT = "wrong_count"
PARAM_GOOD_COUNT = "good_count"
PARAM_TOTAL_COUNT = "total_count"


def _get_param_value(params, name, default=""):
    """Extract current_value from a parameter list by name."""
    for p in params:
        if p.parameter_name == name:
            val = p.current_value
            if isinstance(val, dict):
                return str(val.get("value", default))
            return str(val) if val is not None else default
    return default


class ActivateWorker(QThread):
    """
    Background worker for batch activation to prevent UI hangs.
    """
    res = Signal(bool, str)

    def __init__(self, user_id, recipe_id):
        super().__init__()
        self.user_id = user_id
        self.recipe_id = recipe_id

    def run(self):
        try:
            from core.database import get_session
            from services.recipe_service import RecipeService
            with get_session() as session:
                recipe_service = RecipeService(session)
                success = recipe_service.activate_recipe(self.user_id, self.recipe_id)
                self.res.emit(success, "Batch activated." if success else "Failed to find batch.")
        except Exception as e:
            self.res.emit(False, str(e))


class RecipeActionWorker(QThread):
    """
    Background worker for batch mutations (CREATE, EDIT, VERSION).
    """
    res = Signal(bool, str, int)

    def __init__(self, action_type, user_id, data):
        super().__init__()
        self.action_type = action_type  # 'CREATE', 'EDIT', 'VERSION'
        self.user_id = user_id
        self.data = data

    def run(self):
        try:
            from core.database import get_session
            from services.recipe_service import RecipeService
            from repositories.recipe_repository import RecipeRepository
            from repositories.recipe_parameter_repository import RecipeParameterRepository
            from repositories.audit_repository import AuditRepository
            from models.audit.audit_trail import AuditActionType
            
            with get_session() as session:
                recipe_service = RecipeService(session)
                recipe_repo = RecipeRepository(session)
                param_repo = RecipeParameterRepository(session)
                audit_repo = AuditRepository(session)
                
                if self.action_type == 'CREATE':
                    all_recipes = recipe_repo.all()
                    max_num = max([r.recipe_number for r in all_recipes], default=0) if all_recipes else 0
                    new_num = max_num + 1
                    
                    recipe = recipe_service.create_recipe(
                        self.user_id, new_num, self.data['parameters_data'], reason=self.data['reason']
                    )
                    # Automatically activate the created batch
                    recipe_service.activate_recipe(self.user_id, recipe.recipe_id)
                    self.res.emit(True, f"Batch #{new_num} (v1) created and activated successfully.", recipe.recipe_id)

                elif self.action_type == 'EDIT':
                    recipe_id = self.data['recipe_id']
                    param_map = self.data['param_map']
                    reason = self.data['reason']
                    
                    for pname, pvalue in param_map.items():
                        param = param_repo.get_by_name(recipe_id, pname)
                        if param:
                            old_val = param.current_value
                            param.current_value = {"value": pvalue}
                            audit_repo.create_audit(
                                user_id=self.user_id,
                                action_type=AuditActionType.EDIT,
                                location_screen="Batch Page",
                                old_value={"parameter": pname, "value": old_val},
                                new_value={"parameter": pname, "value": pvalue},
                                reason=f"Edited batch parameter: {pname}. Reason: {reason}"
                            )
                    session.flush()
                    self.res.emit(True, "Batch updated.", 0)

                elif self.action_type == 'VERSION':
                    recipe_id = self.data['recipe_id']
                    parameter_overrides = self.data['parameter_overrides']
                    reason = self.data['reason']
                    
                    recipe = recipe_repo.get_by_id(recipe_id)
                    new_recipe = recipe_service.create_new_version(
                        self.user_id, recipe.recipe_number, parameter_overrides, reason=reason
                    )
                    self.res.emit(True, f"Batch #{recipe.recipe_number} v{new_recipe.recipe_version} created.", 0)
                    
        except Exception as e:
            self.res.emit(False, str(e), 0)


class DeactivateWorker(QThread):
    """
    Background worker for batch deactivation to prevent UI hangs.
    """
    res = Signal(bool, str)

    def __init__(self, user_id, recipe_id, reason):
        super().__init__()
        self.user_id = user_id
        self.recipe_id = recipe_id
        self.reason = reason

    def run(self):
        try:
            from core.database import get_session
            from services.recipe_service import RecipeService
            import redis
            from core.config import settings
            
            # Stop the pipeline in redis immediately
            r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB)
            r.set(settings.START_PIPELINE_KEY, "false")
            
            with get_session() as session:
                recipe_service = RecipeService(session)
                success = recipe_service.deactivate_recipe(self.user_id, self.recipe_id, self.reason)
                self.res.emit(success, "Batch closed/deactivated." if success else "Failed to find active batch.")
        except Exception as e:
            self.res.emit(False, str(e))


class ReasonDialog(QDialog):
    """
    Simple modal dialog that requires user to enter a reason.
    """
    def __init__(self, title="Reason Required", label_text="Please enter reason:", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(400, 220)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.reason_text = ""
        self._build_ui(title, label_text)

    def _build_ui(self, title, label_text):
        from PySide6.QtWidgets import QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout
        from PySide6.QtGui import QFont
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel(title)
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet("color: #007acc;")
        layout.addWidget(header)

        lbl = QLabel(label_text)
        lbl.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        layout.addWidget(lbl)

        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText("Enter reason here...")
        layout.addWidget(self.reason_input)

        layout.addSpacing(6)

        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("Submit")
        ok_btn.setObjectName("accent_button")
        ok_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def _on_submit(self):
        text = self.reason_input.text().strip()
        if not text:
            QMessageBox.warning(self, "Validation", "Reason is required.")
            return
        self.reason_text = text
        self.accept()


class RecipePage(QWidget):
    """
    Recipe management page — root/admin access only.
    """

    def __init__(self, user_id: int, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.setObjectName("recipe_page")
        self._load_qss()
        self._build_ui()

    def _load_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'recipe.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Page Title
        title = QLabel("BATCH MANAGEMENT")
        title.setObjectName("page_title")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        layout.addWidget(title)

        # Splitter: Left = form + active recipe, Right = table
        splitter = QSplitter(Qt.Horizontal)

        # ---- Left Panel ----
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(12)

        # New Recipe Input Form
        form_frame = QFrame()
        form_frame.setObjectName("recipe_form")
        form_grid = QGridLayout(form_frame)
        form_grid.setSpacing(8)

        form_title = QLabel("New Batch")
        form_title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        form_title.setStyleSheet("color: #007acc;")
        form_grid.addWidget(form_title, 0, 0, 1, 2)

        row = 1
        form_grid.addWidget(self._form_label("Batch ID (Auto)"), row, 0)
        self.new_recipe_id = QLineEdit()
        self.new_recipe_id.setObjectName("recipe_input")
        self.new_recipe_id.setReadOnly(True)
        self.new_recipe_id.setPlaceholderText("Auto-generated")
        self.new_recipe_id.setEnabled(False)
        form_grid.addWidget(self.new_recipe_id, row, 1)

        row += 1
        form_grid.addWidget(self._form_label("Batch Date"), row, 0)
        self.new_batch_date = QLineEdit(str(date.today()))
        self.new_batch_date.setObjectName("recipe_input")
        self.new_batch_date.setReadOnly(True)
        self.new_batch_date.setEnabled(False)
        form_grid.addWidget(self.new_batch_date, row, 1)

        row += 1
        form_grid.addWidget(self._form_label("Medicine Name"), row, 0)
        self.new_medicine = QLineEdit()
        self.new_medicine.setObjectName("recipe_input")
        self.new_medicine.setPlaceholderText("Enter medicine name")
        form_grid.addWidget(self.new_medicine, row, 1)

        row += 1
        form_grid.addWidget(self._form_label("Batch Number"), row, 0)
        self.new_batch_num = QLineEdit()
        self.new_batch_num.setObjectName("recipe_input")
        self.new_batch_num.setPlaceholderText("Enter batch number")
        form_grid.addWidget(self.new_batch_num, row, 1)

        row += 1
        form_grid.addWidget(self._form_label("Batch Supervisor"), row, 0)
        self.new_supervisor = QLineEdit()
        self.new_supervisor.setObjectName("recipe_input")
        self.new_supervisor.setPlaceholderText("Enter supervisor")
        form_grid.addWidget(self.new_supervisor, row, 1)

        row += 1
        form_grid.addWidget(self._form_label("Reason"), row, 0)
        self.new_reason = QLineEdit()
        self.new_reason.setObjectName("recipe_input")
        self.new_reason.setPlaceholderText("Reason for creation (Audited)")
        form_grid.addWidget(self.new_reason, row, 1)

        row += 1
        # Create button
        self.create_btn = QPushButton("Create Batch")
        self.create_btn.setObjectName("success_button")
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.clicked.connect(self._on_create_recipe)
        self.create_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.create_btn.setMinimumHeight(40)
        form_grid.addWidget(self.create_btn, row, 0, 1, 2)

        left_layout.addWidget(form_frame)

        # Active Recipe Panel
        self.active_panel = QFrame()
        self.active_panel.setObjectName("active_recipe_panel")
        active_layout = QVBoxLayout(self.active_panel)
        active_layout.setSpacing(6)

        active_title = QLabel("CURRENT ACTIVE BATCH")
        active_title.setObjectName("active_recipe_title")
        active_title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        active_layout.addWidget(active_title)

        self.active_info_labels = {}
        for field in ["Batch ID", "Batch Date", "Medicine Name", "Batch Number",
                       "Batch Supervisor", "Wrong Count", "Good Count", "Total Count"]:
            row_layout = QHBoxLayout()
            name_lbl = QLabel(f"{field}:")
            name_lbl.setStyleSheet("color: #808080; font-weight: 600; font-size: 12px;")
            name_lbl.setFixedWidth(120)
            row_layout.addWidget(name_lbl)
            val_lbl = QLabel("—")
            val_lbl.setObjectName("active_recipe_value")
            row_layout.addWidget(val_lbl)
            row_layout.addStretch()
            active_layout.addLayout(row_layout)
            self.active_info_labels[field] = val_lbl

        self.close_batch_btn = QPushButton("Close Batch")
        self.close_batch_btn.setObjectName("danger_button")
        self.close_batch_btn.setCursor(Qt.PointingHandCursor)
        self.close_batch_btn.clicked.connect(self._on_close_batch)
        self.close_batch_btn.setMinimumHeight(35)
        self.close_batch_btn.setVisible(False)
        active_layout.addWidget(self.close_batch_btn)

        left_layout.addWidget(self.active_panel)
        left_layout.addStretch()

        splitter.addWidget(left_widget)

        # ---- Right Panel ----
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        # Action Bar
        action_bar = QFrame()
        action_bar.setObjectName("action_bar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(8, 6, 8, 6)
        action_layout.setSpacing(12)

        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("success_button")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.clicked.connect(self._on_edit_recipe)
        edit_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_layout.addWidget(edit_btn)

        report_btn = QPushButton("Report PDF")
        report_btn.setObjectName("success_button")
        report_btn.setCursor(Qt.PointingHandCursor)
        report_btn.clicked.connect(self._on_report_recipe)
        report_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_layout.addWidget(report_btn)

        right_layout.addWidget(action_bar)

        # Apply permission-based visibility/enabled state
        if hasattr(self, 'main_window'):
            mw = self.main_window
            can_manage = mw._can_perform('EDIT RECIPE')
            
            # Form and Create Button
            form_frame.setEnabled(can_manage)
            if not can_manage:
                form_frame.setToolTip("You do not have permission to create batches.")
            
            # Edit and Close buttons
            edit_btn.setEnabled(can_manage)
            self.close_batch_btn.setEnabled(can_manage)

        # Batches Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        headers = ["ID", "Batch ID", "Status", "Medicine", "Batch#",
                    "Supervisor", "Created At"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        right_layout.addWidget(self.table)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

    def _form_label(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("form_label")
        return lbl

    def _force_refresh(self):
        """Force the table to refresh and update the display."""
        from PySide6.QtWidgets import QApplication
        self.load_data()
        self.table.viewport().update()
        self.table.update()
        self.active_panel.update()
        QApplication.processEvents()

    def load_data(self):
        """Load all batches into the table and update active batch panel."""
        try:
            with get_session() as session:
                recipe_repo = RecipeRepository(session)
                param_repo = RecipeParameterRepository(session)
                all_recipes = recipe_repo.all()

                self.table.setRowCount(len(all_recipes))
                active_recipe_data = None

                for row, recipe in enumerate(all_recipes):
                    params = param_repo.get_by_recipe(recipe.recipe_id)

                    self.table.setItem(row, 0, QTableWidgetItem(str(recipe.recipe_id)))
                    self.table.setItem(row, 1, QTableWidgetItem(str(recipe.recipe_number)))

                    status_item = QTableWidgetItem(recipe.status.value if recipe.status else "—")
                    if recipe.status == RecipeStatus.ACTIVE:
                        status_item.setForeground(Qt.green)
                    self.table.setItem(row, 2, status_item)

                    self.table.setItem(row, 3, QTableWidgetItem(_get_param_value(params, PARAM_MEDICINE_NAME)))
                    self.table.setItem(row, 4, QTableWidgetItem(_get_param_value(params, PARAM_BATCH_NUMBER)))
                    self.table.setItem(row, 5, QTableWidgetItem(_get_param_value(params, PARAM_BATCH_SUPERVISOR)))
                    self.table.setItem(row, 6, QTableWidgetItem(
                        recipe.created_at.strftime("%Y-%m-%d %H:%M") if recipe.created_at else "—"
                    ))

                    if recipe.status == RecipeStatus.ACTIVE:
                        active_recipe_data = {
                            "Batch ID": str(recipe.recipe_number),
                            "Batch Date": _get_param_value(params, PARAM_BATCH_DATE),
                            "Medicine Name": _get_param_value(params, PARAM_MEDICINE_NAME),
                            "Batch Number": _get_param_value(params, PARAM_BATCH_NUMBER),
                            "Batch Supervisor": _get_param_value(params, PARAM_BATCH_SUPERVISOR),
                            "Number of Labels": _get_param_value(params, PARAM_NUM_CARTONS, "0"),
                            "Wrong Count": _get_param_value(params, PARAM_WRONG_COUNT, "0"),
                            "Good Count": _get_param_value(params, PARAM_GOOD_COUNT, "0"),
                            "Total Count": _get_param_value(params, PARAM_TOTAL_COUNT, "0"),
                        }

                # Table uses Stretch mode — no manual resize needed

                # Update active batch panel
                if active_recipe_data:
                    for key, lbl in self.active_info_labels.items():
                        lbl.setText(active_recipe_data.get(key, "—"))
                    self.close_batch_btn.setVisible(True)
                    self.close_batch_btn.setEnabled(True)
                else:
                    for lbl in self.active_info_labels.values():
                        lbl.setText("—")
                    self.close_batch_btn.setVisible(False)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load batches:\n{str(e)}")

    def _get_selected_recipe_id(self):
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Selection", "Please select a recipe first.")
            return None
        row = selected[0].row()
        return int(self.table.item(row, 0).text())


    def _on_create_recipe(self):
        # Validate required fields
        medicine = self.new_medicine.text().strip()
        batch_num = self.new_batch_num.text().strip()
        supervisor = self.new_supervisor.text().strip()
        reason_text = self.new_reason.text().strip()

        if not medicine or not batch_num or not supervisor:
            QMessageBox.warning(self, "Validation", "Medicine Name, Batch Number, and Batch Supervisor must be filled.")
            return

        if not reason_text:
            QMessageBox.warning(self, "Validation", "Reason is required for auditing.")
            return

        parameters_data = [
            {"parameter_name": PARAM_MEDICINE_NAME, "current_value": {"value": medicine}},
            {"parameter_name": PARAM_BATCH_NUMBER, "current_value": {"value": batch_num}},
            {"parameter_name": PARAM_BATCH_SUPERVISOR, "current_value": {"value": supervisor}},
            {"parameter_name": PARAM_BATCH_SIZE, "current_value": {"value": "0"}},
            {"parameter_name": PARAM_BATCH_DATE, "current_value": {"value": str(date.today())}},
            {"parameter_name": PARAM_NUM_CARTONS, "current_value": {"value": 0}},
            {"parameter_name": PARAM_WRONG_COUNT, "current_value": {"value": 0}},
            {"parameter_name": PARAM_GOOD_COUNT, "current_value": {"value": 0}},
            {"parameter_name": PARAM_TOTAL_COUNT, "current_value": {"value": 0}},
        ]

        # UI State
        self.create_btn.setEnabled(False)
        self.create_btn.setText("Creating...")

        def on_finished(success, message, recipe_id=0):
            self.create_btn.setEnabled(True)
            self.create_btn.setText("Create Batch")
            if success:
                if recipe_id > 0:
                    # Send label count to PLC D412
                    self._send_label_count_to_plc(recipe_id)
                    # Pulse M15 coil on PLC
                    try:
                        plc_comm = getattr(getattr(self, 'main_window', None), 'plc_comm', None)
                        if plc_comm and plc_comm.is_connected:
                            plc_comm.activate_m15()
                            plc_comm.reset_counters()
                            print("[RecipePage] Reset PLC counters for new batch.")
                    except Exception as e:
                        print(f"[RecipePage] Error triggering M15/M3 on creation: {e}")
                
                QMessageBox.information(self, "Success", message)
                self.new_medicine.clear()
                self.new_batch_num.clear()
                self.new_supervisor.clear()
                self.new_reason.clear()
                self._force_refresh()
            else:
                QMessageBox.critical(self, "Error", f"Failed to create batch:\n{message}")

        self._create_worker = RecipeActionWorker('CREATE', self.user_id, {
            'parameters_data': parameters_data,
            'reason': reason_text
        })
        self._create_worker.res.connect(on_finished)
        self._create_worker.start()

    def _on_edit_recipe(self):
        recipe_id = self._get_selected_recipe_id()
        if recipe_id is None:
            return

        actor_id = self.user_id

        try:
            with get_session() as session:
                recipe_repo = RecipeRepository(session)
                param_repo = RecipeParameterRepository(session)
                recipe = recipe_repo.get_by_id(recipe_id)
                if not recipe:
                    QMessageBox.warning(self, "Not Found", "Batch not found.")
                    return

                params = param_repo.get_by_recipe(recipe_id)
                current_data = {
                    'recipe_number': recipe.recipe_number,
                    'recipe_version': recipe.recipe_version,
                    'batch_date': _get_param_value(params, PARAM_BATCH_DATE),
                    'medicine_name': _get_param_value(params, PARAM_MEDICINE_NAME),
                    'batch_number': _get_param_value(params, PARAM_BATCH_NUMBER),
                    'batch_supervisor': _get_param_value(params, PARAM_BATCH_SUPERVISOR),
                    'batch_size': _get_param_value(params, PARAM_BATCH_SIZE),
                    'num_cartons': _get_param_value(params, PARAM_NUM_CARTONS, "0"),
                    'wrong_count': _get_param_value(params, PARAM_WRONG_COUNT, "0"),
                    'good_count': _get_param_value(params, PARAM_GOOD_COUNT, "0"),
                    'total_count': _get_param_value(params, PARAM_TOTAL_COUNT, "0"),
                }

            dlg = RecipeDialog(self, mode='edit', recipe_data=current_data)
            if dlg.exec() == RecipeDialog.Accepted and dlg.result_data:
                data = dlg.result_data
                param_map = {
                    PARAM_MEDICINE_NAME: data['medicine_name'],
                    PARAM_BATCH_NUMBER: data['batch_number'],
                    PARAM_BATCH_SUPERVISOR: data['batch_supervisor'],
                    PARAM_BATCH_SIZE: data['batch_size'],
                    PARAM_NUM_CARTONS: data.get('num_cartons', 0),
                    PARAM_WRONG_COUNT: current_data['wrong_count'],
                    PARAM_GOOD_COUNT: current_data['good_count'],
                    PARAM_TOTAL_COUNT: current_data['total_count'],
                }

                def on_finished(success, message):
                    if success:
                        QMessageBox.information(self, "Success", "Batch updated.")
                    else:
                        QMessageBox.warning(self, "Failure", message)
                    self._force_refresh()

                self._edit_worker = RecipeActionWorker('EDIT', actor_id, {
                    'recipe_id': recipe_id,
                    'param_map': param_map,
                    'reason': data.get('reason', '')
                })
                self._edit_worker.res.connect(on_finished)
                self._edit_worker.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to edit batch:\n{str(e)}")
            self._force_refresh()

    def _on_version_recipe(self):
        recipe_id = self._get_selected_recipe_id()
        if recipe_id is None:
            return

        actor_id = self.user_id

        try:
            with get_session() as session:
                recipe_repo = RecipeRepository(session)
                param_repo = RecipeParameterRepository(session)
                recipe = recipe_repo.get_by_id(recipe_id)
                if not recipe:
                    QMessageBox.warning(self, "Not Found", "Batch not found.")
                    return

                params = param_repo.get_by_recipe(recipe_id)
                new_version_num = recipe.recipe_version + 1

                current_data = {
                    'recipe_number': recipe.recipe_number,
                    'recipe_version': recipe.recipe_version,
                    'new_version': new_version_num,
                    'batch_date': str(date.today()),
                    'medicine_name': _get_param_value(params, PARAM_MEDICINE_NAME),
                    'batch_number': _get_param_value(params, PARAM_BATCH_NUMBER),
                    'batch_supervisor': _get_param_value(params, PARAM_BATCH_SUPERVISOR),
                    'batch_size': _get_param_value(params, PARAM_BATCH_SIZE),
                    'num_cartons': _get_param_value(params, PARAM_NUM_CARTONS, "0"),
                    'wrong_count': 0,
                    'good_count': 0,
                    'total_count': 0,
                }

            dlg = RecipeDialog(self, mode='version', recipe_data=current_data)
            if dlg.exec() == RecipeDialog.Accepted and dlg.result_data:
                data = dlg.result_data
                parameter_overrides = [
                    {"parameter_name": PARAM_MEDICINE_NAME, "current_value": {"value": data['medicine_name']}},
                    {"parameter_name": PARAM_BATCH_NUMBER, "current_value": {"value": data['batch_number']}},
                    {"parameter_name": PARAM_BATCH_SUPERVISOR, "current_value": {"value": data['batch_supervisor']}},
                    {"parameter_name": PARAM_BATCH_SIZE, "current_value": {"value": data['batch_size']}},
                    {"parameter_name": PARAM_BATCH_DATE, "current_value": {"value": data['batch_date']}},
                    {"parameter_name": PARAM_NUM_CARTONS, "current_value": {"value": data.get('num_cartons', 0)}},
                    {"parameter_name": PARAM_WRONG_COUNT, "current_value": {"value": 0}},
                    {"parameter_name": PARAM_GOOD_COUNT, "current_value": {"value": 0}},
                    {"parameter_name": PARAM_TOTAL_COUNT, "current_value": {"value": 0}},
                ]

                def on_finished(success, message):
                    if success:
                        QMessageBox.information(self, "Success", message)
                    else:
                        QMessageBox.warning(self, "Failure", message)
                    self._force_refresh()

                self._version_worker = RecipeActionWorker('VERSION', actor_id, {
                    'recipe_id': recipe_id,
                    'parameter_overrides': parameter_overrides,
                    'reason': data.get('reason', '')
                })
                self._version_worker.res.connect(on_finished)
                self._version_worker.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to version batch:\n{str(e)}")
            self._force_refresh()

    def _on_close_batch(self):
        active_recipe_id = None
        try:
            with get_session() as session:
                from models.recipe.recipe import Recipe, RecipeStatus
                active_recipe = session.query(Recipe).filter(
                    Recipe.status == RecipeStatus.ACTIVE,
                    Recipe.is_deleted == False
                ).first()
                if active_recipe:
                    active_recipe_id = active_recipe.recipe_id
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to retrieve active batch: {e}")
            return

        if not active_recipe_id:
            QMessageBox.warning(self, "Warning", "No batch is currently active.")
            return

        # Prompt for reason
        dlg = ReasonDialog(title="Close Batch", label_text="Reason for closing batch (Audited):", parent=self)
        if dlg.exec() == QDialog.Accepted:
            reason = dlg.reason_text
            self.close_batch_btn.setEnabled(False)
            self.close_batch_btn.setText("Closing...")

            def on_finished(success, message):
                self.close_batch_btn.setEnabled(True)
                self.close_batch_btn.setText("Close Batch")
                if success:
                    QMessageBox.information(self, "Success", message)
                    self._force_refresh()
                else:
                    QMessageBox.warning(self, "Failure", message)

            self._deactivate_worker = DeactivateWorker(self.user_id, active_recipe_id, reason)
            self._deactivate_worker.res.connect(on_finished)
            self._deactivate_worker.start()

    def _on_report_recipe(self):
        recipe_id = self._get_selected_recipe_id()
        if recipe_id is None:
            return

        actor_id = self.user_id

        try:
            with get_session() as session:
                from repositories.user_repository import UserRepository
                user_repo = UserRepository(session)
                actor = user_repo.get_by_id(actor_id)
                actor_name = actor.user_name if actor else "Unknown"

                # Prompt for save location
                default_filename = f"Batch_Report_{recipe_id}_{datetime.now().strftime('%Y%H%M%S')}.pdf"
                file_path, _ = QFileDialog.getSaveFileName(
                    self, "Save Report PDF", default_filename, "PDF Files (*.pdf)"
                )
                
                if not file_path:
                    return

                report_service = ReportService(session)
                actual_path = report_service.generate_recipe_report(actor_name, recipe_id, target_path=file_path)

                # Log the export action
                audit_repo = AuditRepository(session)
                audit_repo.create_audit(
                    user_id=actor_id,
                    action_type=AuditActionType.EXPORT,
                    location_screen="Batch Page",
                    reason=f"Generated Batch Report PDF: {os.path.basename(file_path)}"
                )
                session.flush()

                confirm = QMessageBox.information(
                    self, "Report Generated",
                    f"Report saved to:\n{file_path}\n\nWould you like to view it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                
                if confirm == QMessageBox.Yes:
                    import subprocess
                    if sys.platform == "win32":
                        os.startfile(file_path)
                    else:
                        opener = "open" if sys.platform == "darwin" else "xdg-open"
                        subprocess.call([opener, file_path])

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate report:\n{str(e)}")

    def _send_label_count_to_plc(self, recipe_id: int):
        """Read num_cartons from activated batch and send label count to PLC D412."""
        try:
            plc_comm = getattr(getattr(self, 'main_window', None), 'plc_comm', None)
            if not plc_comm or not plc_comm.is_connected:
                return

            with get_session() as session:
                param_repo = RecipeParameterRepository(session)
                param = param_repo.get_by_name(recipe_id, PARAM_NUM_CARTONS)
                if param:
                    val = param.current_value
                    count = int(val.get('value', 0)) if isinstance(val, dict) else int(val or 0)
                    if hasattr(plc_comm, 'write_label_count'):
                        plc_comm.write_label_count(count)
                    else:
                        plc_comm.write_carton_count(count)
                    print(f"[RecipePage] Sent Number of Labels ({count}) to PLC D412")
        except Exception as e:
            print(f"[RecipePage] Error sending label count to PLC: {e}")

    def update_plc_values(self, data: dict):
        """Update active batch wrong/good/total counts in real-time from PLC online values."""
        if "wrong_count" in data and "Wrong Count" in self.active_info_labels:
            self.active_info_labels["Wrong Count"].setText(str(data["wrong_count"]))
        if "good_count" in data and "Good Count" in self.active_info_labels:
            self.active_info_labels["Good Count"].setText(str(data["good_count"]))
        if "total_count" in data and "Total Count" in self.active_info_labels:
            self.active_info_labels["Total Count"].setText(str(data["total_count"]))

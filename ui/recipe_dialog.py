"""
BLUE SQUARE — Recipe Edit/Version Dialog
Dialog for editing or versioning recipes.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGridLayout
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


class RecipeDialog(QDialog):
    """
    Dialog for editing or versioning a recipe.
    mode='edit' — edit existing recipe parameters.
    mode='version' — create new version with pre-populated fields.
    """

    def __init__(self, parent=None, mode='edit', recipe_data=None):
        super().__init__(parent)
        self.mode = mode
        self.recipe_data = recipe_data or {}
        self.result_data = None
        title = "Edit Batch" if mode == 'edit' else "New Batch Version"
        self.setWindowTitle(title)
        self.setMinimumSize(500, 420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        icon = "✏️" if self.mode == 'edit' else "📋"
        header = QLabel(f"{icon}  {'Edit Batch' if self.mode == 'edit' else 'New Batch Version'}")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet("color: #007acc;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        if self.mode == 'version':
            info = QLabel(f"Creating version {self.recipe_data.get('new_version', '?')} "
                          f"from Batch #{self.recipe_data.get('recipe_number', '?')}")
            info.setStyleSheet("color: #007acc; font-size: 12px;")
            info.setAlignment(Qt.AlignCenter)
            layout.addWidget(info)
"""
BLUE SQUARE — Recipe Edit/Version Dialog
Dialog for editing or versioning recipes.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGridLayout
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


class RecipeDialog(QDialog):
    """
    Dialog for editing or versioning a recipe.
    mode='edit' — edit existing recipe parameters.
    mode='version' — create new version with pre-populated fields.
    """

    def __init__(self, parent=None, mode='edit', recipe_data=None):
        super().__init__(parent)
        self.mode = mode
        self.recipe_data = recipe_data or {}
        self.result_data = None
        title = "Edit Batch" if mode == 'edit' else "New Batch Version"
        self.setWindowTitle(title)
        self.setMinimumSize(500, 420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        icon = "✏️" if self.mode == 'edit' else "📋"
        header = QLabel(f"{icon}  {'Edit Batch' if self.mode == 'edit' else 'New Batch Version'}")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet("color: #007acc;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        if self.mode == 'version':
            info = QLabel(f"Creating version {self.recipe_data.get('new_version', '?')} "
                          f"from Batch #{self.recipe_data.get('recipe_number', '?')}")
            info.setStyleSheet("color: #007acc; font-size: 12px;")
            info.setAlignment(Qt.AlignCenter)
            layout.addWidget(info)

        layout.addSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(8)

        # Batch ID (read-only)
        grid.addWidget(self._label("Batch ID"), 0, 0)
        self.recipe_id_input = QLineEdit()
        self.recipe_id_input.setText(str(self.recipe_data.get('recipe_number', '')))
        self.recipe_id_input.setReadOnly(True)
        self.recipe_id_input.setStyleSheet("background-color: #1e1e1e; color: #808080;")
        grid.addWidget(self.recipe_id_input, 0, 1)

        # Batch Date (read-only, today)
        grid.addWidget(self._label("Batch Date"), 1, 0)
        self.batch_date_input = QLineEdit()
        from datetime import date
        self.batch_date_input.setText(str(date.today()))
        if self.mode == 'edit':
            self.batch_date_input.setText(self.recipe_data.get('batch_date', str(date.today())))
        self.batch_date_input.setReadOnly(True)
        self.batch_date_input.setStyleSheet("background-color: #1e1e1e; color: #808080;")
        grid.addWidget(self.batch_date_input, 1, 1)

        # Medicine Name
        grid.addWidget(self._label("Medicine Name"), 2, 0)
        self.medicine_input = QLineEdit()
        self.medicine_input.setPlaceholderText("Enter medicine name")
        self.medicine_input.setText(self.recipe_data.get('medicine_name', ''))
        grid.addWidget(self.medicine_input, 2, 1)

        # Batch Number
        grid.addWidget(self._label("Batch Number"), 3, 0)
        self.batch_number_input = QLineEdit()
        self.batch_number_input.setPlaceholderText("Enter batch number")
        self.batch_number_input.setText(self.recipe_data.get('batch_number', ''))
        grid.addWidget(self.batch_number_input, 3, 1)

        # Batch Supervisor
        grid.addWidget(self._label("Batch Supervisor"), 4, 0)
        self.supervisor_input = QLineEdit()
        self.supervisor_input.setPlaceholderText("Enter supervisor name")
        self.supervisor_input.setText(self.recipe_data.get('batch_supervisor', ''))
        grid.addWidget(self.supervisor_input, 4, 1)

        # Reason for change/adding
        grid.addWidget(self._label("Reason"), 5, 0)
        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText("Enter reason for change or version (Audited)")
        grid.addWidget(self.reason_input, 5, 1)

        layout.addLayout(grid)
        layout.addSpacing(12)

        # Buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save" if self.mode == 'edit' else "Create Version")
        save_btn.setObjectName("accent_button")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def _label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #808080; font-weight: 600; font-size: 11px;")
        return lbl

    def _on_save(self):
        medicine = self.medicine_input.text().strip()
        batch_number = self.batch_number_input.text().strip()
        supervisor = self.supervisor_input.text().strip()

        reason = self.reason_input.text().strip()

        if not medicine or not batch_number or not supervisor:
            QMessageBox.warning(self, "Validation",
                                "Medicine Name, Batch Number, and Batch Supervisor are required.")
            return

        if not reason:
            QMessageBox.warning(self, "Validation", "Reason is required for auditing purposes.")
            return

        self.result_data = {
            'medicine_name': medicine,
            'batch_number': batch_number,
            'batch_supervisor': supervisor,
            'batch_size': "0",
            'num_cartons': self.recipe_data.get('num_cartons', 0),
            'batch_date': self.batch_date_input.text(),
            'reason': reason,
        }
        self.accept()

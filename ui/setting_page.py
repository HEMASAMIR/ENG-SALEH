import os
import sys
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QFrame, QPushButton, QGridLayout, QMessageBox, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

try:
    from core.user_settings import load_reject_on_delay, save_reject_on_delay
except ImportError:
    from user_settings import load_reject_on_delay, save_reject_on_delay

class SettingPage(QWidget):
    """
    Setting page showing Consecutive Error limit, Count IN, and Count Out.
    Allows updating Consecutive Error limit to D412 on PLC.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("setting_page")
        self.main_window = parent
        self._load_qss()
        self._build_ui()

    def _load_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'setting.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # Page Title
        title = QLabel("SYSTEM SETTING")
        title.setObjectName("page_title")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        layout.addWidget(title)

        # Grid of Cards
        grid = QGridLayout()
        grid.setSpacing(20)

        # 1. Consecutive Error Limit Card
        self.card_error = QFrame()
        self.card_error.setObjectName("setting_card")
        err_layout = QVBoxLayout(self.card_error)
        err_layout.setSpacing(12)

        lbl_err_title = QLabel("Consecutive Error Limit")
        lbl_err_title.setObjectName("card_title")
        lbl_err_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        err_layout.addWidget(lbl_err_title)

        self.lbl_err_val = QLabel("Current Limit: —")
        self.lbl_err_val.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: 500;")
        err_layout.addWidget(self.lbl_err_val)

        # Input Row
        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self.err_input = QLineEdit()
        self.err_input.setObjectName("setting_input")
        self.err_input.setPlaceholderText("Enter limit (e.g. 5)")
        self.err_input.setFixedWidth(150)
        input_row.addWidget(self.err_input)

        self.btn_apply = QPushButton("Apply to PLC")
        self.btn_apply.setObjectName("apply_button")
        self.btn_apply.clicked.connect(self._on_apply_limit)
        input_row.addWidget(self.btn_apply)
        input_row.addStretch()

        err_layout.addLayout(input_row)
        err_layout.addStretch()
        grid.addWidget(self.card_error, 0, 0)

        # 2. Count IN Card
        self.card_in = QFrame()
        self.card_in.setObjectName("setting_card")
        in_layout = QVBoxLayout(self.card_in)
        in_layout.setSpacing(10)

        lbl_in_title = QLabel("Count IN")
        lbl_in_title.setObjectName("card_title")
        lbl_in_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        in_layout.addWidget(lbl_in_title)

        self.lbl_in_val = QLabel("—")
        self.lbl_in_val.setObjectName("counter_value")
        self.lbl_in_val.setAlignment(Qt.AlignCenter)
        in_layout.addWidget(self.lbl_in_val)
        in_layout.addStretch()
        grid.addWidget(self.card_in, 0, 1)

        # 3. Count Out Card
        self.card_out = QFrame()
        self.card_out.setObjectName("setting_card")
        out_layout = QVBoxLayout(self.card_out)
        out_layout.setSpacing(10)

        lbl_out_title = QLabel("Count Out")
        lbl_out_title.setObjectName("card_title")
        lbl_out_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        out_layout.addWidget(lbl_out_title)

        self.lbl_out_val = QLabel("—")
        self.lbl_out_val.setObjectName("counter_value")
        self.lbl_out_val.setAlignment(Qt.AlignCenter)
        out_layout.addWidget(self.lbl_out_val)
        out_layout.addStretch()
        grid.addWidget(self.card_out, 1, 0)

        # 4. Reject On Delay Card (D425)
        self.card_reject = QFrame()
        self.card_reject.setObjectName("setting_card")
        reject_layout = QVBoxLayout(self.card_reject)
        reject_layout.setSpacing(12)

        lbl_reject_title = QLabel("Reject On Delay (D425)")
        lbl_reject_title.setObjectName("card_title")
        lbl_reject_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        reject_layout.addWidget(lbl_reject_title)

        saved_delay = load_reject_on_delay()
        self.lbl_reject_val = QLabel(f"Current Delay: {saved_delay}")
        self.lbl_reject_val.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: 500;")
        reject_layout.addWidget(self.lbl_reject_val)

        # Input Row
        reject_input_row = QHBoxLayout()
        reject_input_row.setSpacing(10)
        self.reject_input = QLineEdit()
        self.reject_input.setObjectName("setting_input")
        self.reject_input.setPlaceholderText("Enter delay (0-65535)")
        self.reject_input.setFixedWidth(150)
        if saved_delay > 0:
            self.reject_input.setText(str(saved_delay))
        reject_input_row.addWidget(self.reject_input)

        self.btn_apply_reject = QPushButton("Apply to PLC")
        self.btn_apply_reject.setObjectName("apply_button")
        self.btn_apply_reject.clicked.connect(self._on_apply_reject_delay)
        reject_input_row.addWidget(self.btn_apply_reject)
        reject_input_row.addStretch()

        reject_layout.addLayout(reject_input_row)
        reject_layout.addStretch()
        grid.addWidget(self.card_reject, 1, 1)

        # Make columns take equal width
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        layout.addLayout(grid)

    def _on_apply_limit(self):
        text = self.err_input.text().strip()
        if not text:
            QMessageBox.warning(self, "Validation", "Please enter a value.")
            return

        try:
            val = int(text)
            if val < 1 or val > 65535:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Validation", "Please enter a valid integer between 1 and 65535.")
            return

        plc_comm = getattr(self.main_window, 'plc_comm', None)
        if plc_comm and plc_comm.is_connected:
            if hasattr(plc_comm, 'write_label_count'):
                plc_comm.write_label_count(val)
            else:
                plc_comm.write_carton_count(val)
            QMessageBox.information(self, "Success", f"Consecutive error limit set to {val} on PLC.")
            self.err_input.clear()
        else:
            QMessageBox.critical(self, "PLC Error", "Failed to write. PLC is disconnected.")

    def _on_apply_reject_delay(self):
        text = self.reject_input.text().strip()
        if not text:
            QMessageBox.warning(self, "Validation", "Please enter a value.")
            return

        try:
            val = int(text)
            if val < 0 or val > 65535:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Validation", "Please enter a valid integer between 0 and 65535.")
            return

        save_reject_on_delay(val)
        self.lbl_reject_val.setText(f"Current Delay: {val}")

        plc_comm = getattr(self.main_window, 'plc_comm', None)
        if plc_comm and plc_comm.is_connected:
            if hasattr(plc_comm, 'write_reject_on_delay'):
                plc_comm.write_reject_on_delay(val)
            QMessageBox.information(self, "Success", f"Reject on delay set to {val} on PLC (D425).")
        else:
            QMessageBox.information(self, "Saved", f"Reject on delay saved as {val}. (PLC disconnected, will apply when connected).")

    def update_plc_values(self, data: dict):
        """Update display widgets with real-time online values from PLC poller."""
        if "carton_count" in data:
            self.lbl_err_val.setText(f"Current Limit: {data['carton_count']}")
        if "reject_on_delay" in data:
            self.lbl_reject_val.setText(f"Current Delay: {data['reject_on_delay']}")
        if "count_in" in data:
            self.lbl_in_val.setText(str(data["count_in"]))
        if "count_out" in data:
            self.lbl_out_val.setText(str(data["count_out"]))

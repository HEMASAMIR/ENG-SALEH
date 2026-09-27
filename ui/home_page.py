from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QFrame, QGroupBox, QGridLayout, QSizePolicy, QPushButton,
    QComboBox, QMessageBox, QSlider, QScrollArea, QSpinBox, QDoubleSpinBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QRect
from PySide6.QtGui import QFont, QImage, QPixmap, QPainter, QPen, QColor
import redis
import json
import re
import numpy as np
import cv2
import os
from core.config import settings
from core.database import get_session
from services.recipe_service import RecipeService
from models.recipe.recipe import RecipeStatus
from core.object_locator import ObjectLocator, LocatorResult

class ROILabel(QLabel):
    """Custom Label that allows selecting ROI by dragging."""
    roi_selected = Signal(QRect)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.begin = QPoint()
        self._end = QPoint()
        self.is_selecting = False
        self.selection_color = QColor(0, 255, 0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.begin = event.position().toPoint()
            self._end = self.begin
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_selecting:
            self._end = event.position().toPoint()
            self.is_selecting = False
            self.update()
            roi_rect = QRect(self.begin, self._end).normalized()
            self.roi_selected.emit(roi_rect)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.is_selecting:
            painter = QPainter(self)
            painter.setPen(QPen(self.selection_color, 2, Qt.SolidLine))
            painter.drawRect(QRect(self.begin, self._end).normalized())

class HomePage(QWidget):
    """
    Home page with ROI selection, adjustment controls, and 3-column parameter validation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("home_page")
        
        # State
        self.roi_selection_mode = None # 'date', 'pharma', or 'locator'
        self.is_running = False
        self.current_raw_img = None
        
        self.redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB
        )
        
        # Object Locator Engine
        self.locator = ObjectLocator()
        self.locator.sync_from_redis(
            self.redis_client,
            settings.ROI_LOCATOR_KEY,
            settings.ROI_LOCATOR_TEMPLATE_KEY
        )
        self.last_locator_result = None

        # Initialize Redis keys if not present
        if not self.redis_client.exists(settings.ROI_DATE_CONTRAST):
            self.redis_client.set(settings.ROI_DATE_CONTRAST, "0.8")
        if not self.redis_client.exists(settings.ROI_PHARMA_CONTRAST):
            self.redis_client.set(settings.ROI_PHARMA_CONTRAST, "3.0")
        if not self.redis_client.exists(settings.ROI_LABELS_CONTRAST):
            self.redis_client.set(settings.ROI_LABELS_CONTRAST, "0.5")
        if not self.redis_client.exists(settings.ROI_DATE_ROTATION):
            self.redis_client.set(settings.ROI_DATE_ROTATION, "90")
        if not self.redis_client.exists(settings.ROI_PHARMA_ROTATION):
            self.redis_client.set(settings.ROI_PHARMA_ROTATION, "0")
        if not self.redis_client.exists(settings.ROI_LABELS_ROTATION):
            self.redis_client.set(settings.ROI_LABELS_ROTATION, "90")
        if not self.redis_client.exists(settings.ROI_DATE_GRAYSCALE):
            self.redis_client.set(settings.ROI_DATE_GRAYSCALE, "0")
        if not self.redis_client.exists(settings.ROI_DATE_THRESHOLD):
            self.redis_client.set(settings.ROI_DATE_THRESHOLD, "0")
        if not self.redis_client.exists(settings.ROI_LABELS_GRAYSCALE):
            self.redis_client.set(settings.ROI_LABELS_GRAYSCALE, "5")
        if not self.redis_client.exists(settings.ROI_LABELS_THRESHOLD):
            self.redis_client.set(settings.ROI_LABELS_THRESHOLD, "0")

        self._load_qss()
        self._build_ui()
        self._load_saved_locator()
        self._load_saved_ocr_conf()
        self._load_saved_labels_conf()
        self._load_saved_contrast()
        self._load_saved_rotation()
        self._load_saved_grayscale()
        self._load_saved_threshold()
        self._load_saved_exposure()
        self._load_saved_expected_values()

        if hasattr(self, "labels_status_badge") and self.redis_client.exists(settings.ROI_LABELS_KEY):
            self.labels_status_badge.setText("READY")
        if hasattr(self, "date_status_badge") and self.redis_client.exists(settings.ROI_DATE_KEY):
            self.date_status_badge.setText("READY")
        if hasattr(self, "pharma_status_badge") and self.redis_client.exists(settings.ROI_PHARMA_KEY):
            self.pharma_status_badge.setText("READY")

        self.last_frame_timestamp = None
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_redis)
        self.poll_timer.start(100)
        self._was_pipeline_running = False

    def _load_qss(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'qss', 'home.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # 1. Top Section: Camera + ROI Canvases
        top_layout = QHBoxLayout()
        top_layout.setSpacing(15)

        # Camera Area
        self.camera_container = QFrame()
        self.camera_container.setObjectName("camera_frame")
        cam_v_layout = QVBoxLayout(self.camera_container)
        cam_v_layout.setContentsMargins(0, 0, 0, 0)
        
        self.camera_label = ROILabel()
        self.camera_label.setObjectName("camera_placeholder")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setMinimumSize(400, 300)
        self.camera_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.camera_label.roi_selected.connect(self._on_roi_selected)
        cam_v_layout.addWidget(self.camera_label)

        # Exposure Control Row
        exposure_row = QHBoxLayout()
        exposure_row.setContentsMargins(8, 4, 8, 4)
        exposure_row.setSpacing(8)

        exposure_lbl = QLabel("Exposure (µs):")
        exposure_lbl.setStyleSheet("color: #cccccc; font-size: 12px; font-weight: 600;")
        exposure_row.addWidget(exposure_lbl)

        self.exposure_input = QLineEdit("10000")
        self.exposure_input.setFixedWidth(100)
        self.exposure_input.setObjectName("home_input")
        self.exposure_input.setAlignment(Qt.AlignCenter)
        exposure_row.addWidget(self.exposure_input)

        self.btn_apply_exposure = QPushButton("Apply")
        self.btn_apply_exposure.setObjectName("roi_button")
        self.btn_apply_exposure.setFixedWidth(70)
        self.btn_apply_exposure.clicked.connect(self._on_apply_exposure)
        exposure_row.addWidget(self.btn_apply_exposure)

        exposure_row.addStretch()
        cam_v_layout.addLayout(exposure_row)
        
        top_layout.addWidget(self.camera_container, 3)

        # Right Canvases Area with smooth ScrollArea
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setObjectName("roi_scroll_area")

        scroll_content = QWidget()
        scroll_content.setObjectName("roi_scroll_content")
        right_v_layout = QVBoxLayout(scroll_content)
        right_v_layout.setContentsMargins(0, 0, 4, 0)
        right_v_layout.setSpacing(6)

        # Status Indicator
        self.status_indicator = QLabel("SYSTEM IDLE")
        self.status_indicator.setAlignment(Qt.AlignCenter)
        self.status_indicator.setFixedHeight(32)
        self.status_indicator.setFont(QFont("Arial", 10, QFont.Bold))
        self.status_indicator.setStyleSheet("background-color: #555555; color: white; border-radius: 4px;")
        right_v_layout.addWidget(self.status_indicator)

        # 1. Object Locator Box
        self.locator_roi_container = self._create_locator_box()
        right_v_layout.addWidget(self.locator_roi_container)

        # 2. OCR Labels Box (LOT : MFG : EXP :)
        self.labels_roi_container = self._create_roi_box("Labels", "labels")
        right_v_layout.addWidget(self.labels_roi_container)

        # 3. OCR Values Box (LOT / MFG / EXP)
        self.date_roi_container = self._create_roi_box("OCR Values", "date")
        right_v_layout.addWidget(self.date_roi_container)

        # 4. Pharma Code ROI Box
        self.pharma_roi_container = self._create_roi_box("Pharma Code", "pharma")
        right_v_layout.addWidget(self.pharma_roi_container)
        right_v_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        top_layout.addWidget(scroll_area, 2)
        main_layout.addLayout(top_layout, 1)

        # 2. Bottom Section: Compact 3-Column Inspection Layout
        bottom_group = QGroupBox("Inspection & Validation")
        bottom_group.setObjectName("input_group")
        bottom_group.setFixedHeight(200)
        bottom_layout = QGridLayout(bottom_group)
        bottom_layout.setContentsMargins(14, 2, 14, 6)
        bottom_layout.setHorizontalSpacing(14)
        bottom_layout.setVerticalSpacing(3)
        bottom_layout.setColumnStretch(0, 0)
        bottom_layout.setColumnStretch(1, 1)
        bottom_layout.setColumnStretch(2, 1)
        bottom_layout.setColumnStretch(3, 1)

        # Headers for Expected and Actual columns
        bottom_layout.addWidget(QLabel(""), 0, 0)
        lbl_expected = QLabel("<b>Expected</b>")
        lbl_expected.setAlignment(Qt.AlignCenter)
        lbl_expected.setStyleSheet("color: #0088cc; font-size: 12px;")
        bottom_layout.addWidget(lbl_expected, 0, 1)

        lbl_actual = QLabel("<b>Actual</b>")
        lbl_actual.setAlignment(Qt.AlignCenter)
        lbl_actual.setStyleSheet("color: #0088cc; font-size: 12px;")
        bottom_layout.addWidget(lbl_actual, 0, 2)

        # Row 1: Labels (LOT : MFG : EXP :)
        lbl_labels = QLabel("<b>Labels</b>")
        lbl_labels.setFixedWidth(50)
        bottom_layout.addWidget(lbl_labels, 1, 0)
        self.expected_labels = QLineEdit("LOT : MFG : EXP :")
        self.expected_labels.setObjectName("home_input")
        self.expected_labels.setPlaceholderText("LOT : MFG : EXP :")
        self.expected_labels.setFixedHeight(23)
        bottom_layout.addWidget(self.expected_labels, 1, 1)
        self.actual_labels = QLineEdit()
        self.actual_labels.setReadOnly(True)
        self.actual_labels.setObjectName("home_input")
        self.actual_labels.setPlaceholderText("LOT : MFG : EXP :")
        self.actual_labels.setFixedHeight(23)
        bottom_layout.addWidget(self.actual_labels, 1, 2)

        # Row 2: LOT
        lbl_lot = QLabel("<b>LOT</b>")
        lbl_lot.setFixedWidth(50)
        bottom_layout.addWidget(lbl_lot, 2, 0)
        self.expected_lot = QLineEdit()
        self.expected_lot.setObjectName("home_input")
        self.expected_lot.setPlaceholderText("LOT")
        self.expected_lot.setFixedHeight(23)
        bottom_layout.addWidget(self.expected_lot, 2, 1)
        self.actual_lot = QLineEdit()
        self.actual_lot.setReadOnly(True)
        self.actual_lot.setObjectName("home_input")
        self.actual_lot.setPlaceholderText("LOT")
        self.actual_lot.setFixedHeight(23)
        bottom_layout.addWidget(self.actual_lot, 2, 2)

        # Row 3: MFG
        lbl_mfg = QLabel("<b>MFG</b>")
        lbl_mfg.setFixedWidth(50)
        bottom_layout.addWidget(lbl_mfg, 3, 0)
        self.expected_mfg = QLineEdit()
        self.expected_mfg.setObjectName("home_input")
        self.expected_mfg.setPlaceholderText("MFG")
        self.expected_mfg.setFixedHeight(23)
        bottom_layout.addWidget(self.expected_mfg, 3, 1)
        self.actual_mfg = QLineEdit()
        self.actual_mfg.setReadOnly(True)
        self.actual_mfg.setObjectName("home_input")
        self.actual_mfg.setPlaceholderText("MFG")
        self.actual_mfg.setFixedHeight(23)
        bottom_layout.addWidget(self.actual_mfg, 3, 2)

        # Row 4: EXP
        lbl_exp = QLabel("<b>EXP</b>")
        lbl_exp.setFixedWidth(50)
        bottom_layout.addWidget(lbl_exp, 4, 0)
        self.expected_exp = QLineEdit()
        self.expected_exp.setObjectName("home_input")
        self.expected_exp.setPlaceholderText("EXP")
        self.expected_exp.setFixedHeight(23)
        bottom_layout.addWidget(self.expected_exp, 4, 1)
        self.actual_exp = QLineEdit()
        self.actual_exp.setReadOnly(True)
        self.actual_exp.setObjectName("home_input")
        self.actual_exp.setPlaceholderText("EXP")
        self.actual_exp.setFixedHeight(23)
        bottom_layout.addWidget(self.actual_exp, 4, 2)

        # Row 5: Pharma
        lbl_pharma = QLabel("<b>Pharma</b>")
        lbl_pharma.setFixedWidth(50)
        bottom_layout.addWidget(lbl_pharma, 5, 0)
        self.expected_pharma = QLineEdit()
        self.expected_pharma.setObjectName("home_input")
        self.expected_pharma.setPlaceholderText("Pharma")
        self.expected_pharma.setFixedHeight(23)
        bottom_layout.addWidget(self.expected_pharma, 5, 1)
        self.actual_pharma = QLineEdit()
        self.actual_pharma.setReadOnly(True)
        self.actual_pharma.setObjectName("home_input")
        self.actual_pharma.setPlaceholderText("Pharma")
        self.actual_pharma.setFixedHeight(23)
        bottom_layout.addWidget(self.actual_pharma, 5, 2)

        # Real-time synchronization of expected values to Redis
        self.expected_labels.textChanged.connect(self._sync_expected_to_redis)
        self.expected_lot.textChanged.connect(self._sync_expected_to_redis)
        self.expected_mfg.textChanged.connect(self._sync_expected_to_redis)
        self.expected_exp.textChanged.connect(self._sync_expected_to_redis)
        self.expected_pharma.textChanged.connect(self._sync_expected_to_redis)

        # Control Buttons (Vertically ordered: 1-Locator, 2-Labels, 3-OCR Values, 4-Pharma)
        self.btn_trigger = QPushButton("Manual Trigger")
        self.btn_trigger.setObjectName("roi_button")
        self.btn_trigger.setFixedHeight(23)
        self.btn_trigger.clicked.connect(self._on_trigger_pressed)

        self.btn_select_locator = QPushButton("1. Select Object Locator")
        self.btn_select_locator.setObjectName("roi_button")
        self.btn_select_locator.setFixedHeight(23)
        self.btn_select_locator.setCheckable(True)
        self.btn_select_locator.clicked.connect(lambda: self._set_roi_mode('locator'))

        self.btn_select_labels = QPushButton("2. Select Labels ROI")
        self.btn_select_labels.setObjectName("roi_button")
        self.btn_select_labels.setFixedHeight(23)
        self.btn_select_labels.setCheckable(True)
        self.btn_select_labels.clicked.connect(lambda: self._set_roi_mode('labels'))

        self.btn_select_date = QPushButton("3. Select OCR Values ROI")
        self.btn_select_date.setObjectName("roi_button")
        self.btn_select_date.setFixedHeight(23)
        self.btn_select_date.setCheckable(True)
        self.btn_select_date.clicked.connect(lambda: self._set_roi_mode('date'))

        self.btn_select_pharma = QPushButton("4. Select Pharma Code ROI")
        self.btn_select_pharma.setObjectName("roi_button")
        self.btn_select_pharma.setFixedHeight(23)
        self.btn_select_pharma.setCheckable(True)
        self.btn_select_pharma.clicked.connect(lambda: self._set_roi_mode('pharma'))

        self.btn_start = QPushButton("Start Test")
        self.btn_start.setObjectName("success_button")
        self.btn_start.setFixedHeight(26)
        self.btn_start.clicked.connect(self._on_start_pressed)

        btn_layout = QVBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(2)
        btn_layout.addWidget(self.btn_trigger)
        btn_layout.addWidget(self.btn_select_locator)
        btn_layout.addWidget(self.btn_select_labels)
        btn_layout.addWidget(self.btn_select_date)
        btn_layout.addWidget(self.btn_select_pharma)
        btn_layout.addWidget(self.btn_start)

        bottom_layout.addLayout(btn_layout, 0, 3, 6, 1)

        # Enable context menu for right-click copy on the container AND individual fields
        for widget in [bottom_group, self.actual_labels, self.actual_lot, self.actual_mfg, self.actual_exp, self.actual_pharma]:
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(self._show_context_menu)

        main_layout.addWidget(bottom_group, 0)

    def _create_locator_box(self):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setObjectName("locator_roi_frame")
        frame.setFixedHeight(145)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        lbl_title = QLabel("1. Object Locator")
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setObjectName("roi_box_title")
        layout.addWidget(lbl_title)

        roi_content_layout = QHBoxLayout()
        roi_content_layout.setContentsMargins(0, 0, 0, 0)
        roi_content_layout.setSpacing(8)

        # Template Picture Canvas
        self.locator_canvas = QLabel("No Locator Set")
        self.locator_canvas.setObjectName("locator_canvas")
        self.locator_canvas.setAlignment(Qt.AlignCenter)
        self.locator_canvas.setMinimumSize(160, 90)
        self.locator_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        roi_content_layout.addWidget(self.locator_canvas, 1)

        # Controls & Info Frame
        controls_frame = QFrame()
        controls_frame.setObjectName("roi_controls_frame")
        controls_frame.setFixedWidth(205)
        ctrl_layout = QVBoxLayout(controls_frame)
        ctrl_layout.setContentsMargins(2, 0, 2, 0)
        ctrl_layout.setSpacing(3)

        st_row = QHBoxLayout()
        self.locator_status_badge = QLabel("IDLE")
        self.locator_status_badge.setAlignment(Qt.AlignCenter)
        self.locator_status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
        st_row.addWidget(self.locator_status_badge)

        self.locator_score_lbl = QLabel("Match: —")
        self.locator_score_lbl.setAlignment(Qt.AlignCenter)
        self.locator_score_lbl.setStyleSheet("color: #cccccc; font-size: 11px; font-weight: bold;")
        st_row.addWidget(self.locator_score_lbl)
        ctrl_layout.addLayout(st_row)

        self.locator_time_lbl = QLabel("Locate Time: —")
        self.locator_time_lbl.setAlignment(Qt.AlignCenter)
        self.locator_time_lbl.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        ctrl_layout.addWidget(self.locator_time_lbl)

        # Confidence Threshold Slider
        conf_block = QVBoxLayout()
        conf_block.setSpacing(1)

        conf_header_layout = QHBoxLayout()
        lbl_conf_title = QLabel("Min Conf:")
        lbl_conf_title.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        self.lbl_conf_val = QLabel("0%")
        self.lbl_conf_val.setStyleSheet("color: #00bcd4; font-size: 10px; font-weight: bold;")
        conf_header_layout.addWidget(lbl_conf_title)
        conf_header_layout.addStretch()
        conf_header_layout.addWidget(self.lbl_conf_val)
        conf_block.addLayout(conf_header_layout)

        self.slider_locator_conf = QSlider(Qt.Horizontal)
        self.slider_locator_conf.setObjectName("locator_conf_slider")
        self.slider_locator_conf.setRange(0, 99)
        self.slider_locator_conf.setValue(0)
        self.slider_locator_conf.valueChanged.connect(self._on_locator_conf_changed)
        conf_block.addWidget(self.slider_locator_conf)

        ctrl_layout.addLayout(conf_block)

        self.btn_clear_locator = QPushButton("Clear Locator")
        self.btn_clear_locator.setObjectName("roi_button")
        self.btn_clear_locator.setFixedHeight(22)
        self.btn_clear_locator.clicked.connect(self._on_clear_locator)
        ctrl_layout.addWidget(self.btn_clear_locator)

        roi_content_layout.addWidget(controls_frame, 0)
        layout.addLayout(roi_content_layout, 1)

        return frame

    def _on_locator_conf_changed(self, val):
        """Handle Object Locator min confidence slider change."""
        conf_float = round(val / 100.0, 2)
        if hasattr(self, "lbl_conf_val"):
            self.lbl_conf_val.setText(f"{val}%")
        self.redis_client.set(settings.LOCATOR_MIN_CONFIDENCE_KEY, str(conf_float))
        try:
            from core.user_settings import save_locator_confidence
            save_locator_confidence(conf_float)
        except Exception:
            pass

    def _load_saved_locator(self):
        """Restore locator picture, state, and confidence setting from disk or Redis."""
        try:
            from core.user_settings import load_locator_confidence
            conf_val = load_locator_confidence()
            int_val = int(round(conf_val * 100))
            if hasattr(self, "slider_locator_conf"):
                self.slider_locator_conf.blockSignals(True)
                self.slider_locator_conf.setValue(int_val)
                self.slider_locator_conf.blockSignals(False)
            if hasattr(self, "lbl_conf_val"):
                self.lbl_conf_val.setText(f"{int_val}%")
            self.redis_client.set(settings.LOCATOR_MIN_CONFIDENCE_KEY, str(conf_val))
        except Exception:
            pass

        self.locator.sync_from_redis(
            self.redis_client,
            settings.ROI_LOCATOR_KEY,
            settings.ROI_LOCATOR_TEMPLATE_KEY
        )
        self._update_locator_picture()
        if self.locator.has_template:
            self.locator_status_badge.setText("READY")
            self.locator_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
            self.locator_score_lbl.setText("Template Ready")
        else:
            self.locator_status_badge.setText("IDLE")
            self.locator_status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
            self.locator_score_lbl.setText("No Template")

    def _on_ocr_conf_changed(self, val):
        """Handle OCR min confidence slider change."""
        conf_float = round(val / 100.0, 2)
        if hasattr(self, "lbl_ocr_conf_val"):
            self.lbl_ocr_conf_val.setText(f"{val}%")
        self.redis_client.set(settings.OCR_MIN_CONFIDENCE_KEY, str(conf_float))
        try:
            from core.user_settings import save_ocr_confidence
            save_ocr_confidence(conf_float)
        except Exception:
            pass

    def _on_labels_conf_changed(self, val):
        """Handle Labels OCR min confidence slider change."""
        conf_float = round(val / 100.0, 2)
        if hasattr(self, "lbl_labels_conf_val"):
            self.lbl_labels_conf_val.setText(f"{val}%")
        self.redis_client.set(settings.LABELS_MIN_CONFIDENCE_KEY, str(conf_float))
        try:
            from core.user_settings import save_labels_confidence
            save_labels_confidence(conf_float)
        except Exception:
            pass

    def _open_ocr_filter_dialog(self, prefix="date"):
        """Open the OCR filter and print quality parameters dialog (Admin & Root only)."""
        # Role check: only admin and root users can configure OCR filters
        role = ""
        main_win = getattr(self, "main_window", None) or self.parent()
        if main_win and hasattr(main_win, "role_name"):
            role = str(main_win.role_name).lower().strip()

        if role and role not in ("root", "admin", "administrator"):
            QMessageBox.warning(
                self,
                "Access Denied",
                "Access restricted: Only Admin and Root users can access OCR Filter settings."
            )
            return

        try:
            from ui.ocr_filter_dialog import OcrFilterDialog
            dlg = OcrFilterDialog(self, prefix=prefix)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open filter dialog: {e}")

    def _load_saved_ocr_conf(self):
        """Restore OCR confidence setting from disk or Redis."""
        try:
            from core.user_settings import load_ocr_confidence
            conf_val = load_ocr_confidence()
            int_val = int(round(conf_val * 100))
            if hasattr(self, "slider_ocr_conf"):
                self.slider_ocr_conf.blockSignals(True)
                self.slider_ocr_conf.setValue(int_val)
                self.slider_ocr_conf.blockSignals(False)
            if hasattr(self, "lbl_ocr_conf_val"):
                self.lbl_ocr_conf_val.setText(f"{int_val}%")
            self.redis_client.set(settings.OCR_MIN_CONFIDENCE_KEY, str(conf_val))
        except Exception:
            pass

    def _load_saved_labels_conf(self):
        """Restore Labels OCR confidence setting from disk or Redis."""
        try:
            from core.user_settings import load_labels_confidence
            conf_val = load_labels_confidence()
            int_val = int(round(conf_val * 100))
            if hasattr(self, "slider_labels_conf"):
                self.slider_labels_conf.blockSignals(True)
                self.slider_labels_conf.setValue(int_val)
                self.slider_labels_conf.blockSignals(False)
            if hasattr(self, "lbl_labels_conf_val"):
                self.lbl_labels_conf_val.setText(f"{int_val}%")
            self.redis_client.set(settings.LABELS_MIN_CONFIDENCE_KEY, str(conf_val))
        except Exception:
            pass

    def _load_saved_grayscale(self):
        """Restore Grayscale parameter settings from disk or Redis."""
        try:
            from core.user_settings import load_grayscale_parameter
            for prefix in ['labels', 'date']:
                val = load_grayscale_parameter(prefix)
                spin = getattr(self, f"spin_{prefix}_grayscale", None)
                if spin:
                    spin.blockSignals(True)
                    spin.setValue(val)
                    spin.blockSignals(False)
                key = settings.ROI_LABELS_GRAYSCALE if prefix == 'labels' else settings.ROI_DATE_GRAYSCALE
                self.redis_client.set(key, str(val))
        except Exception:
            pass

    def _on_grayscale_changed(self, val, prefix='date'):
        """Handle Grayscale parameter input box change."""
        key = settings.ROI_LABELS_GRAYSCALE if prefix == 'labels' else settings.ROI_DATE_GRAYSCALE
        self.redis_client.set(key, str(val))
        try:
            from core.user_settings import save_grayscale_parameter
            save_grayscale_parameter(val, prefix)
        except Exception:
            pass

        # Clear old results to force immediate update
        self.actual_lot.clear()
        self.actual_mfg.clear()
        self.actual_exp.clear()
        self.actual_pharma.clear()
        if hasattr(self, "actual_labels"):
            self.actual_labels.clear()

        # Immediate preview update
        if self.current_raw_img is not None:
            self._update_canvases(self.current_raw_img)

        # Reset idle timer for compliance
        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

    def _load_saved_threshold(self):
        """Restore Threshold parameter settings from disk or Redis."""
        try:
            from core.user_settings import load_threshold_parameter
            for prefix in ['labels', 'date']:
                val = load_threshold_parameter(prefix)
                spin = getattr(self, f"spin_{prefix}_threshold", None)
                if spin:
                    spin.blockSignals(True)
                    spin.setValue(val)
                    spin.blockSignals(False)
                key = settings.ROI_LABELS_THRESHOLD if prefix == 'labels' else settings.ROI_DATE_THRESHOLD
                self.redis_client.set(key, str(val))
        except Exception:
            pass

    def _on_threshold_changed(self, val, prefix='date'):
        """Handle Threshold parameter input box change."""
        key = settings.ROI_LABELS_THRESHOLD if prefix == 'labels' else settings.ROI_DATE_THRESHOLD
        self.redis_client.set(key, str(val))
        try:
            from core.user_settings import save_threshold_parameter
            save_threshold_parameter(val, prefix)
        except Exception:
            pass

        # Clear old results to force immediate update
        self.actual_lot.clear()
        self.actual_mfg.clear()
        self.actual_exp.clear()
        self.actual_pharma.clear()
        if hasattr(self, "actual_labels"):
            self.actual_labels.clear()

        # Immediate preview update
        if self.current_raw_img is not None:
            self._update_canvases(self.current_raw_img)

        # Reset idle timer for compliance
        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

    def _update_locator_picture(self):
        """Displays the reference template picture in the Object Locator canvas."""
        if hasattr(self, "locator_canvas") and self.locator.has_template and self.locator.template_img is not None:
            tpl = self.locator.template_img
            if len(tpl.shape) == 2:
                h, w = tpl.shape
                tpl_rgb = cv2.cvtColor(tpl, cv2.COLOR_GRAY2RGB)
            else:
                h, w, ch = tpl.shape
                tpl_rgb = cv2.cvtColor(tpl, cv2.COLOR_BGR2RGB)
            bytes_per_line = 3 * w
            q_img = QImage(tpl_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(q_img)
            self.locator_canvas.setPixmap(pixmap.scaled(self.locator_canvas.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        elif hasattr(self, "locator_canvas"):
            self.locator_canvas.clear()
            self.locator_canvas.setText("No Locator Set")

    def _on_clear_locator(self):
        """Clears the current object locator."""
        self.locator.clear()
        self.locator.sync_to_redis(
            self.redis_client,
            settings.ROI_LOCATOR_KEY,
            settings.ROI_LOCATOR_TEMPLATE_KEY
        )
        self._update_locator_picture()
        self.locator_status_badge.setText("CLEARED")
        self.locator_status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
        self.locator_score_lbl.setText("Match: —")
        self.locator_time_lbl.setText("Locate Time: —")
        if self.current_raw_img is not None:
            self._display_frame(self.current_raw_img)
            self._update_canvases(self.current_raw_img)
        print("[HomePage] Object Locator cleared.")

    def _create_roi_box(self, title, key_prefix):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setObjectName(f"{key_prefix}_roi_frame")
        layout = QVBoxLayout(frame)
        if key_prefix in ("date", "labels"):
            frame.setFixedHeight(125)
        else:
            frame.setFixedHeight(145)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        
        if key_prefix == "labels":
            display_title = "2. OCR Labels (LOT : MFG : EXP :)"
        elif key_prefix == "date":
            display_title = "3. OCR Values (LOT / MFG / EXP)"
        else:
            display_title = "4. Pharma Code"

        lbl_title = QLabel(display_title)
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setObjectName("roi_box_title")
        layout.addWidget(lbl_title)

        roi_content_layout = QHBoxLayout()
        roi_content_layout.setContentsMargins(0, 0, 0, 0)
        roi_content_layout.setSpacing(8)

        canvas = QLabel("ROI Area")
        canvas.setObjectName(f"{key_prefix}_canvas")
        canvas.setAlignment(Qt.AlignCenter)
        canvas.setMinimumSize(160, 90)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        setattr(self, f"{key_prefix}_label", canvas)
        roi_content_layout.addWidget(canvas, 1)

        # Adjustment controls
        controls_frame = QFrame()
        controls_frame.setObjectName("roi_controls_frame")
        controls_frame.setFixedWidth(205)
        ctrl_layout = QVBoxLayout(controls_frame)
        ctrl_layout.setContentsMargins(2, 0, 2, 0)
        ctrl_layout.setSpacing(3)

        if key_prefix == "labels":
            # Row 1: Time, Conf & Status Badge
            st_row = QHBoxLayout()
            time_lbl = QLabel("Time: —")
            time_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            time_lbl.setStyleSheet("color: #cccccc; font-size: 11px; font-weight: bold;")
            setattr(self, f"{key_prefix}_time_lbl", time_lbl)
            st_row.addWidget(time_lbl)

            btn_labels_filter = QPushButton("⚙ Filter")
            btn_labels_filter.setObjectName("adjust_button")
            btn_labels_filter.setFixedHeight(20)
            btn_labels_filter.setStyleSheet("font-size: 10px; font-weight: bold; padding: 1px 6px; background-color: #2b2b2b; color: #00bcd4; border: 1px solid #00bcd4; border-radius: 3px;")
            btn_labels_filter.setToolTip("Configure Labels OCR Image Filter & Quality Parameters")
            btn_labels_filter.clicked.connect(lambda: self._open_ocr_filter_dialog("labels"))
            st_row.addWidget(btn_labels_filter)

            self.labels_conf_lbl = QLabel("Conf: —")
            self.labels_conf_lbl.setAlignment(Qt.AlignCenter)
            self.labels_conf_lbl.setStyleSheet("color: #00bcd4; font-size: 11px; font-weight: bold;")
            st_row.addWidget(self.labels_conf_lbl)

            status_badge = QLabel("NO ROI")
            status_badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
            setattr(self, f"{key_prefix}_status_badge", status_badge)
            st_row.addWidget(status_badge)
            ctrl_layout.addLayout(st_row)

            # Row 2: Confidence Threshold Slider (Bar from 0 to 99, default 0%)
            labels_conf_block = QVBoxLayout()
            labels_conf_block.setSpacing(1)

            labels_conf_header = QHBoxLayout()
            lbl_labels_conf_title = QLabel("Min Conf:")
            lbl_labels_conf_title.setStyleSheet("color: #aaaaaa; font-size: 10px;")
            self.lbl_labels_conf_val = QLabel("0%")
            self.lbl_labels_conf_val.setStyleSheet("color: #00bcd4; font-size: 10px; font-weight: bold;")
            labels_conf_header.addWidget(lbl_labels_conf_title)
            labels_conf_header.addStretch()
            labels_conf_header.addWidget(self.lbl_labels_conf_val)
            labels_conf_block.addLayout(labels_conf_header)

            self.slider_labels_conf = QSlider(Qt.Horizontal)
            self.slider_labels_conf.setObjectName("labels_conf_slider")
            self.slider_labels_conf.setRange(0, 99)
            self.slider_labels_conf.setValue(0)
            self.slider_labels_conf.valueChanged.connect(self._on_labels_conf_changed)
            labels_conf_block.addWidget(self.slider_labels_conf)

            ctrl_layout.addLayout(labels_conf_block)

            # Row 3: Rotation ComboBox
            rb = QHBoxLayout()
            rb.setSpacing(6)
            rl = QLabel("Rotation:")
            rl.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold;")
            rb.addWidget(rl)

            combo_rot = QComboBox()
            combo_rot.addItems(["0°", "90°", "180°", "270°"])
            saved_rot = self.redis_client.get(settings.ROI_LABELS_ROTATION)
            if saved_rot:
                try:
                    combo_rot.setCurrentText(f"{int(saved_rot)}°")
                except Exception:
                    pass
            combo_rot.setFixedHeight(20)
            combo_rot.currentTextChanged.connect(lambda t, kp=key_prefix: self._set_rotation(kp, t))
            rb.addWidget(combo_rot, 1)
            setattr(self, f"{key_prefix}_rot_combo", combo_rot)
            ctrl_layout.addLayout(rb)

        elif key_prefix == "date":
            # Row 1: Time, Filter, Conf & Status Badge
            st_row = QHBoxLayout()
            time_lbl = QLabel("Time: —")
            time_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            time_lbl.setStyleSheet("color: #cccccc; font-size: 11px; font-weight: bold;")
            setattr(self, f"{key_prefix}_time_lbl", time_lbl)
            st_row.addWidget(time_lbl)

            btn_filter = QPushButton("⚙ Filter")
            btn_filter.setObjectName("adjust_button")
            btn_filter.setFixedHeight(20)
            btn_filter.setStyleSheet("font-size: 10px; font-weight: bold; padding: 1px 6px; background-color: #2b2b2b; color: #00bcd4; border: 1px solid #00bcd4; border-radius: 3px;")
            btn_filter.setToolTip("Configure OCR Image Filter & Print Quality Parameters")
            btn_filter.clicked.connect(lambda: self._open_ocr_filter_dialog("date"))
            st_row.addWidget(btn_filter)

            self.date_conf_lbl = QLabel("Conf: —")
            self.date_conf_lbl.setAlignment(Qt.AlignCenter)
            self.date_conf_lbl.setStyleSheet("color: #00bcd4; font-size: 11px; font-weight: bold;")
            st_row.addWidget(self.date_conf_lbl)

            status_badge = QLabel("NO ROI")
            status_badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
            setattr(self, f"{key_prefix}_status_badge", status_badge)
            st_row.addWidget(status_badge)
            ctrl_layout.addLayout(st_row)

            # Row 2: Confidence Threshold Slider (default 0%)
            ocr_conf_block = QVBoxLayout()
            ocr_conf_block.setSpacing(1)

            ocr_conf_header = QHBoxLayout()
            lbl_ocr_conf_title = QLabel("Min Conf:")
            lbl_ocr_conf_title.setStyleSheet("color: #aaaaaa; font-size: 10px;")
            self.lbl_ocr_conf_val = QLabel("0%")
            self.lbl_ocr_conf_val.setStyleSheet("color: #00bcd4; font-size: 10px; font-weight: bold;")
            ocr_conf_header.addWidget(lbl_ocr_conf_title)
            ocr_conf_header.addStretch()
            ocr_conf_header.addWidget(self.lbl_ocr_conf_val)
            ocr_conf_block.addLayout(ocr_conf_header)

            self.slider_ocr_conf = QSlider(Qt.Horizontal)
            self.slider_ocr_conf.setObjectName("ocr_conf_slider")
            self.slider_ocr_conf.setRange(0, 99)
            self.slider_ocr_conf.setValue(0)
            self.slider_ocr_conf.valueChanged.connect(self._on_ocr_conf_changed)
            ocr_conf_block.addWidget(self.slider_ocr_conf)

            ctrl_layout.addLayout(ocr_conf_block)

            # Row 3: Rotation ComboBox
            rb = QHBoxLayout()
            rb.setSpacing(6)
            rl = QLabel("Rotation:")
            rl.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold;")
            rb.addWidget(rl)

            combo_rot = QComboBox()
            combo_rot.addItems(["0°", "90°", "180°", "270°"])
            saved_rot = self.redis_client.get(settings.ROI_DATE_ROTATION)
            if saved_rot:
                try:
                    combo_rot.setCurrentText(f"{int(saved_rot)}°")
                except Exception:
                    pass
            combo_rot.setFixedHeight(20)
            combo_rot.currentTextChanged.connect(lambda t, kp=key_prefix: self._set_rotation(kp, t))
            rb.addWidget(combo_rot, 1)
            setattr(self, f"{key_prefix}_rot_combo", combo_rot)
            ctrl_layout.addLayout(rb)

        else: # Pharma Code
            st_row = QHBoxLayout()
            time_lbl = QLabel("Time: —")
            time_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            time_lbl.setStyleSheet("color: #cccccc; font-size: 11px; font-weight: bold;")
            setattr(self, f"{key_prefix}_time_lbl", time_lbl)
            st_row.addWidget(time_lbl)

            status_badge = QLabel("NO ROI")
            status_badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
            setattr(self, f"{key_prefix}_status_badge", status_badge)
            st_row.addWidget(status_badge)
            ctrl_layout.addLayout(st_row)

            adj = QHBoxLayout()
            adj.setSpacing(6)

            cb = QVBoxLayout()
            cb.setSpacing(1)
            cl = QLabel("Contrast")
            cl.setAlignment(Qt.AlignCenter)
            cl.setStyleSheet("color: #aaa; font-size: 10px;")
            cb.addWidget(cl)

            spin_con = QDoubleSpinBox()
            spin_con.setObjectName(f"{key_prefix}_contrast_spinbox")
            spin_con.setRange(0.1, 3.0)
            spin_con.setSingleStep(0.1)
            spin_con.setDecimals(1)
            spin_con.setFixedHeight(22)
            spin_con.setAlignment(Qt.AlignCenter)
            spin_con.setStyleSheet(
                "QDoubleSpinBox { background-color: #1e1e1e; color: #00bcd4; font-size: 11px; font-weight: bold; "
                "border: 1px solid #3e3e42; border-radius: 3px; padding-left: 2px; } "
                "QDoubleSpinBox:focus { border: 1px solid #00bcd4; }"
            )
            spin_con.setToolTip("Image contrast multiplier (0.1 to 3.0, default 1.0)")
            saved_con = float(self.redis_client.get(settings.ROI_PHARMA_CONTRAST) or 1.0)
            spin_con.setValue(saved_con)
            spin_con.valueChanged.connect(lambda val, kp=key_prefix: self._on_contrast_changed(kp, val))
            setattr(self, f"spin_{key_prefix}_contrast", spin_con)
            cb.addWidget(spin_con)
            adj.addLayout(cb)

            rb = QVBoxLayout()
            rb.setSpacing(1)
            rl = QLabel("Rotation")
            rl.setAlignment(Qt.AlignCenter)
            rl.setStyleSheet("color: #aaa; font-size: 10px;")
            rb.addWidget(rl)

            combo_rot = QComboBox()
            combo_rot.addItems(["0°", "90°", "180°", "270°"])
            saved_rot = self.redis_client.get(settings.ROI_PHARMA_ROTATION)
            if saved_rot:
                try:
                    combo_rot.setCurrentText(f"{int(saved_rot)}°")
                except Exception:
                    pass
            combo_rot.setFixedHeight(20)
            combo_rot.currentTextChanged.connect(lambda t, kp=key_prefix: self._set_rotation(kp, t))
            rb.addWidget(combo_rot)
            setattr(self, f"{key_prefix}_rot_combo", combo_rot)
            adj.addLayout(rb)

            ctrl_layout.addLayout(adj)
            ctrl_layout.addStretch(1)

        roi_content_layout.addWidget(controls_frame, 0)
        layout.addLayout(roi_content_layout, 1)
        
        return frame

    def _set_roi_mode(self, mode):
        if self.roi_selection_mode == mode:
            self.roi_selection_mode = None
            if hasattr(self, 'btn_select_locator'): self.btn_select_locator.setChecked(False)
            if hasattr(self, 'btn_select_labels'): self.btn_select_labels.setChecked(False)
            self.btn_select_date.setChecked(False)
            self.btn_select_pharma.setChecked(False)
            self.camera_label.selection_color = QColor(0, 255, 0)
        else:
            self.roi_selection_mode = mode
            if hasattr(self, 'btn_select_locator'): self.btn_select_locator.setChecked(mode == 'locator')
            if hasattr(self, 'btn_select_labels'): self.btn_select_labels.setChecked(mode == 'labels')
            self.btn_select_date.setChecked(mode == 'date')
            self.btn_select_pharma.setChecked(mode == 'pharma')
            if mode == 'locator':
                self.camera_label.selection_color = QColor(0, 200, 255)
            elif mode == 'labels':
                self.camera_label.selection_color = QColor(255, 215, 0)
            elif mode == 'date':
                self.camera_label.selection_color = QColor(0, 255, 0)
            else:
                self.camera_label.selection_color = QColor(0, 150, 255)

    def _on_roi_selected(self, rect: QRect):
        if not self.roi_selection_mode or self.current_raw_img is None:
            return

        rect = rect.normalized()

        # Map UI coordinates to actual image coordinates
        img_h, img_w = self.current_raw_img.shape[:2]
        label_size = self.camera_label.size()
        pixmap = self.camera_label.pixmap()
        if not pixmap: return
        
        pw, ph = pixmap.width(), pixmap.height()
        lw, lh = label_size.width(), label_size.height()
        
        off_x = (lw - pw) / 2
        off_y = (lh - ph) / 2
        
        rx1 = int((rect.left() - off_x) * (img_w / pw))
        ry1 = int((rect.top() - off_y) * (img_h / ph))
        rx2 = int((rect.right() - off_x) * (img_w / pw))
        ry2 = int((rect.bottom() - off_y) * (img_h / ph))

        # Normalize and clamp values safely
        x1 = max(0, min(img_w, min(rx1, rx2)))
        x2 = max(0, min(img_w, max(rx1, rx2)))
        y1 = max(0, min(img_h, min(ry1, ry2)))
        y2 = max(0, min(img_h, max(ry1, ry2)))
        
        if (x2 - x1) < 8 or (y2 - y1) < 8:
            print("[HomePage] Selected ROI is too small, ignoring.")
            self._set_roi_mode(None)
            return

        coords = [x1, y1, x2, y2]

        if self.roi_selection_mode == 'locator':
            success = self.locator.set_template(self.current_raw_img, coords)
            if success:
                self.locator.sync_to_redis(
                    self.redis_client,
                    settings.ROI_LOCATOR_KEY,
                    settings.ROI_LOCATOR_TEMPLATE_KEY
                )
                self._update_locator_picture()
                self.locator_score_lbl.setText("Template Set (100%)")
                self.locator_status_badge.setText("READY")
                self.locator_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
            else:
                self.locator_status_badge.setText("ERROR")
                self.locator_status_badge.setStyleSheet("color: white; background-color: #E74C3C; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
                self.locator_score_lbl.setText("Set Failed")
        elif self.roi_selection_mode == 'labels':
            self.redis_client.set(settings.ROI_LABELS_KEY, json.dumps(coords))
        else:
            key = settings.ROI_DATE_KEY if self.roi_selection_mode == 'date' else settings.ROI_PHARMA_KEY
            self.redis_client.set(key, json.dumps(coords))
        
        # Clear old results immediately to show re-processing
        self.actual_lot.clear()
        self.actual_mfg.clear()
        self.actual_exp.clear()
        self.actual_pharma.clear()
        if hasattr(self, "actual_labels"):
            self.actual_labels.clear()

        # Reset idle timer for compliance
        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

        # Immediate update for feedback
        if self.current_raw_img is not None:
            self._display_frame(self.current_raw_img)
            self._update_canvases(self.current_raw_img)
            
        # Reset mode
        self._set_roi_mode(None)

    def _on_contrast_changed(self, prefix, val):
        key = settings.ROI_LABELS_CONTRAST if prefix == 'labels' else (settings.ROI_DATE_CONTRAST if prefix == 'date' else settings.ROI_PHARMA_CONTRAST)
        self.redis_client.set(key, str(round(val, 2)))
        try:
            from core.user_settings import save_contrast_parameter
            save_contrast_parameter(prefix, val)
        except Exception:
            pass

        # Clear old results
        self.actual_lot.clear()
        self.actual_mfg.clear()
        self.actual_exp.clear()
        self.actual_pharma.clear()
        if hasattr(self, "actual_labels"):
            self.actual_labels.clear()

        # Immediate update
        if self.current_raw_img is not None:
            self._update_canvases(self.current_raw_img)

        # Reset idle timer
        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

    def _load_saved_contrast(self):
        """Restore Contrast parameter settings from disk into spinboxes and Redis."""
        try:
            from core.user_settings import load_contrast_parameter
            for prefix in ['labels', 'date', 'pharma']:
                val = load_contrast_parameter(prefix)
                key = settings.ROI_LABELS_CONTRAST if prefix == 'labels' else (settings.ROI_DATE_CONTRAST if prefix == 'date' else settings.ROI_PHARMA_CONTRAST)
                spin = getattr(self, f"spin_{prefix}_contrast", None)
                if spin:
                    spin.blockSignals(True)
                    spin.setValue(val)
                    spin.blockSignals(False)
                self.redis_client.set(key, str(round(val, 2)))
        except Exception:
            pass

    def _load_saved_rotation(self):
        """Restore Rotation combo boxes and Redis from disk settings."""
        try:
            from core.user_settings import load_rotation_parameter
            for prefix in ['labels', 'date', 'pharma']:
                rot = load_rotation_parameter(prefix)
                key = settings.ROI_LABELS_ROTATION if prefix == 'labels' else (settings.ROI_DATE_ROTATION if prefix == 'date' else settings.ROI_PHARMA_ROTATION)
                combo = getattr(self, f"{prefix}_rot_combo", None)
                if combo:
                    combo.blockSignals(True)
                    combo.setCurrentText(f"{rot}°")
                    combo.blockSignals(False)
                self.redis_client.set(key, str(rot))
        except Exception:
            pass

    def _adj_contrast(self, prefix, delta):
        spin = getattr(self, f"spin_{prefix}_contrast", None)
        if spin:
            new_val = max(0.1, min(3.0, round(spin.value() + delta, 1)))
            spin.setValue(new_val)
        else:
            self._on_contrast_changed(prefix, 1.0 + delta)

    def _set_rotation(self, prefix, val_str):
        if prefix == 'labels':
            key = settings.ROI_LABELS_ROTATION
        elif prefix == 'date':
            key = settings.ROI_DATE_ROTATION
        else:
            key = settings.ROI_PHARMA_ROTATION
        angle = int(val_str.rstrip("°"))
        self.redis_client.set(key, str(angle))
        try:
            from core.user_settings import save_rotation_parameter
            save_rotation_parameter(prefix, angle)
        except Exception:
            pass
        
        # Clear old results
        self.actual_lot.clear()
        self.actual_mfg.clear()
        self.actual_exp.clear()
        self.actual_pharma.clear()
        if hasattr(self, "actual_labels"):
            self.actual_labels.clear()

        # Immediate update
        if self.current_raw_img is not None:
            self._update_canvases(self.current_raw_img)

        # Reset idle timer
        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

    def _poll_redis(self):
        try:
            # 1. Latest Frame
            frame_data = self.redis_client.lrange(settings.RAW_FRAMES_KEY, 0, 0)
            if frame_data:
                payload = json.loads(frame_data[0])
                ts = payload.get("timestamp")
                if ts != self.last_frame_timestamp:
                    self.last_frame_timestamp = ts
                    frame_bytes = bytes.fromhex(payload.get("frame_data"))
                    nparr = np.frombuffer(frame_bytes, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if img is not None:
                        self.current_raw_img = img
                        self._display_frame(img)
                        self._update_canvases(img)

            # 2. Results
            result_data = self.redis_client.lrange(settings.RESULTS_KEY, 0, 0)
            if result_data:
                results = json.loads(result_data[0])
                if "LABELS_STATUS" in results:
                    labels_st = results.get("LABELS_STATUS", "N/A")
                    labels_txt = results.get("LABELS_TEXT", "N/A")
                    labels_err = results.get("LABELS_ERROR", "")
                    if hasattr(self, "actual_labels"):
                        exp_lbl_text = self.expected_labels.text().strip() if hasattr(self, "expected_labels") else ""
                        exp_lbl_words = [w.upper() for w in re.findall(r'[A-Za-z]+', exp_lbl_text)]
                        act_lbl_words = [w.upper() for w in re.findall(r'[A-Za-z]+', labels_txt)]
                        # Apply label normalization to recognized words (e.g. EAP -> EXP, NFG -> MFG)
                        from processing_thread import ProcessingThread
                        norm_act_words = [ProcessingThread._normalize_label_word(w) for w in act_lbl_words]

                        # Strictly pure uppercase text without any conditions or error strings
                        display_txt = " ".join(norm_act_words).upper() if norm_act_words else (labels_txt.upper() if labels_txt not in ("N/A", "EMPTY", "LOCATOR MISSED") else labels_txt)

                        lbl_ok = (labels_st == "PASS")
                        if exp_lbl_words:
                            lbl_ok = (norm_act_words == exp_lbl_words)

                        self.actual_labels.setText(display_txt)
                        if lbl_ok and display_txt not in ("N/A", "EMPTY", "LOCATOR MISSED"):
                            self.actual_labels.setStyleSheet("color: #2ECC71; font-weight: bold;")
                        elif display_txt in ("N/A", "EMPTY", "LOCATOR MISSED"):
                            self.actual_labels.setStyleSheet("")
                        else:
                            self.actual_labels.setStyleSheet("color: #E74C3C; font-weight: bold;")
                    if hasattr(self, "labels_status_badge"):
                        if labels_st == "PASS":
                            self.labels_status_badge.setText("PASS")
                            self.labels_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                        elif labels_st == "FAIL":
                            self.labels_status_badge.setText("FAIL")
                            self.labels_status_badge.setStyleSheet("color: white; background-color: #E74C3C; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                        else:
                            self.labels_status_badge.setText(labels_st)
                            self.labels_status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                    if hasattr(self, "labels_time_lbl") and "labels_time_ms" in results:
                        self.labels_time_lbl.setText(f"Time: {results['labels_time_ms']}ms")
                    if hasattr(self, "labels_conf_lbl") and "labels_conf" in results:
                        c_val = results.get("labels_conf", 0.0)
                        if c_val > 0:
                            self.labels_conf_lbl.setText(f"Conf: {c_val:.1f}%")
                        else:
                            self.labels_conf_lbl.setText("Conf: —")

                # 2.2 OCR Values (LOT / MFG / EXP)
                act_lot = results.get("LOT", "N/A")
                act_mfg = results.get("MFG", "N/A")
                act_exp = results.get("EXP", "N/A")
                ocr_conf = float(results.get("ocr_conf", 0.0))

                self.actual_lot.setText(act_lot)
                self.actual_mfg.setText(act_mfg)
                self.actual_exp.setText(act_exp)

                if hasattr(self, "date_time_lbl") and "ocr_time_ms" in results:
                    self.date_time_lbl.setText(f"Time: {results['ocr_time_ms']}ms")
                if hasattr(self, "date_conf_lbl"):
                    if ocr_conf > 0:
                        self.date_conf_lbl.setText(f"Conf: {ocr_conf:.1f}%")
                    else:
                        self.date_conf_lbl.setText("Conf: —")

                has_date_roi = self.redis_client.exists(settings.ROI_DATE_KEY)
                if not has_date_roi:
                    if hasattr(self, "date_status_badge"):
                        self.date_status_badge.setText("NO ROI")
                        self.date_status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                    self.actual_lot.setStyleSheet("")
                    self.actual_mfg.setStyleSheet("")
                    self.actual_exp.setStyleSheet("")
                else:
                    exp_lot_raw = self.expected_lot.text().strip()
                    exp_mfg_raw = self.expected_mfg.text().strip()
                    exp_exp_raw = self.expected_exp.text().strip()

                    norm_act_lot = self._normalize_val(act_lot, "LOT")
                    norm_exp_lot = self._normalize_val(exp_lot_raw, "LOT")
                    lot_matches = True
                    if exp_lot_raw:
                        lot_ok = (norm_act_lot == norm_exp_lot) or (
                            norm_exp_lot and norm_act_lot.startswith(norm_exp_lot) and len(norm_act_lot) == len(norm_exp_lot) + 1
                        )
                        if lot_ok and act_lot not in ("N/A", "EMPTY", "LOCATOR MISSED"):
                            self.actual_lot.setStyleSheet("color: #2ECC71; font-weight: bold;")
                        else:
                            self.actual_lot.setStyleSheet("color: #E74C3C; font-weight: bold;")
                            lot_matches = False
                    else:
                        self.actual_lot.setStyleSheet("")

                    norm_act_mfg = self._normalize_val(act_mfg, "MFG")
                    norm_exp_mfg = self._normalize_val(exp_mfg_raw, "MFG")
                    mfg_matches = True
                    if exp_mfg_raw:
                        if norm_act_mfg == norm_exp_mfg and act_mfg not in ("N/A", "EMPTY", "LOCATOR MISSED"):
                            self.actual_mfg.setStyleSheet("color: #2ECC71; font-weight: bold;")
                        else:
                            self.actual_mfg.setStyleSheet("color: #E74C3C; font-weight: bold;")
                            mfg_matches = False
                    else:
                        self.actual_mfg.setStyleSheet("")

                    norm_act_exp = self._normalize_val(act_exp, "EXP")
                    norm_exp_exp = self._normalize_val(exp_exp_raw, "EXP")
                    exp_matches = True
                    if exp_exp_raw:
                        if norm_act_exp == norm_exp_exp and act_exp not in ("N/A", "EMPTY", "LOCATOR MISSED"):
                            self.actual_exp.setStyleSheet("color: #2ECC71; font-weight: bold;")
                        else:
                            self.actual_exp.setStyleSheet("color: #E74C3C; font-weight: bold;")
                            exp_matches = False
                    else:
                        self.actual_exp.setStyleSheet("")

                    min_ocr_conf = self.slider_ocr_conf.value() if hasattr(self, "slider_ocr_conf") else 0
                    conf_ok = (ocr_conf >= min_ocr_conf) if min_ocr_conf > 0 else True
                    
                    any_field_expected = bool(exp_lot_raw or exp_mfg_raw or exp_exp_raw)
                    has_error_text = any(x in (act_lot, act_mfg, act_exp) for x in ("LOCATOR MISSED", "EMPTY"))

                    # Check for Smudged Characters in Date / Values ROI
                    smudged_values = [s for s in results.get("smudged_chars", []) if s.get("field") in ("LOT", "MFG", "EXP", "VALUES")]
                    smudged_fields = {s.get("field") for s in smudged_values}

                    if "LOT" in smudged_fields:
                        lot_matches = False
                        self.actual_lot.setText(f"{act_lot} [مطموس]")
                        self.actual_lot.setStyleSheet("color: #E74C3C; font-weight: bold;")

                    if "MFG" in smudged_fields:
                        mfg_matches = False
                        self.actual_mfg.setText(f"{act_mfg} [مطموس]")
                        self.actual_mfg.setStyleSheet("color: #E74C3C; font-weight: bold;")

                    if "EXP" in smudged_fields:
                        exp_matches = False
                        self.actual_exp.setText(f"{act_exp} [مطموس]")
                        self.actual_exp.setStyleSheet("color: #E74C3C; font-weight: bold;")

                    if any_field_expected:
                        date_all_pass = lot_matches and mfg_matches and exp_matches and conf_ok and not has_error_text and not smudged_values
                    else:
                        date_all_pass = conf_ok and not has_error_text and not smudged_values and any(x not in ("N/A", "") for x in (act_lot, act_mfg, act_exp))

                    if hasattr(self, "date_status_badge"):
                        if date_all_pass:
                            self.date_status_badge.setText("PASS")
                            self.date_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                        else:
                            badge_text = "FAIL [مطموس]" if smudged_values else "FAIL"
                            self.date_status_badge.setText(badge_text)
                            self.date_status_badge.setStyleSheet("color: white; background-color: #E74C3C; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")

                # 2.3 Pharma Code
                act_pharma = str(results.get("PHARMA_CODE", "N/A")).strip()
                self.actual_pharma.setText(act_pharma)
                if hasattr(self, "pharma_time_lbl") and "pharma_time_ms" in results:
                    self.pharma_time_lbl.setText(f"Time: {results['pharma_time_ms']}ms")

                has_pharma_roi = self.redis_client.exists(settings.ROI_PHARMA_KEY)
                if not has_pharma_roi:
                    if hasattr(self, "pharma_status_badge"):
                        self.pharma_status_badge.setText("NO ROI")
                        self.pharma_status_badge.setStyleSheet("color: white; background-color: #555555; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                    self.actual_pharma.setStyleSheet("")
                else:
                    exp_pharma = self.expected_pharma.text().strip()
                    if exp_pharma:
                        pharma_pass = (act_pharma == exp_pharma and act_pharma not in ("N/A", "EMPTY", "LOCATOR MISSED"))
                        if pharma_pass:
                            self.actual_pharma.setStyleSheet("color: #2ECC71; font-weight: bold;")
                            if hasattr(self, "pharma_status_badge"):
                                self.pharma_status_badge.setText("PASS")
                                self.pharma_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                        else:
                            self.actual_pharma.setStyleSheet("color: #E74C3C; font-weight: bold;")
                            if hasattr(self, "pharma_status_badge"):
                                self.pharma_status_badge.setText("FAIL")
                                self.pharma_status_badge.setStyleSheet("color: white; background-color: #E74C3C; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                    else:
                        self.actual_pharma.setStyleSheet("")
                        if hasattr(self, "pharma_status_badge"):
                            if act_pharma not in ("N/A", "EMPTY", "LOCATOR MISSED"):
                                self.pharma_status_badge.setText("PASS")
                                self.pharma_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")
                            else:
                                self.pharma_status_badge.setText("FAIL")
                                self.pharma_status_badge.setStyleSheet("color: white; background-color: #E74C3C; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 8px;")

            # 3. Status Indicator (Live signal)
            status = self.redis_client.get(settings.INSPECTION_STATUS_KEY)
            is_pipeline_on = self.redis_client.get(settings.START_PIPELINE_KEY) == b"true"
            
            if not is_pipeline_on:
                if not hasattr(self, "_flash_state"):
                    self._flash_state = True
                if not hasattr(self, "_flash_counter"):
                    self._flash_counter = 0
                
                self._flash_counter += 1
                if self._flash_counter >= 5: # Toggle every 500ms (5 * 100ms)
                    self._flash_counter = 0
                    self._flash_state = not self._flash_state
                
                if self._flash_state:
                    self.status_indicator.setText("SYSTEM IDLE")
                    self.status_indicator.setStyleSheet("background-color: #E74C3C; color: white; border-radius: 5px;")
                else:
                    self.status_indicator.setText("")
                    self.status_indicator.setStyleSheet("background-color: #555555; color: white; border-radius: 5px;")
            else:
                self._flash_state = True
                self._flash_counter = 0
                if status == b"pass":
                    self.status_indicator.setText("MATCH (PASS)")
                    self.status_indicator.setStyleSheet("background-color: #2ECC71; color: white; border-radius: 5px;")
                elif status == b"fail":
                    self.status_indicator.setText("MISMATCH (FAIL)")
                    self.status_indicator.setStyleSheet("background-color: #E74C3C; color: white; border-radius: 5px;")
                else:
                    self.status_indicator.setText("SCANNING...")
                    self.status_indicator.setStyleSheet("background-color: #F1C40F; color: black; border-radius: 5px;")

            # 4. Update Scan Times & Confidences
            ocr_time = results.get("ocr_time_ms", "—")
            pharma_time = results.get("pharma_time_ms", "—")
            self.date_time_lbl.setText(f"Time: {ocr_time}ms")
            self.pharma_time_lbl.setText(f"Scan Time: {pharma_time}ms")

            if hasattr(self, "date_conf_lbl") and "ocr_conf" in results:
                c_val = results.get("ocr_conf", 0.0)
                if c_val > 0:
                    self.date_conf_lbl.setText(f"Conf: {c_val:.1f}%")
                else:
                    self.date_conf_lbl.setText("Conf: —")

            # 5. Locator Info Update
            if "locator_found" in results and results["locator_found"] is not None:
                loc_found = results.get("locator_found", False)
                loc_score = results.get("locator_score", 0.0)
                loc_time = results.get("locator_time_ms", 0.0)
                if loc_found:
                    self.locator_status_badge.setText("FOUND")
                    self.locator_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
                    self.locator_score_lbl.setText(f"Match: {loc_score*100:.1f}%")
                    self.locator_time_lbl.setText(f"Locate Time: {loc_time}ms")
                else:
                    self.locator_status_badge.setText("MISSED")
                    self.locator_status_badge.setStyleSheet("color: white; background-color: #E74C3C; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")
                    self.locator_score_lbl.setText(f"Match: {loc_score*100:.1f}%")
                    self.locator_time_lbl.setText(f"Locate Time: {loc_time}ms")
            elif self.locator.has_template and not is_pipeline_on:
                self.locator_status_badge.setText("READY")
                self.locator_status_badge.setStyleSheet("color: white; background-color: #2ECC71; border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 6px;")

            # 6. Real-time Start Button Validation
            if not self.is_running:
                is_valid = self._check_pre_start_validation()
                self.btn_start.setEnabled(is_valid)
            
            self._was_pipeline_running = is_pipeline_on

        except Exception:
            pass

    def _display_frame(self, img: np.ndarray):
        """Display frame on camera label with ROI bounding boxes.
        Shows Object Locator bounding box, and shifted OCR/Pharmacode bounding boxes.
        Red on mismatch, green otherwise."""
        display_img = img.copy()
        h_img, w_img = img.shape[:2]
        
        # Determine mismatch status for bounding box color
        status = self.redis_client.get(settings.INSPECTION_STATUS_KEY)
        is_pipeline_on = self.redis_client.get(settings.START_PIPELINE_KEY) == b"true"
        is_mismatch = (status == b"fail" and is_pipeline_on)
        box_color = (0, 0, 255) if is_mismatch else (0, 255, 0)  # BGR: red or green
        
        dx, dy = 0, 0
        if self.locator.has_template:
            loc_conf = float(self.redis_client.get(settings.LOCATOR_MIN_CONFIDENCE_KEY) or 0.5)
            loc_res = self.locator.locate(img, min_confidence=loc_conf)
            self.last_locator_result = loc_res
            if loc_res.found and loc_res.bbox:
                bx1, by1, bx2, by2 = loc_res.bbox
                dx, dy = loc_res.dx, loc_res.dy
                # Draw Locator Box in Cyan
                cv2.rectangle(display_img, (bx1, by1), (bx2, by2), (255, 200, 0), 2)
                cv2.putText(display_img, f"LOCATOR {int(loc_res.score * 100)}%", (bx1, max(22, by1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 200, 0), 2)
            else:
                # Draw reference box in Red to indicate missed template
                if self.locator.ref_rect and len(self.locator.ref_rect) >= 4:
                    rx1, ry1, rx2, ry2 = self.locator.ref_rect
                    cv2.rectangle(display_img, (rx1, ry1), (rx2, ry2), (0, 0, 255), 2)
                    cv2.putText(display_img, "LOCATOR MISSED", (rx1, max(22, ry1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)

        # Draw shifted Labels, OCR, and Pharma Code bounding boxes
        for prefix in ['labels', 'date', 'pharma']:
            if prefix == 'labels':
                roi_key = settings.ROI_LABELS_KEY
                color = (0, 215, 255)  # Yellow in BGR
                label_text = "LABELS"
            elif prefix == 'date':
                roi_key = settings.ROI_DATE_KEY
                color = box_color
                label_text = "OCR"
            else:
                roi_key = settings.ROI_PHARMA_KEY
                color = (255, 128, 0)  # Blue in BGR
                label_text = "PHARMA"

            roi_raw = self.redis_client.get(roi_key)
            if roi_raw:
                try:
                    coords = json.loads(roi_raw)
                    shifted = self.locator.shift_roi(coords, dx, dy, (h_img, w_img)) if self.locator.has_template else coords
                    if shifted:
                        x1, y1, x2, y2 = shifted
                        cv2.rectangle(display_img, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(display_img, label_text, (x1, max(22, y1 - 6)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
                except Exception:
                    pass
        
        img_rgb = cv2.cvtColor(display_img, cv2.COLOR_BGR2RGB)
        h, w, ch = img_rgb.shape
        q_img = QImage(img_rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)
        scaled = pixmap.scaled(self.camera_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.camera_label.setPixmap(scaled)

    def _update_canvases(self, img):
        # Compute current displacement for live ROI canvases (reuse cached locate result if available)
        dx, dy = 0, 0
        if self.locator.has_template:
            if self.last_locator_result and self.last_locator_result.found:
                dx, dy = self.last_locator_result.dx, self.last_locator_result.dy
            else:
                loc_conf = float(self.redis_client.get(settings.LOCATOR_MIN_CONFIDENCE_KEY) or 0.5)
                loc_res = self.locator.locate(img, min_confidence=loc_conf)
                self.last_locator_result = loc_res
                if loc_res.found:
                    dx, dy = loc_res.dx, loc_res.dy

        h_img, w_img = img.shape[:2]
        for prefix in ['labels', 'date', 'pharma']:
            if prefix == 'labels':
                roi_key = settings.ROI_LABELS_KEY
                con_key = settings.ROI_LABELS_CONTRAST
                rot_key = settings.ROI_LABELS_ROTATION
                gray_key = settings.ROI_LABELS_GRAYSCALE
                thresh_key = settings.ROI_LABELS_THRESHOLD
            elif prefix == 'date':
                roi_key = settings.ROI_DATE_KEY
                con_key = settings.ROI_DATE_CONTRAST
                rot_key = settings.ROI_DATE_ROTATION
                gray_key = settings.ROI_DATE_GRAYSCALE
                thresh_key = settings.ROI_DATE_THRESHOLD
            else:
                roi_key = settings.ROI_PHARMA_KEY
                con_key = settings.ROI_PHARMA_CONTRAST
                rot_key = settings.ROI_PHARMA_ROTATION
                gray_key = None
                thresh_key = None

            roi_raw = self.redis_client.get(roi_key)
            if roi_raw:
                try:
                    coords = json.loads(roi_raw)
                    shifted = self.locator.shift_roi(coords, dx, dy, (h_img, w_img)) if self.locator.has_template else coords
                    if shifted:
                        x1, y1, x2, y2 = shifted
                        crop = img[y1:y2, x1:x2]
                        if crop.size > 0:
                            # Apply adjustments (locally for preview)
                            contrast = float(self.redis_client.get(con_key) or 1.0)
                            rotation = int(self.redis_client.get(rot_key) or 0)
                            
                            # Apply contrast
                            if contrast != 1.0:
                                crop = cv2.convertScaleAbs(crop, alpha=contrast, beta=0)
                            # Apply rotation
                            if rotation == 90: crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
                            elif rotation == 180: crop = cv2.rotate(crop, cv2.ROTATE_180)
                            elif rotation == 270: crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)

                            # Apply full industrial preprocessing pipeline preview (brightness, gamma, local contrast, smoothing, threshold, connect dots)
                            if gray_key and thresh_key:
                                bright_k = settings.ROI_DATE_BRIGHTNESS if prefix == 'date' else settings.ROI_LABELS_BRIGHTNESS
                                gamma_k = settings.ROI_DATE_GAMMA if prefix == 'date' else settings.ROI_LABELS_GAMMA
                                loc_con_k = settings.ROI_DATE_LOCAL_CONTRAST if prefix == 'date' else settings.ROI_LABELS_LOCAL_CONTRAST
                                smooth_k = settings.ROI_DATE_SMOOTHING if prefix == 'date' else settings.ROI_LABELS_SMOOTHING
                                dots_k = settings.ROI_DATE_CONNECT_DOTS if prefix == 'date' else settings.ROI_LABELS_CONNECT_DOTS

                                b_val = int(self.redis_client.get(bright_k) or self.redis_client.get(gray_key) or 0)
                                gamma_val = float(self.redis_client.get(gamma_k) or 1.0)
                                loc_con_val = float(self.redis_client.get(loc_con_k) or 1.6)
                                smooth_val = int(self.redis_client.get(smooth_k) or 0)
                                dots_val = int(self.redis_client.get(dots_k) or 0)
                                thresh_val = int(self.redis_client.get(thresh_key) or 0)

                                from core.image_processing import preprocess_img
                                crop = preprocess_img(
                                    crop,
                                    brightness=b_val,
                                    gamma=gamma_val,
                                    local_contrast=loc_con_val,
                                    smoothing=smooth_val,
                                    connect_dots=dots_val,
                                    threshold=thresh_val
                                )

                            # Display
                            h, w, ch = crop.shape
                            crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                            q_img = QImage(crop_rgb.data, w, h, ch * w, QImage.Format_RGB888)
                            pixmap = QPixmap.fromImage(q_img)
                            label = getattr(self, f"{prefix}_label", None)
                            if label:
                                label.setPixmap(pixmap.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                except Exception: pass

    def _show_context_menu(self, pos):
        """Shows a context menu on right-click to copy results."""
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        
        menu = QMenu(self)
        copy_action = QAction("Copy All to Expected", self)
        copy_action.triggered.connect(self._on_copy_pressed)
        menu.addAction(copy_action)
        
        # Highlight: Map local position to global screen position
        menu.exec(self.sender().mapToGlobal(pos))

    def _sync_expected_to_redis(self):
        """Immediately synchronize expected inspection fields to Redis and user_settings."""
        try:
            expected = {
                "LOT": self.expected_lot.text().strip(),
                "MFG": self.expected_mfg.text().strip(),
                "EXP": self.expected_exp.text().strip(),
                "PHARMA": self.expected_pharma.text().strip(),
                "LABELS": self.expected_labels.text().strip() if hasattr(self, "expected_labels") else "LOT : MFG : EXP :"
            }
            self.redis_client.set("expected_values", json.dumps(expected))
            from core.user_settings import save_expected_values
            save_expected_values(expected)
        except Exception:
            pass

    def _on_copy_pressed(self):
        """Copies all Actual results to Expected input fields."""
        if hasattr(self, "actual_labels") and hasattr(self, "expected_labels"):
            txt = self.actual_labels.text().strip()
            if txt and txt not in ("N/A", "EMPTY", "LOCATOR MISSED"):
                self.expected_labels.setText(txt)
        self.expected_lot.setText(self.actual_lot.text())
        self.expected_mfg.setText(self.actual_mfg.text())
        self.expected_exp.setText(self.actual_exp.text())
        self.expected_pharma.setText(self.actual_pharma.text())
        self._sync_expected_to_redis()
        
        # Immediate feedback
        print("[HomePage] Copied Actual results to Expected fields.")

    def _on_start_pressed(self):
        # Reset idle timer for major action
        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

        if not self.is_running:
            # Check permission
            if hasattr(self, "main_window") and self.main_window:
                if not self.main_window._can_perform('START MACHINE'):
                    QMessageBox.warning(self, "Access Denied", "You do not have permission to start the test.")
                    return
            
            # Validate
            if not self._validate_inspection():
                return
            
            self.is_running = True
            self.btn_start.setText("Stop")
            self.btn_start.setStyleSheet("background-color: #d9534f;") # Red
            
            # Sync expected values to Redis for backend validation
            self._sync_expected_to_redis()

            self.redis_client.set(settings.START_PIPELINE_KEY, "true")
            self._lock_controls(True)
        else:
            # Check permission
            if hasattr(self, "main_window") and self.main_window:
                if not self.main_window._can_perform('STOP MACHINE'):
                    QMessageBox.warning(self, "Access Denied", "You do not have permission to stop the test.")
                    return
            
            self.is_running = False
            self.btn_start.setText("start Test")
            self.btn_start.setStyleSheet("") # Reset
            self.redis_client.set(settings.START_PIPELINE_KEY, "false")
            self._lock_controls(False)

    def _on_trigger_pressed(self):
        """Request a software/manual trigger via Redis."""
        self.redis_client.set(settings.TRIGGER_REQUEST_KEY, "true")
        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

    def _load_saved_exposure(self):
        """Restore exposure input and Redis from persisted settings."""
        from core.user_settings import load_camera_exposure
        exposure = load_camera_exposure()
        raw = self.redis_client.get(settings.CAMERA_EXPOSURE_KEY)
        if raw:
            try:
                exposure = float(raw)
            except (ValueError, TypeError):
                pass
        display = str(int(exposure)) if exposure == int(exposure) else str(exposure)
        self.exposure_input.setText(display)
        self.redis_client.set(settings.CAMERA_EXPOSURE_KEY, str(exposure))

    def persist_exposure_setting(self):
        """Save current exposure input to disk and Redis (used on Apply and shutdown)."""
        from core.user_settings import save_camera_exposure
        text = self.exposure_input.text().strip()
        try:
            value = float(text)
            if value < 1 or value > 1000000:
                return
            self.redis_client.set(settings.CAMERA_EXPOSURE_KEY, str(value))
            save_camera_exposure(value)
            return
        except ValueError:
            pass
        raw = self.redis_client.get(settings.CAMERA_EXPOSURE_KEY)
        if raw:
            try:
                save_camera_exposure(float(raw))
            except (ValueError, TypeError):
                pass

    def _load_saved_expected_values(self):
        """Restore Expected values from disk into fields and Redis."""
        try:
            from core.user_settings import load_expected_values
            exp = load_expected_values()
            if hasattr(self, "expected_labels") and exp.get("LABELS"):
                self.expected_labels.setText(exp["LABELS"])
            if hasattr(self, "expected_lot") and exp.get("LOT"):
                self.expected_lot.setText(exp["LOT"])
            if hasattr(self, "expected_mfg") and exp.get("MFG"):
                self.expected_mfg.setText(exp["MFG"])
            if hasattr(self, "expected_exp") and exp.get("EXP"):
                self.expected_exp.setText(exp["EXP"])
            if hasattr(self, "expected_pharma") and exp.get("PHARMA"):
                self.expected_pharma.setText(exp["PHARMA"])
            self.redis_client.set("expected_values", json.dumps(exp))
        except Exception:
            pass

    def persist_all_settings(self):
        """Save all UI parameters to disk (user_settings.json) and Redis."""
        try:
            from core.user_settings import (
                save_camera_exposure, save_locator_confidence, save_ocr_confidence,
                save_labels_confidence, save_contrast_parameter, save_rotation_parameter,
                save_grayscale_parameter, save_threshold_parameter, save_expected_values
            )
            # 1. Exposure
            try:
                exp_val = float(self.exposure_input.text().strip())
                if 1 <= exp_val <= 1000000:
                    self.redis_client.set(settings.CAMERA_EXPOSURE_KEY, str(exp_val))
                    save_camera_exposure(exp_val)
            except Exception:
                pass

            # 2. Confidences
            if hasattr(self, "slider_locator_conf"):
                conf = round(self.slider_locator_conf.value() / 100.0, 2)
                self.redis_client.set(settings.LOCATOR_MIN_CONFIDENCE_KEY, str(conf))
                save_locator_confidence(conf)
            if hasattr(self, "slider_ocr_conf"):
                conf = round(self.slider_ocr_conf.value() / 100.0, 2)
                self.redis_client.set(settings.OCR_MIN_CONFIDENCE_KEY, str(conf))
                save_ocr_confidence(conf)
            if hasattr(self, "slider_labels_conf"):
                conf = round(self.slider_labels_conf.value() / 100.0, 2)
                self.redis_client.set(settings.LABELS_MIN_CONFIDENCE_KEY, str(conf))
                save_labels_confidence(conf)

            # 3. Contrasts
            for prefix in ['labels', 'date', 'pharma']:
                spin = getattr(self, f"spin_{prefix}_contrast", None)
                if spin:
                    c_val = round(spin.value(), 2)
                    key = settings.ROI_LABELS_CONTRAST if prefix == 'labels' else (settings.ROI_DATE_CONTRAST if prefix == 'date' else settings.ROI_PHARMA_CONTRAST)
                    self.redis_client.set(key, str(c_val))
                    save_contrast_parameter(prefix, c_val)

            # 4. Rotations
            for prefix in ['labels', 'date', 'pharma']:
                combo = getattr(self, f"{prefix}_rot_combo", None)
                if combo:
                    angle = int(combo.currentText().rstrip("°"))
                    key = settings.ROI_LABELS_ROTATION if prefix == 'labels' else (settings.ROI_DATE_ROTATION if prefix == 'date' else settings.ROI_PHARMA_ROTATION)
                    self.redis_client.set(key, str(angle))
                    save_rotation_parameter(prefix, angle)

            # 5. Grayscale & Threshold
            for prefix in ['labels', 'date']:
                spin_g = getattr(self, f"spin_{prefix}_grayscale", None)
                if spin_g:
                    g_val = spin_g.value()
                    key = settings.ROI_LABELS_GRAYSCALE if prefix == 'labels' else settings.ROI_DATE_GRAYSCALE
                    self.redis_client.set(key, str(g_val))
                    save_grayscale_parameter(g_val, prefix)
                spin_t = getattr(self, f"spin_{prefix}_threshold", None)
                if spin_t:
                    t_val = spin_t.value()
                    key = settings.ROI_LABELS_THRESHOLD if prefix == 'labels' else settings.ROI_DATE_THRESHOLD
                    self.redis_client.set(key, str(t_val))
                    save_threshold_parameter(t_val, prefix)

            # 6. Expected Values
            expected = {
                "LOT": self.expected_lot.text().strip(),
                "MFG": self.expected_mfg.text().strip(),
                "EXP": self.expected_exp.text().strip(),
                "PHARMA": self.expected_pharma.text().strip(),
                "LABELS": self.expected_labels.text().strip() if hasattr(self, "expected_labels") else "LOT : MFG : EXP :"
            }
            self.redis_client.set("expected_values", json.dumps(expected))
            save_expected_values(expected)
            print("[HomePage] Successfully persisted all settings.")
        except Exception as e:
            print(f"[HomePage] Error persisting settings: {e}")

    def _on_apply_exposure(self):
        """Write the exposure value from the input field to Redis and persist it."""
        from core.user_settings import save_camera_exposure
        text = self.exposure_input.text().strip()
        try:
            value = float(text)
            if value < 1 or value > 1000000:
                QMessageBox.warning(self, "Invalid Exposure", "Exposure must be between 1 and 1,000,000 µs.")
                return
            self.redis_client.set(settings.CAMERA_EXPOSURE_KEY, str(value))
            save_camera_exposure(value)
            print(f"[HomePage] Exposure set to {value} µs")
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter a valid number for exposure.")

        if hasattr(self, "main_window") and self.main_window:
            self.main_window.reset_idle_timer()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_locator_picture()
        if self.current_raw_img is not None:
            self._display_frame(self.current_raw_img)
            self._update_canvases(self.current_raw_img)

    def _validate_inspection(self):
        # 1. Check Batch Active
        from models.recipe.recipe import Recipe
        with get_session() as session:
            active_recipe = session.query(Recipe).filter(
                Recipe.status == RecipeStatus.ACTIVE, 
                Recipe.is_deleted == False
            ).first()
            
            if not active_recipe:
                QMessageBox.warning(self, "Validation Error", "No batch is currently active.")
                return False

        # 2. Check Locator ROI
        if not self.redis_client.exists(settings.ROI_LOCATOR_KEY):
            QMessageBox.warning(self, "Validation Error", "Please select Object Locator ROI.")
            return False

        # 3. Check ROIs
        if not self.redis_client.exists(settings.ROI_DATE_KEY) or not self.redis_client.exists(settings.ROI_PHARMA_KEY):
            QMessageBox.warning(self, "Validation Error", "Please select both Date and Pharma ROIs.")
            return False

        return True

    @staticmethod
    def _normalize_val(val, prefix=""):
        """Normalize code for flexible prefix matching."""
        if not val:
            return ""
        s = str(val).strip().upper()
        if prefix:
            p = prefix.upper()
            if s.startswith(p + ":"):
                s = s[len(p) + 1:].strip()
            elif s.startswith(p) and len(s) > len(p) and not s[len(p)].isalnum():
                s = s[len(p):].lstrip(" :.-")
        for generic in ["LOT:", "MFG:", "EXP:", "LOT", "MFG", "EXP", "B.NO:", "BN:", "BATCH:"]:
            if s.startswith(generic):
                s = s[len(generic):].strip(" :.-")
        if prefix.upper() == "LOT":
            return re.sub(r'[^0-9]', '', s)
        elif prefix.upper() in ("MFG", "EXP"):
            m = re.search(r'(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{1,2}[\/\-\.]\d{4}|\d{1,2}[\/\-\.]\d{2})', s)
            if m:
                s = m.group(1).replace(".", "-").replace("/", "-")
                clean = re.sub(r'-+', '-', s).strip('-')
            else:
                s = s.replace(".", "-").replace("/", "-")
                s = re.sub(r'[^0-9\-]', '', s)
                clean = re.sub(r'-+', '-', s).strip('-')
            # 0 vs 6 month disambiguation (e.g. 66-2026 -> 06-2026)
            m_date = re.match(r'^([0-9]{2})-([0-9]{4})$', clean)
            if m_date and m_date.group(1)[0] == '6' and int(m_date.group(1)) > 12:
                clean = f"0{m_date.group(1)[1]}-{m_date.group(2)}"
            return clean
        return s.replace(".", "-").replace("/", "-").strip()

    def _check_pre_start_validation(self) -> bool:
        """Lightweight validation for enabling/disabling Start button."""
        # Check permission
        if hasattr(self, 'main_window') and self.main_window:
            if not self.main_window._can_perform('START MACHINE'):
                return False

        # Check that required inspection ROIs exist (Date and Pharma)
        if not self.redis_client.exists(settings.ROI_DATE_KEY) or not self.redis_client.exists(settings.ROI_PHARMA_KEY):
            return False
            
        return True

    def _lock_controls(self, lock: bool):
        """Locks or unlocks all adjustment and ROI controls."""
        if hasattr(self, "btn_select_locator"):
            self.btn_select_locator.setEnabled(not lock)
        if hasattr(self, "btn_clear_locator"):
            self.btn_clear_locator.setEnabled(not lock)
        if hasattr(self, "slider_locator_conf"):
            self.slider_locator_conf.setEnabled(not lock)
        if hasattr(self, "slider_ocr_conf"):
            self.slider_ocr_conf.setEnabled(not lock)
        if hasattr(self, "slider_labels_conf"):
            self.slider_labels_conf.setEnabled(not lock)
        if hasattr(self, "btn_select_labels"):
            self.btn_select_labels.setEnabled(not lock)
        self.btn_select_date.setEnabled(not lock)
        self.btn_select_pharma.setEnabled(not lock)
        
        # Lock contrast/rotation/threshold controls
        for prefix in ['labels', 'date', 'pharma']:
            container = getattr(self, f"{prefix}_roi_container", None)
            if container:
                # Find all buttons, combo boxes, and spin boxes in the container
                for child in container.findChildren(QPushButton) + container.findChildren(QComboBox) + container.findChildren(QSpinBox) + container.findChildren(QDoubleSpinBox):
                    child.setEnabled(not lock)
                    
        # Lock Expected fields
        if hasattr(self, "expected_labels"):
            self.expected_labels.setEnabled(not lock)
        self.expected_lot.setEnabled(not lock)
        self.expected_mfg.setEnabled(not lock)
        self.expected_exp.setEnabled(not lock)
        self.expected_pharma.setEnabled(not lock)

        # Lock Exposure controls
        self.exposure_input.setEnabled(not lock)
        self.btn_apply_exposure.setEnabled(not lock)

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QSpinBox, QDoubleSpinBox, QPushButton, QGroupBox, QScrollArea, QWidget
)
from PySide6.QtCore import Qt
from core.config import settings
from core.user_settings import (
    load_contrast_parameter, save_contrast_parameter,
    load_brightness_parameter, save_brightness_parameter,
    load_gamma_parameter, save_gamma_parameter,
    load_local_contrast_parameter, save_local_contrast_parameter,
    load_smoothing_parameter, save_smoothing_parameter,
    load_connect_dots_parameter, save_connect_dots_parameter,
    load_threshold_parameter, save_threshold_parameter
)

class OcrFilterDialog(QDialog):
    """
    Dialog for fine-tuning industrial OCR image preprocessing parameters:
    Contrast, Brightness, Gamma, Local Contrast (CLAHE), Smoothing, Connect Dots, and Threshold.
    """
    def __init__(self, parent=None, prefix="date"):
        super().__init__(parent)
        self.prefix = prefix
        self.setWindowTitle(f"OCR Filter & Quality Settings ({prefix.upper()})")
        self.setFixedSize(460, 680)
        self.parent_page = parent
        self.redis_client = getattr(parent, "redis_client", None)
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        # 1. Contrast Group
        grp_contrast = QGroupBox("1. Contrast (التباين)")
        grp_contrast.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 11px;")
        c_layout = QVBoxLayout(grp_contrast)
        c_layout.setSpacing(4)
        c_info = QLabel("Image contrast multiplier (0.10 to 3.00, default 0.50 for labels, 0.80 for values)")
        c_info.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: normal;")
        c_layout.addWidget(c_info)

        c_row = QHBoxLayout()
        self.slider_con = QSlider(Qt.Horizontal)
        self.slider_con.setRange(10, 300)  # 0.10 to 3.00
        c_row.addWidget(self.slider_con, 1)

        self.spin_con = QDoubleSpinBox()
        self.spin_con.setRange(0.10, 3.00)
        self.spin_con.setSingleStep(0.05)
        self.spin_con.setDecimals(2)
        self.spin_con.setFixedWidth(70)
        self.spin_con.setStyleSheet("background-color: #1e1e1e; color: #00bcd4; font-weight: bold; padding: 2px;")
        c_row.addWidget(self.spin_con)
        c_layout.addLayout(c_row)
        layout.addWidget(grp_contrast)

        # 2. Brightness Group
        grp_bright = QGroupBox("2. Brightness (السطوع)")
        grp_bright.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 11px;")
        b_layout = QVBoxLayout(grp_bright)
        b_layout.setSpacing(4)
        b_info = QLabel("Offset applied before contrast/gamma (-100 to +100, default 0)")
        b_info.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: normal;")
        b_layout.addWidget(b_info)

        b_row = QHBoxLayout()
        self.slider_bright = QSlider(Qt.Horizontal)
        self.slider_bright.setRange(-100, 100)
        b_row.addWidget(self.slider_bright, 1)

        self.spin_bright = QSpinBox()
        self.spin_bright.setRange(-100, 100)
        self.spin_bright.setFixedWidth(70)
        self.spin_bright.setStyleSheet("background-color: #1e1e1e; color: #00bcd4; font-weight: bold; padding: 2px;")
        b_row.addWidget(self.spin_bright)
        b_layout.addLayout(b_row)
        layout.addWidget(grp_bright)

        # 3. Gamma Group
        grp_gamma = QGroupBox("3. Gamma Correction (جاما)")
        grp_gamma.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 11px;")
        g_layout = QVBoxLayout(grp_gamma)
        g_layout.setSpacing(4)
        g_info = QLabel("Non-linear tone curve: <1.0 darkens midtones, >1.0 lightens faint print (0.10 to 3.00, default 1.00)")
        g_info.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: normal;")
        g_layout.addWidget(g_info)

        g_row = QHBoxLayout()
        self.slider_gamma = QSlider(Qt.Horizontal)
        self.slider_gamma.setRange(10, 300) # 0.10 to 3.00
        g_row.addWidget(self.slider_gamma, 1)

        self.spin_gamma = QDoubleSpinBox()
        self.spin_gamma.setRange(0.10, 3.00)
        self.spin_gamma.setSingleStep(0.05)
        self.spin_gamma.setDecimals(2)
        self.spin_gamma.setFixedWidth(70)
        self.spin_gamma.setStyleSheet("background-color: #1e1e1e; color: #00bcd4; font-weight: bold; padding: 2px;")
        g_row.addWidget(self.spin_gamma)
        g_layout.addLayout(g_row)
        layout.addWidget(grp_gamma)

        # 4. Local Contrast Group (CLAHE)
        grp_loc_con = QGroupBox("4. Local Contrast / CLAHE (التباين المحلي)")
        grp_loc_con.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 11px;")
        lc_layout = QVBoxLayout(grp_loc_con)
        lc_layout.setSpacing(4)
        lc_info = QLabel("Adaptive histogram clip limit: enhances contrast locally (0.0 to 10.0, default 1.6)")
        lc_info.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: normal;")
        lc_layout.addWidget(lc_info)

        lc_row = QHBoxLayout()
        self.slider_loc_con = QSlider(Qt.Horizontal)
        self.slider_loc_con.setRange(0, 100) # 0.0 to 10.0
        lc_row.addWidget(self.slider_loc_con, 1)

        self.spin_loc_con = QDoubleSpinBox()
        self.spin_loc_con.setRange(0.0, 10.0)
        self.spin_loc_con.setSingleStep(0.1)
        self.spin_loc_con.setDecimals(1)
        self.spin_loc_con.setFixedWidth(70)
        self.spin_loc_con.setStyleSheet("background-color: #1e1e1e; color: #00bcd4; font-weight: bold; padding: 2px;")
        lc_row.addWidget(self.spin_loc_con)
        lc_layout.addLayout(lc_row)
        layout.addWidget(grp_loc_con)

        # 5. Smoothing Group
        grp_smooth = QGroupBox("5. Smoothing (التنعيم)")
        grp_smooth.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 11px;")
        sm_layout = QVBoxLayout(grp_smooth)
        sm_layout.setSpacing(4)
        sm_info = QLabel("Gaussian blur filter to eliminate substrate noise & speckles (0 = Off, 1..15)")
        sm_info.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: normal;")
        sm_layout.addWidget(sm_info)

        sm_row = QHBoxLayout()
        self.slider_smooth = QSlider(Qt.Horizontal)
        self.slider_smooth.setRange(0, 15)
        sm_row.addWidget(self.slider_smooth, 1)

        self.spin_smooth = QSpinBox()
        self.spin_smooth.setRange(0, 15)
        self.spin_smooth.setSpecialValueText("Off")
        self.spin_smooth.setFixedWidth(70)
        self.spin_smooth.setStyleSheet("background-color: #1e1e1e; color: #00bcd4; font-weight: bold; padding: 2px;")
        sm_row.addWidget(self.spin_smooth)
        sm_layout.addLayout(sm_row)
        layout.addWidget(grp_smooth)

        # 6. Connect Dots Group
        grp_dots = QGroupBox("6. Connect Dots (وصل النقاط)")
        grp_dots.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 11px;")
        cd_layout = QVBoxLayout(grp_dots)
        cd_layout.setSpacing(4)
        cd_info = QLabel("Morphological closing bridges disjoint dot-matrix inkjet dots (0 = Off, 1..10)")
        cd_info.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: normal;")
        cd_layout.addWidget(cd_info)

        cd_row = QHBoxLayout()
        self.slider_dots = QSlider(Qt.Horizontal)
        self.slider_dots.setRange(0, 10)
        cd_row.addWidget(self.slider_dots, 1)

        self.spin_dots = QSpinBox()
        self.spin_dots.setRange(0, 10)
        self.spin_dots.setSpecialValueText("Off")
        self.spin_dots.setFixedWidth(70)
        self.spin_dots.setStyleSheet("background-color: #1e1e1e; color: #00bcd4; font-weight: bold; padding: 2px;")
        cd_row.addWidget(self.spin_dots)
        cd_layout.addLayout(cd_row)
        layout.addWidget(grp_dots)

        # 7. Binarization Threshold Group
        grp_thresh = QGroupBox("7. Binarization Threshold (العتبة الثنائية)")
        grp_thresh.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 11px;")
        th_layout = QVBoxLayout(grp_thresh)
        th_layout.setSpacing(4)
        th_info = QLabel("0 = Auto (Otsu binarization) | 1..254 = Manual cutoff | 255 = Off (Grayscale)")
        th_info.setStyleSheet("color: #aaaaaa; font-size: 10px; font-weight: normal;")
        th_layout.addWidget(th_info)

        th_row = QHBoxLayout()
        self.slider_thresh = QSlider(Qt.Horizontal)
        self.slider_thresh.setRange(0, 255)
        th_row.addWidget(self.slider_thresh, 1)

        self.spin_thresh = QSpinBox()
        self.spin_thresh.setRange(0, 255)
        self.spin_thresh.setSpecialValueText("Auto")
        self.spin_thresh.setFixedWidth(70)
        self.spin_thresh.setStyleSheet("background-color: #1e1e1e; color: #00bcd4; font-weight: bold; padding: 2px;")
        th_row.addWidget(self.spin_thresh)
        th_layout.addLayout(th_row)
        layout.addWidget(grp_thresh)

        scroll.setWidget(container)
        main_layout.addWidget(scroll, 1)

        # Connect signals
        # Contrast
        self.slider_con.valueChanged.connect(lambda v: self.spin_con.setValue(round(v / 100.0, 2)))
        self.spin_con.valueChanged.connect(lambda v: self.slider_con.setValue(int(round(v * 100))))
        self.spin_con.valueChanged.connect(self._on_contrast_changed)

        # Brightness
        self.slider_bright.valueChanged.connect(self.spin_bright.setValue)
        self.spin_bright.valueChanged.connect(self.slider_bright.setValue)
        self.spin_bright.valueChanged.connect(self._on_bright_changed)

        # Gamma
        self.slider_gamma.valueChanged.connect(lambda v: self.spin_gamma.setValue(round(v / 100.0, 2)))
        self.spin_gamma.valueChanged.connect(lambda v: self.slider_gamma.setValue(int(round(v * 100))))
        self.spin_gamma.valueChanged.connect(self._on_gamma_changed)

        # Local contrast
        self.slider_loc_con.valueChanged.connect(lambda v: self.spin_loc_con.setValue(round(v / 10.0, 1)))
        self.spin_loc_con.valueChanged.connect(lambda v: self.slider_loc_con.setValue(int(round(v * 10))))
        self.spin_loc_con.valueChanged.connect(self._on_loc_con_changed)

        # Smoothing
        self.slider_smooth.valueChanged.connect(self.spin_smooth.setValue)
        self.spin_smooth.valueChanged.connect(self.slider_smooth.setValue)
        self.spin_smooth.valueChanged.connect(self._on_smooth_changed)

        # Connect Dots
        self.slider_dots.valueChanged.connect(self.spin_dots.setValue)
        self.spin_dots.valueChanged.connect(self.slider_dots.setValue)
        self.spin_dots.valueChanged.connect(self._on_dots_changed)

        # Threshold
        self.slider_thresh.valueChanged.connect(self.spin_thresh.setValue)
        self.spin_thresh.valueChanged.connect(self.slider_thresh.setValue)
        self.spin_thresh.valueChanged.connect(self._on_thresh_changed)

        # Load initial values from disk/settings
        self._load_values()

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_reset = QPushButton("Reset Defaults")
        btn_reset.setStyleSheet("background-color: #3e3e42; color: white; padding: 6px 14px; border-radius: 4px; font-weight: bold;")
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; padding: 6px 18px; border-radius: 4px;")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        main_layout.addLayout(btn_row)

    def _load_values(self):
        p = self.prefix
        self.spin_con.setValue(load_contrast_parameter(p))
        self.spin_bright.setValue(load_brightness_parameter(p))
        self.spin_gamma.setValue(load_gamma_parameter(p))
        self.spin_loc_con.setValue(load_local_contrast_parameter(p))
        self.spin_smooth.setValue(load_smoothing_parameter(p))
        self.spin_dots.setValue(load_connect_dots_parameter(p))
        self.spin_thresh.setValue(load_threshold_parameter(p))

    def _trigger_preview_update(self):
        if self.parent_page and hasattr(self.parent_page, "_update_canvases") and self.parent_page.current_raw_img is not None:
            self.parent_page._update_canvases(self.parent_page.current_raw_img)

    def _on_contrast_changed(self, val):
        val = round(float(val), 2)
        save_contrast_parameter(self.prefix, val)
        if self.redis_client:
            k = settings.ROI_DATE_CONTRAST if self.prefix == "date" else settings.ROI_LABELS_CONTRAST
            self.redis_client.set(k, str(val))
        if self.parent_page:
            for attr in ("actual_lot", "actual_mfg", "actual_exp", "actual_pharma", "actual_labels"):
                field = getattr(self.parent_page, attr, None)
                if field and hasattr(field, "clear"):
                    field.clear()
        self._trigger_preview_update()

    def _on_bright_changed(self, val):
        save_brightness_parameter(val, self.prefix)
        if self.redis_client:
            k = settings.ROI_DATE_BRIGHTNESS if self.prefix == "date" else settings.ROI_LABELS_BRIGHTNESS
            gk = settings.ROI_DATE_GRAYSCALE if self.prefix == "date" else settings.ROI_LABELS_GRAYSCALE
            self.redis_client.set(k, str(val))
            self.redis_client.set(gk, str(val))
        spin = getattr(self.parent_page, f"spin_{self.prefix}_grayscale", None)
        if spin:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
        self._trigger_preview_update()

    def _on_gamma_changed(self, val):
        save_gamma_parameter(val, self.prefix)
        if self.redis_client:
            k = settings.ROI_DATE_GAMMA if self.prefix == "date" else settings.ROI_LABELS_GAMMA
            self.redis_client.set(k, str(val))
        self._trigger_preview_update()

    def _on_loc_con_changed(self, val):
        save_local_contrast_parameter(val, self.prefix)
        if self.redis_client:
            k = settings.ROI_DATE_LOCAL_CONTRAST if self.prefix == "date" else settings.ROI_LABELS_LOCAL_CONTRAST
            self.redis_client.set(k, str(val))
        self._trigger_preview_update()

    def _on_smooth_changed(self, val):
        save_smoothing_parameter(val, self.prefix)
        if self.redis_client:
            k = settings.ROI_DATE_SMOOTHING if self.prefix == "date" else settings.ROI_LABELS_SMOOTHING
            self.redis_client.set(k, str(val))
        self._trigger_preview_update()

    def _on_dots_changed(self, val):
        save_connect_dots_parameter(val, self.prefix)
        if self.redis_client:
            k = settings.ROI_DATE_CONNECT_DOTS if self.prefix == "date" else settings.ROI_LABELS_CONNECT_DOTS
            self.redis_client.set(k, str(val))
        self._trigger_preview_update()

    def _on_thresh_changed(self, val):
        save_threshold_parameter(val, self.prefix)
        if self.redis_client:
            k = settings.ROI_DATE_THRESHOLD if self.prefix == "date" else settings.ROI_LABELS_THRESHOLD
            self.redis_client.set(k, str(val))
        spin = getattr(self.parent_page, f"spin_{self.prefix}_threshold", None)
        if spin:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
        self._trigger_preview_update()

    def _on_reset(self):
        if self.prefix == "labels":
            self.spin_con.setValue(0.50)
            self.spin_bright.setValue(5)
            self.spin_gamma.setValue(0.72)
            self.spin_loc_con.setValue(1.0)
            self.spin_smooth.setValue(1)
            self.spin_dots.setValue(2)
            self.spin_thresh.setValue(0)
        else:  # date
            self.spin_con.setValue(0.80)
            self.spin_bright.setValue(0)
            self.spin_gamma.setValue(1.00)
            self.spin_loc_con.setValue(2.0)
            self.spin_smooth.setValue(1)
            self.spin_dots.setValue(3)
            self.spin_thresh.setValue(0)


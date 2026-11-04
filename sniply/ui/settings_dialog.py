"""Settings dialog exposing the tunable detection parameters from
config/defaults.py, plus the default save location.
"""

from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
)

from sniply.config.defaults import DetectionConfig


class SettingsDialog(QDialog):
    def __init__(self, config: DetectionConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._config = config

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(500, 2000)
        self.interval_spin.setSingleStep(100)
        self.interval_spin.setSuffix(" ms")
        self.interval_spin.setValue(config.capture_interval_ms)

        self.stability_spin = QSpinBox()
        self.stability_spin.setRange(1, 10)
        self.stability_spin.setValue(config.stability_count)

        self.fast_diff_spin = QDoubleSpinBox()
        self.fast_diff_spin.setRange(0.0, 50.0)
        self.fast_diff_spin.setSingleStep(0.5)
        self.fast_diff_spin.setValue(config.fast_diff_threshold)

        self.phash_dup_spin = QSpinBox()
        self.phash_dup_spin.setRange(0, 32)
        self.phash_dup_spin.setValue(config.phash_duplicate_max)

        self.phash_new_spin = QSpinBox()
        self.phash_new_spin.setRange(0, 64)
        self.phash_new_spin.setValue(config.phash_new_min)

        self.ssim_spin = QDoubleSpinBox()
        self.ssim_spin.setRange(0.0, 1.0)
        self.ssim_spin.setSingleStep(0.01)
        self.ssim_spin.setValue(config.ssim_duplicate_threshold)

        form = QFormLayout()
        form.addRow("Capture interval:", self.interval_spin)
        form.addRow("Stability count (ticks):", self.stability_spin)
        form.addRow("Fast-diff \"changed\" threshold:", self.fast_diff_spin)
        form.addRow("Phash duplicate max distance:", self.phash_dup_spin)
        form.addRow("Phash new-content min distance:", self.phash_new_spin)
        form.addRow("SSIM duplicate threshold:", self.ssim_spin)

        ok_button = QPushButton("Save")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row = QHBoxLayout()
        button_row.addWidget(ok_button)
        button_row.addWidget(cancel_button)

        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(button_row)

    def updated_config(self) -> DetectionConfig:
        return DetectionConfig(
            compare_width=self._config.compare_width,
            capture_interval_ms=self.interval_spin.value(),
            fast_diff_threshold=self.fast_diff_spin.value(),
            stability_count=self.stability_spin.value(),
            phash_duplicate_max=self.phash_dup_spin.value(),
            phash_new_min=self.phash_new_spin.value(),
            ssim_duplicate_threshold=self.ssim_spin.value(),
        )

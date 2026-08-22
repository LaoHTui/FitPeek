from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QPlainTextEdit, QSplitter, QVBoxLayout,
)

from extractor import ExtractionCancelled, estimate_outputs, parse_background_windows, process_files


class _ExtractorWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, options):
        super().__init__()
        self.options = options
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True

    def run(self):
        try:
            result = process_files(
                progress=self.progress.emit,
                cancel_check=lambda: self.cancel_requested,
                **self.options,
            )
        except ExtractionCancelled:
            self.finished.emit({"cancelled": True, "files": [], "skipped": []})
        except Exception as exc:  # surfaced in the window without killing Qt
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class ExtractorWindow(QDialog):
    """Single-page targeted event extractor for Fermi/GBM and GECAM."""

    def __init__(self, parent=None, initial_paths=()):
        super().__init__(None, Qt.Window)
        self.host_window = parent
        self.setWindowTitle("Extractor")
        self.settings = getattr(parent, "settings", None)
        self.setMinimumSize(620, 500)
        self.resize(1040, 720)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlags(
            Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self._thread = None
        self._worker = None
        self._initial_paths = tuple(initial_paths or ())
        self._build_ui()
        self._load_settings()
        if self._initial_paths:
            self._add_paths(self._initial_paths)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        intro = QLabel("Batch event FITS extraction")
        intro_font = intro.font(); intro_font.setPointSizeF(intro_font.pointSizeF() + 3); intro_font.setBold(True); intro.setFont(intro_font)
        root.addWidget(intro)
        note = QLabel("Choose the mission first. Files from another mission are skipped and summarized after extraction.")
        note.setWordWrap(True); note.setStyleSheet("color:#64748b;")
        root.addWidget(note)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_files_panel())
        splitter.addWidget(self._build_options_panel())
        splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 2)
        self.splitter = splitter
        root.addWidget(splitter, 1)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output folder"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Choose a folder for generated LC/EVT files")
        output_row.addWidget(self.output_edit, 1)
        self.output_button = QPushButton("Browse..."); self.output_button.clicked.connect(self._choose_output); output_row.addWidget(self.output_button)
        root.addLayout(output_row)

        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.setTextVisible(True)
        root.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500); self.log.setPlaceholderText("Processing messages will appear here")
        root.addWidget(self.log, 1)

        buttons = QHBoxLayout(); buttons.addStretch(1)
        self.run_button = QPushButton("Run extraction"); self.run_button.setDefault(True); self.run_button.clicked.connect(self._run); buttons.addWidget(self.run_button)
        self.cancel_button = QPushButton("Cancel"); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self._cancel); buttons.addWidget(self.cancel_button)
        self.close_button = QPushButton("Close"); self.close_button.clicked.connect(self.close); buttons.addWidget(self.close_button)
        root.addLayout(buttons)

    def _build_files_panel(self):
        panel = QGroupBox("Input files")
        layout = QVBoxLayout(panel)
        self.file_list = QListWidget(); self.file_list.setSelectionMode(QListWidget.ExtendedSelection); layout.addWidget(self.file_list, 1)
        row = QHBoxLayout()
        self.add_session_button = QPushButton("Add loaded session files")
        self.add_session_button.clicked.connect(self._add_session_files)
        row.addWidget(self.add_session_button)
        self.add_files_button = QPushButton("Add files..."); self.add_files_button.clicked.connect(self._add_files); row.addWidget(self.add_files_button)
        self.add_folder_button = QPushButton("Add folder..."); self.add_folder_button.clicked.connect(self._add_folder); row.addWidget(self.add_folder_button)
        self.remove_button = QPushButton("Remove"); self.remove_button.clicked.connect(self._remove_selected); row.addWidget(self.remove_button)
        layout.addLayout(row)
        self.file_info = QLabel("0 files selected"); self.file_info.setStyleSheet("color:#64748b;"); layout.addWidget(self.file_info)
        return panel

    def _build_options_panel(self):
        panel = QGroupBox("Extraction options")
        layout = QVBoxLayout(panel)
        form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.satellite = QComboBox(); self.satellite.addItem("Fermi / GBM", "fermi"); self.satellite.addItem("GECAM", "gecam")
        self.mode = QComboBox(); self.mode.addItem("Light curves + event lists", "both"); self.mode.addItem("Light curves only", "lc"); self.mode.addItem("Event lists only", "evt")
        self.energy_preset = QComboBox(); self.energy_preset.addItem("Custom", "custom"); self.energy_preset.addItem("All energy", "all"); self.energy_preset.addItem("Fermi standard bands", "fermi_default"); self.energy_preset.addItem("GECAM standard bands", "gecam_default")
        self.energy_preset.currentIndexChanged.connect(self._energy_preset_changed)
        self.energy_bands = QLineEdit("8-50,50-100,100-300,300-1000,1000-40000"); self.energy_bands.setPlaceholderText("LOW-HIGH,LOW-HIGH")
        self.bin_widths = QLineEdit("0.01,0.05,0.1"); self.bin_widths.setPlaceholderText("seconds, comma separated")
        self.time_start = QLineEdit(); self.time_start.setPlaceholderText("auto")
        self.time_stop = QLineEdit(); self.time_stop.setPlaceholderText("auto")
        self.background = QLineEdit("-50:-20,70:150"); self.background.setPlaceholderText("none or START:STOP,...")
        form.addRow("Satellite", self.satellite); form.addRow("Output mode", self.mode); form.addRow("Energy preset", self.energy_preset); form.addRow("Energy bands (keV)", self.energy_bands); form.addRow("Bin widths (s)", self.bin_widths)
        form.addRow("Time start (s)", self.time_start); form.addRow("Time stop (s)", self.time_stop); form.addRow("Background windows (s)", self.background)
        layout.addLayout(form)
        self.output_estimate = QLabel("Select files to estimate output count and size.")
        self.output_estimate.setWordWrap(True); self.output_estimate.setStyleSheet("color:#64748b; font-size:11px;")
        layout.addWidget(self.output_estimate)
        self.relative = QCheckBox("Use trigger-relative time when TRIGTIME is available"); self.relative.setChecked(True)
        self.use_gti = QCheckBox("Apply GTI intervals when available"); self.use_gti.setChecked(True)
        self.overlap = QCheckBox("Keep channels overlapping an energy band"); self.overlap.setChecked(False)
        self.combined = QCheckBox("Write combined light curves for each source"); self.combined.setChecked(True)
        for box in (self.relative, self.use_gti, self.overlap, self.combined): layout.addWidget(box)
        hint = QLabel("Fermi uses EVENTS + EBOUNDS. GECAM uses EVENTSnn + EBOUNDS and supports TRIGTIME/BST_TIME.")
        hint.setWordWrap(True); hint.setStyleSheet("color:#64748b;"); layout.addWidget(hint)
        layout.addStretch(1)
        for widget in (self.energy_bands, self.bin_widths, self.time_start, self.time_stop):
            widget.textChanged.connect(self._update_output_estimate)
        for widget in (self.satellite, self.mode):
            widget.currentIndexChanged.connect(self._update_output_estimate)
        for widget in (self.overlap, self.combined):
            widget.toggled.connect(self._update_output_estimate)
        return panel

    def _energy_preset_changed(self, _index):
        preset = self.energy_preset.currentData()
        values = {
            "all": "all",
            "fermi_default": "8-50,50-100,100-300,300-1000,1000-40000",
            "gecam_default": "10-50,50-100,100-300,300-1000,1000-5000",
        }
        self.energy_bands.setEnabled(preset == "custom")
        if preset in values:
            self.energy_bands.setText(values[preset])
        self._update_output_estimate()

    @staticmethod
    def _format_size(value):
        value = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024

    def _update_output_estimate(self, *_args):
        if not hasattr(self, "output_estimate"):
            return
        paths = [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())]
        if not paths:
            self.output_estimate.setText("Select files to estimate output count and size.")
            return
        try:
            widths = [float(value.strip()) for value in self.bin_widths.text().split(",") if value.strip()]
            start = self._optional_float(self.time_start, "Time start")
            stop = self._optional_float(self.time_stop, "Time stop")
            estimate = estimate_outputs(
                paths, self.energy_bands.text().strip() or "all", widths,
                satellite=self.satellite.currentData(), mode=self.mode.currentData(),
                t_start=start, t_stop=stop, overlap=self.overlap.isChecked(),
                write_combined=self.combined.isChecked(),
            )
        except (OSError, ValueError, TypeError):
            self.output_estimate.setText("Complete the options to estimate output count and size.")
            return
        skipped = f"; {estimate['skipped']} selected file(s) would be skipped" if estimate["skipped"] else ""
        self.output_estimate.setText(
            f"Estimated: {estimate['data_files']} data file(s) + manifest, about "
            f"{self._format_size(estimate['estimated_bytes'])}{skipped}."
        )

    def _add_paths(self, paths):
        existing = {self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())}
        for value in paths:
            path = Path(value).expanduser().resolve()
            candidates = sorted(p for p in path.rglob("*") if p.is_file() and p.name.lower().endswith((".fits", ".fit", ".fits.gz", ".fit.gz", ".evt"))) if path.is_dir() else [path]
            for candidate in candidates:
                text = str(candidate)
                if text not in existing:
                    item = QListWidgetItem(candidate.name); item.setToolTip(text); item.setData(Qt.UserRole, text); self.file_list.addItem(item); existing.add(text)
        self.file_info.setText(f"{self.file_list.count()} file(s) selected")
        self._update_output_estimate()

    def _load_settings(self):
        if self.settings is None:
            return
        value = self.settings.value("extractor/inputs", [])
        if isinstance(value, str):
            value = [value]
        self._add_paths([path for path in (value or []) if Path(path).exists()])
        self.output_edit.setText(str(self.settings.value("extractor/output", "")))
        for widget, key in ((self.energy_bands, "energy_bands"), (self.bin_widths, "bin_widths"), (self.time_start, "time_start"), (self.time_stop, "time_stop"), (self.background, "background")):
            widget.setText(str(self.settings.value(f"extractor/{key}", widget.text())))
        mode = self.mode.findData(self.settings.value("extractor/mode", "both"))
        if mode >= 0: self.mode.setCurrentIndex(mode)
        satellite = self.satellite.findData(self.settings.value("extractor/satellite", "fermi"))
        if satellite >= 0: self.satellite.setCurrentIndex(satellite)
        for widget, key in ((self.relative, "relative"), (self.use_gti, "use_gti"), (self.overlap, "overlap"), (self.combined, "combined")):
            raw = self.settings.value(f"extractor/{key}", widget.isChecked())
            widget.setChecked(str(raw).lower() in {"1", "true", "yes", "on"})

    def _save_settings(self):
        if self.settings is None:
            return
        self.settings.setValue("extractor/inputs", [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())])
        self.settings.setValue("extractor/output", self.output_edit.text().strip())
        for widget, key in ((self.energy_bands, "energy_bands"), (self.bin_widths, "bin_widths"), (self.time_start, "time_start"), (self.time_stop, "time_stop"), (self.background, "background")):
            self.settings.setValue(f"extractor/{key}", widget.text())
        self.settings.setValue("extractor/mode", self.mode.currentData())
        self.settings.setValue("extractor/satellite", self.satellite.currentData())
        for widget, key in ((self.relative, "relative"), (self.use_gti, "use_gti"), (self.overlap, "overlap"), (self.combined, "combined")):
            self.settings.setValue(f"extractor/{key}", widget.isChecked())
        self.settings.sync()

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select event FITS files", "", "Event FITS (*.fits *.fit *.fits.gz *.fit.gz *.evt);;All files (*)")
        self._add_paths(paths)

    def _add_session_files(self):
        paths = []
        for path in getattr(self.host_window, "roots", {}):
            if Path(path).exists():
                paths.append(path)
        self._add_paths(paths)

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select folder containing event FITS files")
        if path: self._add_paths([path])

    def _remove_selected(self):
        for item in self.file_list.selectedItems(): self.file_list.takeItem(self.file_list.row(item))
        self.file_info.setText(f"{self.file_list.count()} file(s) selected")
        self._update_output_estimate()

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder")
        if path: self.output_edit.setText(path)

    @staticmethod
    def _optional_float(widget, label):
        text = widget.text().strip()
        if not text: return None
        try: return float(text)
        except ValueError: raise ValueError(f"{label} must be a number or empty")

    def _options(self):
        if not self.file_list.count(): raise ValueError("Select at least one event FITS file")
        output = self.output_edit.text().strip()
        if not output: raise ValueError("Choose an output folder")
        widths = [float(value.strip()) for value in self.bin_widths.text().split(",") if value.strip()]
        if not widths: raise ValueError("Enter at least one bin width")
        return {
            "input_paths": [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())], "output_dir": output,
            "satellite": self.satellite.currentData(),
            "energy_bands": self.energy_bands.text().strip() or "all", "bin_widths": widths, "mode": self.mode.currentData(),
            "use_gti": self.use_gti.isChecked(), "relative_time": self.relative.isChecked(), "t_start": self._optional_float(self.time_start, "Time start"),
            "t_stop": self._optional_float(self.time_stop, "Time stop"), "background_windows": parse_background_windows(self.background.text()),
            "overlap": self.overlap.isChecked(), "write_combined": self.combined.isChecked(),
        }

    def _run(self):
        try: options = self._options()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Invalid options", str(exc)); return
        self.log.clear(); self.log.appendPlainText("Starting extraction..."); self.progress.setValue(0)
        self.run_button.setEnabled(False); self.cancel_button.setEnabled(True); self.close_button.setEnabled(False)
        self._set_input_enabled(False)
        self._thread = QThread(self); self._worker = _ExtractorWorker(options); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run); self._worker.progress.connect(self._on_progress); self._worker.finished.connect(self._on_finished); self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit); self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_finished)
        self._thread.finished.connect(self._thread.deleteLater); self._thread.finished.connect(self._worker.deleteLater); self._thread.start()

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            self.log.appendPlainText("Cancellation requested; finishing the current operation...")

    def _on_progress(self, done, total, message):
        self.progress.setValue(min(100, int(done * 100 / max(total, 1)))); self.log.appendPlainText(message)

    def _on_finished(self, result):
        cancelled = bool(result.get("cancelled"))
        if not cancelled:
            self._on_progress(1, 1, f"Finished: {len(result.get('files', []))} output files")
        self.cancel_button.setEnabled(False)
        if cancelled:
            self.log.appendPlainText("Extraction cancelled.")
            QMessageBox.information(self, "Extraction cancelled", "Extraction stopped. Outputs completed before cancellation were kept.")
            return
        skipped = result.get("skipped", [])
        for item in skipped:
            self.log.appendPlainText(f"Skipped {Path(item['path']).name}: {item['reason']}")
        detail = f"\nSkipped {len(skipped)} file(s) that did not match the selected satellite. See the log for details." if skipped else ""
        QMessageBox.information(self, "Extraction complete", f"Generated {len(result.get('files', []))} files in:\n{result.get('output', '')}{detail}")

    def _on_failed(self, message):
        self.log.appendPlainText(f"Error: {message}"); self.cancel_button.setEnabled(False)
        QMessageBox.critical(self, "Extraction failed", message)

    def _thread_finished(self):
        self._worker = None
        self._thread = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self._set_input_enabled(True)

    def _set_input_enabled(self, enabled):
        for widget in (self.add_session_button, self.add_files_button, self.add_folder_button, self.remove_button, self.output_button, self.satellite, self.mode, self.energy_preset, self.energy_bands, self.bin_widths, self.time_start, self.time_stop, self.background, self.relative, self.use_gti, self.overlap, self.combined):
            widget.setEnabled(enabled)
        if enabled:
            self._energy_preset_changed(self.energy_preset.currentIndex())

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "Extraction in progress", "Wait for the current extraction to finish before closing this window.")
            event.ignore(); return
        self._save_settings()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            self.splitter.setOrientation(Qt.Vertical if self.width() < 820 else Qt.Horizontal)

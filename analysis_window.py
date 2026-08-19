from __future__ import annotations

import html
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QObject, QPointF, QRectF, QSize, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QImageWriter, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout,
    QGraphicsItem, QHBoxLayout, QLabel, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QProgressBar, QSizePolicy, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from light_curve import (
    AnalysisCancelled, automatic_background_windows, compute_light_curve, downsample_envelope,
)
from fits_reader import FITSReader
from app_info import APP_NAME, APP_VERSION

EVENT_ROWS_ROLE = int(Qt.UserRole) + 1
SOURCE_PATH_ROLE = EVENT_ROWS_ROLE + 1


def light_curve_settings_key(path):
    """Return a stable, compact QSettings key for a FITS source path."""
    identity = str(Path(path).resolve()).lower().encode("utf-8", errors="replace")
    return f"lightCurveBackground/{hashlib.sha1(identity).hexdigest()[:16]}"


class CollapsibleSection(QWidget):
    def __init__(self, title, content_layout, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setArrowType(Qt.DownArrow)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        font = self.toggle_button.font()
        font.setBold(True)
        self.toggle_button.setFont(font)
        root.addWidget(self.toggle_button)

        self.content = QFrame()
        self.content.setFrameShape(QFrame.StyledPanel)
        self.content.setLayout(content_layout)
        root.addWidget(self.content)
        self.toggle_button.toggled.connect(self.set_expanded)
        self._update_tooltip(True)

    def is_expanded(self):
        return self.toggle_button.isChecked()

    def set_expanded(self, expanded):
        expanded = bool(expanded)
        if self.toggle_button.isChecked() != expanded:
            self.toggle_button.setChecked(expanded)
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.content.setVisible(expanded)
        self._update_tooltip(expanded)
        self.updateGeometry()

    def _update_tooltip(self, expanded):
        self.toggle_button.setToolTip("Collapse section" if expanded else "Expand section")


def step_series_points(x_values, y_values, single_bin_width=1.0):
    x_values = np.asarray(x_values, dtype=float)
    y_values = np.asarray(y_values, dtype=float)
    if not len(x_values):
        return []
    if len(x_values) == 1:
        half_width = max(float(single_bin_width), 1e-12) * 0.5
        boundaries = np.asarray([x_values[0] - half_width, x_values[0] + half_width])
    else:
        middle = 0.5 * (x_values[:-1] + x_values[1:])
        boundaries = np.concatenate((
            [x_values[0] - (x_values[1] - x_values[0]) * 0.5],
            middle,
            [x_values[-1] + (x_values[-1] - x_values[-2]) * 0.5],
        ))
    points = []
    for index, value in enumerate(y_values):
        points.append(QPointF(float(boundaries[index]), float(value)))
        points.append(QPointF(float(boundaries[index + 1]), float(value)))
    return points


class AnalysisWorker(QObject):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)
    done = Signal()

    def __init__(self, path, config):
        super().__init__()
        self.path = path
        self.config = config

    @Slot()
    def run(self):
        try:
            thread = QThread.currentThread()
            result = compute_light_curve(
                self.path,
                self.config,
                cancelled=thread.isInterruptionRequested,
                progress=self.progress.emit,
            )
            self.result.emit(result)
        except AnalysisCancelled:
            self.failed.emit("Analysis cancelled")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class ExportWorker(QObject):
    saved = Signal(str)
    failed = Signal(str)
    done = Signal()

    def __init__(self, kind, path, result):
        super().__init__()
        self.kind = kind
        self.path = path
        self.result = result

    @Slot()
    def run(self):
        try:
            delimiter = "," if self.path.lower().endswith(".csv") else " "
            comments = "# "
            if self.kind == "events":
                data = self.result["events"].reshape(-1, 1)
                header = _text_header(
                    self.result,
                    "EVENT_TIME_S" if delimiter == "," else "Column 0: Event time (s)",
                )
            else:
                nan_values = np.full(np.asarray(self.result["time_centers"]).shape, np.nan)
                data = np.column_stack([
                    self.result["time_centers"], self.result["counts"], self.result["count_error"],
                    self.result["rate"], self.result["rate_error"],
                    self.result.get("background_rate", nan_values),
                    self.result.get("background_rate_error", nan_values),
                    self.result.get("net_rate", nan_values),
                    self.result.get("net_rate_error", nan_values),
                ])
                columns = (
                    "TIME_CENTER_S,COUNTS,COUNT_ERROR,RATE_PER_S,RATE_ERROR_PER_S,"
                    "BACKGROUND_RATE_PER_S,BACKGROUND_RATE_ERROR_PER_S,NET_RATE_PER_S,NET_RATE_ERROR_PER_S"
                )
                header = _text_header(
                    self.result,
                    f"Columns: {columns.replace(',', ' ')}" if delimiter != "," else columns,
                )
            np.savetxt(self.path, data, delimiter=delimiter, fmt="%.12g", header=header, comments=comments)
            self.saved.emit(self.path)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class ErrorBarItem(QGraphicsItem):
    def __init__(self, chart, series, x_values, y_values, errors):
        super().__init__(chart)
        self.chart = chart
        self.series = series
        self.x_values = np.asarray(x_values, dtype=float)
        self.y_values = np.asarray(y_values, dtype=float)
        self.errors = np.asarray(errors, dtype=float)
        self.setZValue(2)
        chart.plotAreaChanged.connect(lambda area: self.update())

    def boundingRect(self):
        return QRectF(self.chart.boundingRect())

    def paint(self, painter, option, widget=None):
        plot_area = self.chart.plotArea()
        painter.save()
        painter.setClipRect(plot_area)
        painter.setPen(QPen(QColor("#555555"), 1.0))
        cap = 3.0
        for x_value, y_value, error in zip(self.x_values, self.y_values, self.errors):
            low = self.chart.mapToPosition(QPointF(x_value, y_value - error), self.series)
            high = self.chart.mapToPosition(QPointF(x_value, y_value + error), self.series)
            painter.drawLine(low, high)
            painter.drawLine(QPointF(low.x() - cap, low.y()), QPointF(low.x() + cap, low.y()))
            painter.drawLine(QPointF(high.x() - cap, high.y()), QPointF(high.x() + cap, high.y()))
        painter.restore()


class TriggerMarkerItem(QGraphicsItem):
    def __init__(self, chart, series, y_axis):
        super().__init__(chart)
        self.chart = chart
        self.series = series
        self.y_axis = y_axis
        self.setZValue(3)
        chart.plotAreaChanged.connect(lambda area: self.update())

    def boundingRect(self):
        return QRectF(self.chart.boundingRect())

    def paint(self, painter, option, widget=None):
        plot_area = self.chart.plotArea()
        position = self.chart.mapToPosition(QPointF(0.0, self.y_axis.min()), self.series)
        if not plot_area.left() <= position.x() <= plot_area.right():
            return
        painter.save()
        painter.setClipRect(plot_area)
        pen = QPen(QColor("#c43d35"), 1.4, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(position.x(), plot_area.top()), QPointF(position.x(), plot_area.bottom()))
        painter.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        painter.drawText(QPointF(position.x() + 5, plot_area.top() + 16), "T0")
        painter.restore()


class BackgroundWindowItem(QGraphicsItem):
    """Paint translucent off-source windows behind the chart series."""

    def __init__(self, chart, series, x_axis, y_axis, windows):
        super().__init__(chart)
        self.chart = chart
        self.series = series
        self.x_axis = x_axis
        self.y_axis = y_axis
        self.windows = tuple((float(start), float(end)) for start, end in windows)
        self.setZValue(-1)
        chart.plotAreaChanged.connect(lambda _area: self.update())

    def boundingRect(self):
        return QRectF(self.chart.boundingRect())

    def paint(self, painter, option, widget=None):
        plot_area = self.chart.plotArea()
        if plot_area.isEmpty():
            return
        painter.save()
        painter.setClipRect(plot_area)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 121, 107, 34))
        axis_min = self.y_axis.min()
        axis_max = self.y_axis.max()
        x_min = self.x_axis.min()
        x_max = self.x_axis.max()
        for start, end in self.windows:
            start = max(start, x_min)
            end = min(end, x_max)
            if start >= end:
                continue
            left = self.chart.mapToPosition(QPointF(start, axis_min), self.series).x()
            right = self.chart.mapToPosition(QPointF(end, axis_min), self.series).x()
            top = self.chart.mapToPosition(QPointF(start, axis_max), self.series).y()
            bottom = self.chart.mapToPosition(QPointF(start, axis_min), self.series).y()
            painter.drawRect(QRectF(min(left, right), min(top, bottom), abs(right - left), abs(bottom - top)))
        painter.restore()


class ResponsiveChartView(QChartView):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class BackgroundFitDialog(QDialog):
    def __init__(self, file_start, file_end, enabled=True, windows=None, parent=None):
        super().__init__(parent)
        self.file_start = float(file_start)
        self.file_end = float(file_end)
        self.setWindowTitle("Background fit")
        self.resize(520, 390)

        root = QVBoxLayout(self)
        self.enabled_box = QCheckBox("Fit a linear background")
        self.enabled_box.setChecked(bool(enabled))
        root.addWidget(self.enabled_box)

        explanation = QLabel(
            "The initial intervals use the first and last 20% of the full FITS time range. "
            "They are independent of the displayed light-curve time range. Edit them here if needed. "
            "Changing only the display time does not change these intervals; changing energy selection "
            "requires a new fit."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #9aa4b2;")
        root.addWidget(explanation)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Start (s)", "End (s)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        root.addWidget(self.table, 1)

        tools = QHBoxLayout()
        self.add_button = QPushButton("Add interval")
        self.add_button.clicked.connect(self._add_default_interval)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self._remove_selected)
        tools.addWidget(self.add_button)
        tools.addWidget(self.remove_button)
        tools.addStretch()
        root.addLayout(tools)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        initial = windows or automatic_background_windows(self.file_start, self.file_end)
        self._set_intervals(initial)
        self.enabled_box.toggled.connect(self._update_enabled)
        self._update_enabled()

    def _number_box(self, value):
        box = QDoubleSpinBox()
        box.setRange(-1e15, 1e15)
        box.setDecimals(6)
        box.setKeyboardTracking(False)
        box.setValue(float(value))
        return box

    def _set_intervals(self, windows):
        self.table.setRowCount(0)
        for start, end in windows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setCellWidget(row, 0, self._number_box(start))
            self.table.setCellWidget(row, 1, self._number_box(end))

    def _update_enabled(self):
        editable = self.enabled_box.isChecked()
        self.table.setEnabled(self.enabled_box.isChecked())
        self.add_button.setEnabled(editable)
        self.remove_button.setEnabled(editable)

    def _add_default_interval(self):
        duration = self.file_end - self.file_start
        width = max(duration * 0.1, 1e-6)
        if self.table.rowCount():
            start = self.table.cellWidget(self.table.rowCount() - 1, 1).value()
        else:
            start = self.file_start
        end = min(self.file_end, start + width)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setCellWidget(row, 0, self._number_box(start))
        self.table.setCellWidget(row, 1, self._number_box(end))

    def _remove_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def intervals(self):
        return [
            (self.table.cellWidget(row, 0).value(), self.table.cellWidget(row, 1).value())
            for row in range(self.table.rowCount())
        ]

    def _accept_if_valid(self):
        if self.enabled_box.isChecked():
            intervals = self.intervals()
            if not intervals:
                QMessageBox.warning(self, "Background fit", "Add at least one background interval.")
                return
            for start, end in intervals:
                if start >= end:
                    QMessageBox.warning(self, "Background fit", "Every interval start must be smaller than its end.")
                    return
        self.accept()


class LightCurveWindow(QDialog):
    def __init__(self, reader, parent=None):
        super().__init__(parent, Qt.Window)
        self.reader = reader
        self.settings = getattr(parent, "settings", None)
        self.source_entries = []
        self._owned_readers = set()
        self.background_enabled = True
        self.background_windows = []
        self.background_default_intervals = True
        self.background_file_bounds = None
        self.background_fit_signature = None
        self._setting_restore = False
        self.font_scale = getattr(parent, "font_scale", 100)
        self.result = None
        self.analysis_thread = None
        self.analysis_worker = None
        self.export_thread = None
        self.export_worker = None
        self.chart_resize_timer = QTimer(self)
        self.chart_resize_timer.setSingleShot(True)
        self.chart_resize_timer.setInterval(150)
        self.chart_resize_timer.timeout.connect(self.refresh_chart)
        self.setWindowTitle(f"Light Curve - {reader.path.name}")
        self.resize(880, 660)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._build_ui()
        self._load_defaults()

    def _build_ui(self):
        root = QVBoxLayout(self)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source"))
        self.source_label = QLabel(str(self.reader.path))
        self.source_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.source_label.setToolTip(str(self.reader.path))
        self.source_label.setMinimumWidth(0)
        self.source_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        source_row.addWidget(self.source_label, 1)
        self.replace_source_button = QPushButton("Change source...")
        self.replace_source_button.clicked.connect(self.replace_source)
        self.add_source_button = QPushButton("Add FITS...")
        self.add_source_button.clicked.connect(self.add_sources)
        self.related_source_button = QPushButton("Find related...")
        self.related_source_button.clicked.connect(self.add_related_sources)
        source_row.addWidget(self.replace_source_button)
        source_row.addWidget(self.add_source_button)
        source_row.addWidget(self.related_source_button)
        root.addLayout(source_row)

        events_layout = QVBoxLayout()
        events_toolbar = QHBoxLayout()
        select_all_button = QPushButton("Select all")
        select_all_button.clicked.connect(lambda: self._set_all_events(Qt.Checked))
        select_none_button = QPushButton("None")
        select_none_button.clicked.connect(lambda: self._set_all_events(Qt.Unchecked))
        invert_button = QPushButton("Invert")
        invert_button.clicked.connect(self._invert_events)
        self.event_summary = QLabel("0 selected")
        events_toolbar.addWidget(select_all_button)
        events_toolbar.addWidget(select_none_button)
        events_toolbar.addWidget(invert_button)
        events_toolbar.addSpacing(12)
        events_toolbar.addWidget(self.event_summary)
        events_toolbar.addStretch()
        events_layout.addLayout(events_toolbar)
        self.event_list = QListWidget()
        self.event_list.setViewMode(QListView.IconMode)
        self.event_list.setFlow(QListView.LeftToRight)
        self.event_list.setWrapping(True)
        self.event_list.setResizeMode(QListView.Adjust)
        self.event_list.setMovement(QListView.Static)
        self.event_list.setGridSize(QSize(240, 30))
        self.event_list.setSpacing(2)
        self.event_list.setMinimumHeight(96)
        self.event_list.setMaximumHeight(150)
        self.event_list.itemChanged.connect(self._update_event_summary)
        self.event_list.itemChanged.connect(self._mark_background_stale)
        events_layout.addWidget(self.event_list)
        self.events_section = CollapsibleSection("Event HDUs", events_layout)
        root.addWidget(self.events_section)

        grid = QGridLayout()
        self.time_start = self._number_box(-1e15, 1e15, 6)
        self.time_end = self._number_box(-1e15, 1e15, 6)
        self.dt = self._number_box(1e-6, 1e9, 6)
        self.dt.setValue(0.01)
        self.dt.valueChanged.connect(self._mark_background_stale)
        grid.addWidget(QLabel("Time start (s)"), 0, 0)
        grid.addWidget(self.time_start, 0, 1)
        grid.addWidget(QLabel("Time end (s)"), 0, 2)
        grid.addWidget(self.time_end, 0, 3)
        grid.addWidget(QLabel("DT (s)"), 1, 0)
        grid.addWidget(self.dt, 1, 1)

        self.relative_time = QCheckBox("Relative to TRIGTIME")
        self.relative_time.toggled.connect(self._time_reference_changed)
        self.use_gti = QCheckBox("Apply GTI")
        self.use_gti.toggled.connect(self._mark_background_stale)
        self.filter_flag = QCheckBox("Filter FLAG")
        self.flag_value = QSpinBox()
        self.flag_value.setRange(-2147483648, 2147483647)
        self.flag_value.setValue(0)
        self.filter_flag.toggled.connect(self.flag_value.setEnabled)
        self.filter_flag.toggled.connect(self._mark_background_stale)
        self.flag_value.valueChanged.connect(self._mark_background_stale)
        self.filter_evt_type = QCheckBox("Filter EVT_TYPE")
        self.evt_type_value = QSpinBox()
        self.evt_type_value.setRange(-2147483648, 2147483647)
        self.evt_type_value.setValue(1)
        self.filter_evt_type.toggled.connect(self.evt_type_value.setEnabled)
        self.filter_evt_type.toggled.connect(self._mark_background_stale)
        self.evt_type_value.valueChanged.connect(self._mark_background_stale)
        grid.addWidget(self.relative_time, 2, 0, 1, 2)
        grid.addWidget(self.use_gti, 2, 2, 1, 2)

        self.apply_energy = QCheckBox("Energy filter")
        self.apply_energy.toggled.connect(self._energy_filter_toggled)
        self.energy_low = self._number_box(-1e12, 1e12, 4)
        self.energy_high = self._number_box(-1e12, 1e12, 4)
        self.energy_low.valueChanged.connect(self._mark_background_stale)
        self.energy_high.valueChanged.connect(self._mark_background_stale)
        self.apply_energy.toggled.connect(self._mark_background_stale)
        energy_row = QHBoxLayout()
        energy_row.addWidget(self.apply_energy)
        energy_row.addSpacing(12)
        energy_row.addWidget(QLabel("E low"))
        energy_row.addWidget(self.energy_low)
        energy_row.addSpacing(12)
        energy_row.addWidget(QLabel("E high"))
        energy_row.addWidget(self.energy_high)
        energy_row.addStretch()
        grid.addLayout(energy_row, 3, 0, 1, 4)

        event_filter_row = QHBoxLayout()
        event_filter_row.addWidget(self.filter_flag)
        event_filter_row.addWidget(self.flag_value)
        event_filter_row.addSpacing(12)
        event_filter_row.addWidget(self.filter_evt_type)
        event_filter_row.addWidget(self.evt_type_value)
        event_filter_row.addStretch()
        grid.addLayout(event_filter_row, 4, 0, 1, 4)

        self.y_mode = QComboBox()
        self.y_mode.addItem("Counts / bin", "counts")
        self.y_mode.addItem("Count rate / s", "rate")
        self.y_mode.currentIndexChanged.connect(self.refresh_chart)
        grid.addWidget(QLabel("Preview Y"), 1, 2)
        grid.addWidget(self.y_mode, 1, 3)

        background_row = QHBoxLayout()
        self.background_button = QPushButton("Background...")
        self.background_button.clicked.connect(self.configure_background)
        self.background_button.setToolTip(
            "Fixed intervals are based on the full FITS time range. Display-time changes reuse the fit; "
            "energy, binning, GTI, or event-filter changes require refitting."
        )
        self.background_summary = QLabel()
        background_row.addWidget(self.background_button)
        background_row.addWidget(self.background_summary)
        background_row.addStretch()
        grid.addLayout(background_row, 5, 0, 1, 4)
        self._update_background_summary()

        controls = QHBoxLayout()
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run_analysis)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("Ready")
        controls.addWidget(self.run_button)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.progress)
        controls.addWidget(self.status, 1)
        grid.addLayout(controls, 6, 0, 1, 4)
        self.options_section = CollapsibleSection("Binning and filters", grid)
        root.addWidget(self.options_section)

        lower = QVBoxLayout()
        self.preview_stack = QStackedWidget()
        placeholder = QLabel("No result")
        placeholder.setAlignment(Qt.AlignCenter)
        self.preview_stack.addWidget(placeholder)
        self.chart_view = ResponsiveChartView()
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.chart_view.setBackgroundBrush(QBrush(QColor("white")))
        self.chart_view.resized.connect(self._schedule_chart_refresh)
        self.preview_stack.addWidget(self.chart_view)
        lower.addWidget(self.preview_stack, 1)

        save_panel = QHBoxLayout()
        save_panel.addStretch()
        self.save_events_button = QPushButton("Save Events...")
        self.save_events_button.clicked.connect(self.save_events)
        self.save_lc_button = QPushButton("Save Light Curve Data...")
        self.save_lc_button.clicked.connect(self.save_light_curve)
        self.save_image_button = QPushButton("Save Image...")
        self.save_image_button.clicked.connect(self.save_image)
        for button in (self.save_events_button, self.save_lc_button, self.save_image_button):
            button.setEnabled(False)
            save_panel.addWidget(button)
        lower.addLayout(save_panel)
        root.addLayout(lower, 1)

    @staticmethod
    def _number_box(minimum, maximum, decimals):
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(decimals)
        box.setKeyboardTracking(False)
        return box

    def _set_all_events(self, state):
        self.event_list.blockSignals(True)
        for row in range(self.event_list.count()):
            self.event_list.item(row).setCheckState(state)
        self.event_list.blockSignals(False)
        self._update_event_summary()

    def _invert_events(self):
        self.event_list.blockSignals(True)
        for row in range(self.event_list.count()):
            item = self.event_list.item(row)
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        self.event_list.blockSignals(False)
        self._update_event_summary()

    def _update_event_summary(self, item=None):
        selected = [
            self.event_list.item(row)
            for row in range(self.event_list.count())
            if self.event_list.item(row).checkState() == Qt.Checked
        ]
        rows = sum(int(item.data(EVENT_ROWS_ROLE) or 0) for item in selected)
        self.event_summary.setText(f"{len(selected)}/{self.event_list.count()} HDUs | {rows:,} source rows")
        if self.analysis_thread is None:
            self.run_button.setEnabled(bool(selected))

    def _load_defaults(self):
        self.source_entries = [{"path": str(self.reader.path), "reader": self.reader}]
        self._rebuild_event_list()
        candidates = [info for info in self.reader.infos if info.is_table and "TIME" in {field.name.upper() for field in info.fields} and not info.display_name.upper().startswith("GTI")]
        available_columns = {field.name.upper() for info in candidates for field in info.fields}
        self._update_event_summary()
        has_flag = "FLAG" in available_columns
        has_evt_type = "EVT_TYPE" in available_columns
        self.filter_flag.setEnabled(has_flag)
        self.filter_flag.setChecked(has_flag)
        self.flag_value.setEnabled(has_flag)
        self.filter_evt_type.setEnabled(has_evt_type)
        self.filter_evt_type.setChecked(has_evt_type)
        self.evt_type_value.setEnabled(has_evt_type)

        trigtime = self.reader.header_value("TRIGTIME")
        self.trigtime = float(trigtime) if trigtime is not None else None
        self.relative_time.setEnabled(trigtime is not None)
        self.relative_time.blockSignals(True)
        self.relative_time.setChecked(trigtime is not None)
        self.relative_time.blockSignals(False)
        gti_index = self.reader.find_hdu("GTI")
        self.use_gti.setEnabled(gti_index is not None)
        self.use_gti.setChecked(gti_index is not None)
        time_start, time_end = (-30.0, 60.0)
        self.time_start.setValue(time_start)
        self.time_end.setValue(time_end)
        self._initialize_background_windows(candidates)

        ebounds_index = self.reader.find_hdu("EBOUNDS")
        direct_energy = any("ENERGY" in {field.name.upper() for field in info.fields} for info in candidates)
        energy_available = ebounds_index is not None or direct_energy
        self.apply_energy.setEnabled(energy_available)
        self.apply_energy.setChecked(energy_available)
        if ebounds_index is not None:
            fields = [field.name.upper() for field in self.reader.table_schema(ebounds_index)]
            rows = self.reader.read_table_rows(ebounds_index, 0, min(10000, self.reader.infos[ebounds_index].rows or 0))
            if rows and "E_MIN" in fields and "E_MAX" in fields:
                self.energy_low.setValue(min(float(row[fields.index("E_MIN")]) for row in rows))
                self.energy_high.setValue(max(float(row[fields.index("E_MAX")]) for row in rows))
        self.energy_low.setEnabled(energy_available)
        self.energy_high.setEnabled(energy_available)
        self._restore_analysis_settings()

    def _rebuild_event_list(self):
        checked_keys = {
            (self.event_list.item(row).data(SOURCE_PATH_ROLE), int(self.event_list.item(row).data(Qt.UserRole)))
            for row in range(self.event_list.count())
            if self.event_list.item(row).checkState() == Qt.Checked
        }
        self.event_list.blockSignals(True)
        self.event_list.clear()
        for entry in self.source_entries:
            reader = entry["reader"]
            for info in reader.infos:
                names = {field.name.upper() for field in info.fields}
                if not info.is_table or "TIME" not in names or info.display_name.upper().startswith("GTI"):
                    continue
                item = QListWidgetItem(f"{Path(entry['path']).name} [{info.index}] {info.display_name}")
                item.setData(Qt.UserRole, info.index)
                item.setData(SOURCE_PATH_ROLE, entry["path"])
                item.setData(EVENT_ROWS_ROLE, info.rows or 0)
                item.setToolTip(f"{entry['path']} | {info.display_name} | {info.rows or 0:,} rows")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                key = (entry["path"], info.index)
                item.setCheckState(Qt.Checked if not checked_keys or key in checked_keys else Qt.Unchecked)
                self.event_list.addItem(item)
        self.event_list.blockSignals(False)
        self._update_event_summary()

    def _source_signature(self, reader):
        identity = {
            "OBJECT": reader.header_value("OBJECT", "") or reader.header_value("SRC_NAME", ""),
            "TELESCOP": reader.header_value("TELESCOP", ""),
            "INSTRUME": reader.header_value("INSTRUME", ""),
            "OBS_ID": reader.header_value("OBS_ID", "") or reader.header_value("OBSID", ""),
            "TIMESYS": reader.header_value("TIMESYS", ""),
            "TIMEUNIT": reader.header_value("TIMEUNIT", ""),
            "MJDREF": reader.header_value("MJDREF", "") or (
                f"{reader.header_value('MJDREFI', '')}:{reader.header_value('MJDREFF', '')}"
                if reader.header_value("MJDREFI", "") not in (None, "") else ""
            ),
        }
        identity = {key: str(value or "").strip().upper() for key, value in identity.items()}
        tables = []
        for info in reader.infos:
            names = tuple(field.name.upper() for field in info.fields)
            if info.is_table and "TIME" in names and not info.display_name.upper().startswith("GTI"):
                fields = tuple((field.name.upper(), str(field.format).upper(), str(field.unit).upper()) for field in info.fields)
                tables.append((info.display_name.upper(), fields))
        return identity, tuple(tables)

    def _validate_new_reader(self, reader):
        if not reader.infos:
            raise ValueError(reader.open_error or "Unable to read FITS file")
        base = self._source_signature(self.source_entries[0]["reader"])
        candidate = self._source_signature(reader)
        for keyword in ("OBJECT", "TELESCOP", "INSTRUME", "OBS_ID", "TIMESYS", "TIMEUNIT", "MJDREF"):
            base_value = base[0].get(keyword, "")
            candidate_value = candidate[0].get(keyword, "")
            if base_value and candidate_value and base_value != candidate_value:
                raise ValueError(f"Source/time mismatch: {keyword} differs ({base_value} vs {candidate_value}).")
        if not any(base[0].get(key) and base[0].get(key) == candidate[0].get(key) for key in ("OBJECT", "OBS_ID")):
            raise ValueError("Source identity cannot be confirmed: matching OBJECT/SRC_NAME or OBS_ID is required.")
        base_formats = {fields for _name, fields in base[1]}
        candidate_formats = {fields for _name, fields in candidate[1]}
        if not base_formats.intersection(candidate_formats):
            raise ValueError("Event table format mismatch: no compatible TIME/event column layout was found.")

    def _append_readers(self, readers):
        existing = {str(Path(entry["path"]).resolve()).lower() for entry in self.source_entries}
        added = 0
        for reader in readers:
            key = str(Path(reader.path).resolve()).lower()
            if key in existing:
                reader.close()
                continue
            self._validate_new_reader(reader)
            self.source_entries.append({"path": str(Path(reader.path)), "reader": reader})
            self._owned_readers.add(reader)
            existing.add(key)
            added += 1
        if added:
            self.source_label.setText(f"{len(self.source_entries)} FITS sources selected")
            self.source_label.setToolTip("\n".join(entry["path"] for entry in self.source_entries))
            self._rebuild_event_list()
            self.background_fit_signature = None
            if self.background_default_intervals:
                self.background_windows = []
                self._initialize_background_windows([])
        return added

    def add_sources(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add FITS sources", str(Path(self.reader.path).parent), "FITS files (*.fits *.fit *.fits.gz *.evt);;All files (*)")
        for path in paths:
            reader = None
            try:
                reader = FITSReader(path)
                self._append_readers([reader])
            except Exception as exc:
                if reader is not None:
                    reader.close()
                QMessageBox.warning(self, "Cannot add source", f"{path}\n\n{exc}")

    def add_related_sources(self):
        directory = Path(self.source_entries[0]["path"]).parent
        existing = {str(Path(entry["path"]).resolve()).lower() for entry in self.source_entries}
        compatible = []
        for path in sorted(directory.iterdir()):
            lower = path.name.lower()
            if not path.is_file() or not lower.endswith((".fits", ".fit", ".fits.gz", ".evt")):
                continue
            if str(path.resolve()).lower() in existing:
                continue
            reader = FITSReader(path)
            try:
                self._validate_new_reader(reader)
            except Exception:
                reader.close()
                continue
            compatible.append(reader)
        if not compatible:
            QMessageBox.information(self, "No related files", "No compatible FITS files were found in the source directory.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Select related FITS files")
        dialog.resize(620, 420)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        for reader in compatible:
            item = QListWidgetItem(reader.path.name)
            item.setData(Qt.UserRole, reader)
            item.setToolTip(str(reader.path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            listing.addItem(item)
        layout.addWidget(listing)
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: [listing.item(row).setCheckState(Qt.Checked) for row in range(listing.count())])
        layout.addWidget(select_all, 0, Qt.AlignLeft)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            selected = [listing.item(row).data(Qt.UserRole) for row in range(listing.count()) if listing.item(row).checkState() == Qt.Checked]
            self._append_readers(selected)
            for reader in compatible:
                if reader not in selected:
                    reader.close()
        else:
            for reader in compatible:
                reader.close()

    def replace_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Change FITS source", str(Path(self.reader.path).parent), "FITS files (*.fits *.fit *.fits.gz *.evt);;All files (*)")
        if not path:
            return
        reader = None
        try:
            reader = FITSReader(path)
            if not reader.infos:
                raise ValueError(reader.open_error or "Unable to read FITS file")
            for owned_reader in self._owned_readers:
                owned_reader.close()
            self._owned_readers = {reader}
            self.reader = reader
            self.source_entries = [{"path": str(Path(path)), "reader": reader}]
            self.background_windows = []
            self.background_file_bounds = None
            self.background_default_intervals = True
            self.background_fit_signature = None
            self.result = None
            self.event_list.clear()
            self.source_label.setText(str(reader.path))
            self.source_label.setToolTip(str(reader.path))
            self._load_defaults()
        except Exception as exc:
            if reader is not None and reader not in self._owned_readers:
                reader.close()
            QMessageBox.critical(self, "Cannot change source", str(exc))

    @Slot(bool)
    def _time_reference_changed(self, relative):
        if self.trigtime is None:
            return
        offset = -self.trigtime if relative else self.trigtime
        self.time_start.setValue(self.time_start.value() + offset)
        self.time_end.setValue(self.time_end.value() + offset)
        self.background_windows = [
            (start + offset, end + offset) for start, end in self.background_windows
        ]
        if self.background_file_bounds is not None:
            self.background_file_bounds = tuple(value + offset for value in self.background_file_bounds)
        self.background_fit_signature = None
        self._update_background_summary()

    @Slot(bool)
    def _energy_filter_toggled(self, enabled):
        available = self.apply_energy.isEnabled()
        self.energy_low.setEnabled(available and enabled)
        self.energy_high.setEnabled(available and enabled)

    def _initialize_background_windows(self, candidates):
        bounds_parts = []
        for entry in self.source_entries:
            indices = [
                info.index for info in entry["reader"].infos
                if info.is_table and "TIME" in {field.name.upper() for field in info.fields}
                and not info.display_name.upper().startswith("GTI")
            ]
            bounds = entry["reader"].time_bounds(
                indices, relative_to_trigtime=self.relative_time.isChecked(),
            )
            if bounds is not None:
                bounds_parts.append(bounds)
        bounds = (
            (min(value[0] for value in bounds_parts), max(value[1] for value in bounds_parts))
            if bounds_parts else (self.time_start.value(), self.time_end.value())
        )
        self.background_file_bounds = tuple(float(value) for value in bounds)
        saved = self._load_background_settings()
        if saved:
            self.background_enabled = bool(saved.get("enabled", True))
            self.background_windows = [tuple(window) for window in saved.get("windows", [])]
            self.background_default_intervals = bool(saved.get("default_intervals", False))
        elif not self.background_windows:
            self.background_windows = list(automatic_background_windows(*self.background_file_bounds))
            self.background_default_intervals = True
        self._update_background_summary()

    def _background_settings_key(self):
        return light_curve_settings_key(self.reader.path)

    def _restore_analysis_settings(self):
        if self.settings is None:
            return
        raw = self.settings.value(self._background_settings_key(), "")
        if not raw:
            return
        try:
            saved = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        self._setting_restore = True
        try:
            if self.relative_time.isEnabled() and "relative_time" in saved:
                self.relative_time.setChecked(bool(saved["relative_time"]))
            for widget, key in (
                (self.use_gti, "use_gti"),
                (self.filter_flag, "filter_flag"), (self.filter_evt_type, "filter_evt_type"),
                (self.apply_energy, "apply_energy"),
            ):
                if widget.isEnabled() and key in saved:
                    widget.setChecked(bool(saved[key]))
            self.time_start.setValue(float(saved.get("time_start", -30.0)))
            self.time_end.setValue(float(saved.get("time_end", 60.0)))
            self.dt.setValue(float(saved.get("dt", self.dt.value())))
            self.flag_value.setValue(int(saved.get("flag_value", self.flag_value.value())))
            self.evt_type_value.setValue(int(saved.get("evt_type_value", self.evt_type_value.value())))
            self.energy_low.setValue(float(saved.get("energy_low", self.energy_low.value())))
            self.energy_high.setValue(float(saved.get("energy_high", self.energy_high.value())))
            mode = self.y_mode.findData(saved.get("y_mode", "counts"))
            if mode >= 0:
                self.y_mode.setCurrentIndex(mode)
            selected = {(str(path), int(index)) for path, index in saved.get("selected_hdus", [])}
            if selected:
                for row in range(self.event_list.count()):
                    item = self.event_list.item(row)
                    item.setCheckState(
                        Qt.Checked if (str(item.data(SOURCE_PATH_ROLE)), int(item.data(Qt.UserRole))) in selected else Qt.Unchecked
                    )
            windows = saved.get("background_windows", [])
            if windows:
                self.background_windows = [tuple(map(float, window)) for window in windows]
                self.background_default_intervals = bool(saved.get("background_default_intervals", False))
            self.background_enabled = bool(saved.get("background_enabled", self.background_enabled))
        finally:
            self._setting_restore = False
        self._update_background_summary()

    def _save_analysis_settings(self, config=None):
        if self.settings is None:
            return
        config = config or self._config()
        selected = [
            [str(item.data(SOURCE_PATH_ROLE)), int(item.data(Qt.UserRole))]
            for row in range(self.event_list.count())
            if (item := self.event_list.item(row)).checkState() == Qt.Checked
        ]
        saved = {
            "time_start": config.get("time_start", -30.0),
            "time_end": config.get("time_end", 60.0),
            "dt": config.get("dt", 0.01),
            "relative_time": config.get("relative_time", False),
            "use_gti": config.get("use_gti", False),
            "filter_flag": config.get("filter_flag", False),
            "flag_value": config.get("flag_value", 0),
            "filter_evt_type": config.get("filter_evt_type", False),
            "evt_type_value": config.get("evt_type_value", 1),
            "apply_energy": config.get("apply_energy", False),
            "energy_low": config.get("energy_low", 0.0),
            "energy_high": config.get("energy_high", 0.0),
            "y_mode": self.y_mode.currentData(),
            "selected_hdus": selected,
            "background_enabled": self.background_enabled,
            "background_windows": self.background_windows,
            "background_default_intervals": self.background_default_intervals,
        }
        self.settings.setValue(self._background_settings_key(), json.dumps(saved, separators=(",", ":")))
        self.settings.sync()

    def _load_background_settings(self):
        if self.settings is None:
            return None
        raw = self.settings.value(self._background_settings_key(), "")
        if not raw:
            return None
        try:
            value = json.loads(str(raw))
            windows = value.get("background_windows", value.get("windows", []))
            if not windows or any(len(window) != 2 or float(window[0]) >= float(window[1]) for window in windows):
                return None
            return {
                "enabled": value.get("background_enabled", value.get("enabled", True)),
                "windows": windows,
                "default_intervals": value.get("background_default_intervals", False),
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _save_background_settings(self):
        self._save_analysis_settings()

    def _mark_background_stale(self, *_):
        if self._setting_restore:
            return
        if self.background_fit_signature is not None:
            self.background_fit_signature = None
            self._update_background_summary()

    def configure_background(self):
        bounds = self.background_file_bounds or (self.time_start.value(), self.time_end.value())
        dialog = BackgroundFitDialog(
            bounds[0],
            bounds[1],
            enabled=self.background_enabled,
            windows=self.background_windows,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.background_enabled = dialog.enabled_box.isChecked()
        self.background_windows = dialog.intervals()
        self.background_default_intervals = False
        self.background_fit_signature = None
        self._save_background_settings()
        self._update_background_summary()

    def _update_background_summary(self):
        if not self.background_enabled:
            self.background_summary.setText("Off (export columns will contain nan)")
        else:
            stale = " · refit required" if self.result is not None and self.background_fit_signature is None else ""
            self.background_summary.setText(f"Linear fit · {len(self.background_windows)} fixed interval(s){stale}")

    def _config(self):
        selected_items = [
            self.event_list.item(row)
            for row in range(self.event_list.count())
            if self.event_list.item(row).checkState() == Qt.Checked
        ]
        if not selected_items:
            raise ValueError("Select at least one event HDU")
        if self.time_start.value() >= self.time_end.value():
            raise ValueError("Time start must be smaller than time end")
        if self.apply_energy.isChecked() and self.energy_low.value() > self.energy_high.value():
            raise ValueError("E low must not exceed E high")
        grouped = {}
        for item in selected_items:
            grouped.setdefault(item.data(SOURCE_PATH_ROLE), []).append(int(item.data(Qt.UserRole)))
        config = {
            "hdu_indices": [int(item.data(Qt.UserRole)) for item in selected_items],
            "paths": list(grouped),
            "sources": [{"path": source_path, "hdu_indices": indices} for source_path, indices in grouped.items()],
            "time_start": self.time_start.value(),
            "time_end": self.time_end.value(),
            "dt": self.dt.value(),
            "relative_time": self.relative_time.isChecked(),
            "use_gti": self.use_gti.isChecked(),
            "filter_flag": self.filter_flag.isChecked(),
            "flag_value": self.flag_value.value(),
            "filter_evt_type": self.filter_evt_type.isChecked(),
            "evt_type_value": self.evt_type_value.value(),
            "apply_energy": self.apply_energy.isChecked(),
            "energy_low": self.energy_low.value(),
            "energy_high": self.energy_high.value(),
            "background_fit": self.background_enabled,
            "background_windows": list(self.background_windows),
            "background_automatic": self.background_default_intervals,
        }
        if (
            self.result is not None
            and self.background_fit_signature == self._background_signature(config)
            and self.result.get("background_fit", {}).get("performed")
        ):
            config["background_cached_fit"] = dict(self.result["background_fit"])
        return config

    def run_analysis(self):
        if self.analysis_thread is not None:
            return
        try:
            config = self._config()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        self._save_analysis_settings(config)
        self.result = None
        self.preview_stack.setCurrentIndex(0)
        self._set_save_enabled(False)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("Starting...")
        thread = QThread(self)
        worker = AnalysisWorker(str(self.reader.path), config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.result.connect(self._on_result)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        thread.finished.connect(self._analysis_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self.analysis_thread, self.analysis_worker = thread, worker
        thread.start()

    def cancel_analysis(self):
        if self.analysis_thread is not None:
            self.analysis_thread.requestInterruption()
            self.status.setText("Cancelling...")

    @Slot(int, str)
    def _on_progress(self, value, text):
        self.progress.setValue(value)
        self.status.setText(text)

    @Slot(object)
    def _on_result(self, result):
        self.result = result
        self.progress.setValue(100)
        tail = result.get("excluded_tail", 0.0)
        tail_text = f" | omitted partial tail {tail:.6g} s" if tail > 1e-10 else ""
        fit = result.get("background_fit", {})
        fit_text = ""
        if fit.get("performed"):
            self.background_windows = [tuple(window) for window in fit.get("windows_s", self.background_windows)]
            self.background_fit_signature = self._background_signature(result.get("config", {}))
            coefficients = fit.get("coefficients", [np.nan, np.nan])
            reuse_text = " (reused)" if fit.get("reused") else ""
            fit_text = f" | background{reuse_text} a={_format_number(coefficients[0])}, b={_format_number(coefficients[1])}, N={fit.get('n_fit_bins', 0)}"
            self.background_summary.setText(f"Linear fit{reuse_text} · a={_format_number(coefficients[0])}, b={_format_number(coefficients[1])}, N={fit.get('n_fit_bins', 0)}")
        elif not self.background_enabled:
            self.background_summary.setText("Off (export columns will contain nan)")
        self.status.setText(f"{len(result['events']):,} events | {len(result['counts']):,} full bins{fit_text}{tail_text}")
        self._set_save_enabled(True)
        self.refresh_chart()
        warnings = fit.get("warnings", [])
        if warnings:
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                self,
                "Background interval warning",
                "\n\n".join(warnings) + "\n\nReview the shaded intervals or choose manual background windows.",
            ))

    @staticmethod
    def _background_signature(config):
        return (
            bool(config.get("apply_energy")),
            float(config.get("energy_low", 0.0)),
            float(config.get("energy_high", 0.0)),
            bool(config.get("filter_flag")),
            int(config.get("flag_value", 0)),
            bool(config.get("filter_evt_type")),
            int(config.get("evt_type_value", 1)),
            bool(config.get("use_gti")),
            float(config.get("dt", 0.0)),
            tuple(tuple(window) for window in config.get("background_windows", [])),
            tuple(
                (str(source.get("path", "")), tuple(int(index) for index in source.get("hdu_indices", [])))
                for source in config.get("sources", [])
            ),
        )

    @Slot(str)
    def _on_failed(self, message):
        self.status.setText(message)
        if message != "Analysis cancelled":
            QMessageBox.critical(self, "Light curve failed", message)

    @Slot()
    def _analysis_finished(self):
        self.analysis_thread = None
        self.analysis_worker = None
        self._update_event_summary()
        self.cancel_button.setEnabled(False)

    def refresh_chart(self):
        if not self.result:
            return
        mode = self.y_mode.currentData()
        full_x = self.result["time_centers"]
        full_y = self.result["counts"] if mode == "counts" else self.result["rate"]
        full_error = self.result["count_error"] if mode == "counts" else self.result["rate_error"]
        pixel_width = max(480, self.chart_view.viewport().width())
        max_step_bins = max(400, min(3000, pixel_width))
        max_error_bars = max(80, min(500, pixel_width // 5))
        x_values, y_values = downsample_envelope(full_x, full_y, max_points=max_step_bins)
        step_points = step_series_points(x_values, y_values, self.result["config"]["dt"])
        self.preview_point_count = len(step_points)
        series = QLineSeries()
        series.replace(step_points)
        source_density = len(full_x) / max(1, pixel_width)
        if source_density > 5:
            line_width = 0.6
        elif source_density > 2:
            line_width = 0.8
        elif source_density > 1:
            line_width = 1.0
        else:
            line_width = 1.4
        series.setPen(QPen(QColor("black"), line_width))
        chart = QChart()
        chart.setTheme(QChart.ChartThemeLight)
        chart.setBackgroundBrush(QBrush(QColor("white")))
        chart.setPlotAreaBackgroundBrush(QBrush(QColor("white")))
        chart.setPlotAreaBackgroundVisible(True)
        chart.setMargins(QMargins(12, 6, 12, 8))
        chart.setTitle(_chart_title(self.result, mode, self.font_scale))
        chart.setTitleBrush(QBrush(QColor("#202124")))
        chart.setTitleFont(self._chart_font(9))
        chart.addSeries(series)
        background_series = None
        plotted_background_y = None
        background_values = np.asarray(self.result.get("background_rate", []), dtype=float)
        background_performed = bool(self.result.get("background_fit", {}).get("performed"))
        if background_performed and background_values.shape == np.asarray(full_x).shape:
            background_y = background_values * self.result["config"]["dt"] if mode == "counts" else background_values
            display_start = float(self.result["config"]["time_start"])
            display_end = float(self.result["config"]["time_end"])
            finite_background = (
                np.isfinite(background_y)
                & (np.asarray(full_x) >= display_start)
                & (np.asarray(full_x) <= display_end)
            )
            if np.any(finite_background):
                background_series = QLineSeries()
                background_series.setName("Linear background")
                plot_background_x = np.asarray(full_x)[finite_background]
                plot_background_y = background_y[finite_background]
                plotted_background_y = plot_background_y
                background_series.replace([
                    QPointF(float(x_value), float(y_value))
                    for x_value, y_value in zip(plot_background_x, plot_background_y)
                ])
                background_pen = QPen(QColor("#00796b"), 2.0, Qt.DashLine)
                background_pen.setCapStyle(Qt.RoundCap)
                background_series.setPen(background_pen)
                chart.addSeries(background_series)
        chart.legend().setVisible(background_series is not None)
        chart.legend().setAlignment(Qt.AlignBottom)
        chart.legend().setLabelColor(QColor("#374151"))
        chart.legend().setFont(self._chart_font(8))
        axis_x = QValueAxis()
        axis_x.setTitleText("Time relative to TRIGTIME (s)" if self.result["relative_time"] else "FITS TIME (s)")
        axis_y = QValueAxis()
        axis_y.setTitleText("Counts / bin" if mode == "counts" else "Count rate / s")
        for axis in (axis_x, axis_y):
            axis.setLabelsBrush(QBrush(QColor("black")))
            axis.setTitleBrush(QBrush(QColor("black")))
            axis.setGridLinePen(QPen(QColor("#d9d9d9"), 1.0))
            axis.setLinePen(QPen(QColor("#444444"), 1.0))
            axis.setLabelsFont(self._chart_font(9))
            axis_title_font = self._chart_font(9)
            axis_title_font.setWeight(QFont.DemiBold)
            axis.setTitleFont(axis_title_font)
        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)
        if background_series is not None:
            background_series.attachAxis(axis_x)
            background_series.attachAxis(axis_y)
        config = self.result["config"]
        axis_start = float(self.result.get("effective_time_start", config["time_start"]))
        axis_end = float(self.result.get("effective_time_end", config["time_end"]))
        duration = axis_end - axis_start
        padding = max(float(config["dt"]) * 0.5, duration * 0.02)
        axis_x.setRange(axis_start - padding, axis_end + padding)
        axis_x.setTickCount(8)
        if len(full_y):
            range_parts = [np.asarray(full_y) - np.asarray(full_error), np.asarray(full_y) + np.asarray(full_error)]
            if background_series is not None:
                range_parts.append(plotted_background_y)
            finite_range = np.concatenate(range_parts)
            finite_range = finite_range[np.isfinite(finite_range)]
            y_min = float(np.min(finite_range)) if len(finite_range) else 0.0
            y_max = float(np.max(finite_range)) if len(finite_range) else 1.0
            if y_min == y_max:
                y_min -= 1.0
                y_max += 1.0
            axis_y.setRange(y_min, y_max)
            axis_y.applyNiceNumbers()
        self.background_window_item = None
        fit_windows = self.result.get("background_fit", {}).get("windows_s", [])
        display_start = float(config["time_start"])
        display_end = float(config["time_end"])
        visible_windows = [
            (max(display_start, float(window[0])), min(display_end, float(window[1])))
            for window in fit_windows
            if min(display_end, float(window[1])) > max(display_start, float(window[0]))
        ]
        if background_series is not None and visible_windows:
            self.background_window_item = BackgroundWindowItem(
                chart, series, axis_x, axis_y, visible_windows,
            )
        self.chart_view.setChart(chart)
        if len(full_x) > max_error_bars:
            error_indices = np.linspace(0, len(full_x) - 1, max_error_bars, dtype=int)
        else:
            error_indices = np.arange(len(full_x), dtype=int)
        self.preview_error_count = len(error_indices)
        self.error_bar_item = ErrorBarItem(
            chart, series, full_x[error_indices], full_y[error_indices], full_error[error_indices]
        )
        self.trigger_item = None
        if self.result["relative_time"] and axis_x.min() <= 0.0 <= axis_x.max():
            self.trigger_item = TriggerMarkerItem(chart, series, axis_y)
        self.preview_stack.setCurrentIndex(1)

    def _chart_font(self, point_size):
        font = QFont("Segoe UI")
        font.setPointSizeF(float(point_size) * self.font_scale / 100.0)
        return font

    def set_font_scale(self, scale):
        self.font_scale = int(scale)
        if self.result:
            self.refresh_chart()

    @Slot()
    def _schedule_chart_refresh(self):
        if self.result:
            self.chart_resize_timer.start()

    def save_events(self):
        if not self.result:
            return
        default = _default_output_path(self.result, "event_sequence", ".txt")
        path, selected = QFileDialog.getSaveFileName(self, "Save filtered events", default, "Text (*.txt);;CSV (*.csv)")
        if path:
            self._start_export("events", _ensure_extension(path, selected))

    def save_light_curve(self):
        if not self.result:
            return
        default = _default_output_path(self.result, "lightcurve", ".txt")
        path, selected = QFileDialog.getSaveFileName(self, "Save light curve data", default, "Text (*.txt);;CSV (*.csv)")
        if path:
            self._start_export("lightcurve", _ensure_extension(path, selected))

    def save_image(self):
        if not self.result:
            return
        default = _default_output_path(self.result, "lightcurve", ".png")
        path, selected = QFileDialog.getSaveFileName(self, "Save light curve image", default, "PNG (*.png);;JPEG (*.jpg *.jpeg)")
        if path:
            path = _ensure_extension(path, selected)
            image = self.chart_view.grab().toImage()
            writer = QImageWriter(path)
            writer.setText("Software", f"{APP_NAME} {APP_VERSION}")
            writer.setText("GeneratedUTC", datetime.now(timezone.utc).isoformat(timespec="seconds"))
            writer.setText("Description", _text_header(self.result, "Rendered light curve image"))
            if not writer.write(image):
                QMessageBox.critical(self, "Save failed", "The image could not be written")
            else:
                self.status.setText(f"Saved {path}")

    def _start_export(self, kind, path):
        if self.export_thread is not None:
            return
        self._set_save_enabled(False)
        self.status.setText("Saving...")
        thread = QThread(self)
        worker = ExportWorker(kind, path, self.result)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.saved.connect(self._export_saved)
        worker.failed.connect(self._export_failed)
        worker.done.connect(thread.quit)
        thread.finished.connect(self._export_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self.export_thread, self.export_worker = thread, worker
        thread.start()

    @Slot()
    def _export_finished(self):
        self.export_thread = None
        self.export_worker = None
        self._set_save_enabled(self.result is not None)

    @Slot(str)
    def _export_saved(self, path):
        self.status.setText(f"Saved {path}")

    @Slot(str)
    def _export_failed(self, message):
        QMessageBox.critical(self, "Save failed", message)

    def _set_save_enabled(self, enabled):
        for button in (self.save_events_button, self.save_lc_button, self.save_image_button):
            button.setEnabled(enabled)

    def closeEvent(self, event):
        if self.export_thread is not None:
            QMessageBox.information(self, "Save in progress", "Wait for the current save operation to finish.")
            event.ignore()
            return
        if self.analysis_thread is not None:
            self.cancel_analysis()
            QMessageBox.information(self, "Analysis in progress", "Cancellation requested. Close the window after it finishes.")
            event.ignore()
            return
        try:
            self._save_analysis_settings()
        except (ValueError, TypeError):
            # A partially initialized/empty event list has no settings to persist.
            pass
        for reader in self._owned_readers:
            reader.close()
        self._owned_readers.clear()
        event.accept()


def _ensure_extension(path, selected_filter):
    if Path(path).suffix:
        return path
    if "Text" in selected_filter:
        return path + ".txt"
    if "JPEG" in selected_filter:
        return path + ".jpg"
    if "PNG" in selected_filter:
        return path + ".png"
    return path + ".csv"


def _text_header(result, columns):
    config = result["config"]
    metadata = result.get("metadata", {})
    provenance = result.get("provenance", {})
    detector = ", ".join(str(value) for value in metadata.get("detectors", []) if value) or "not specified"
    hdus = ", ".join(
        f"[{item['index']}] {item['name']}" for item in metadata.get("selected_hdus", [])
    ) or str(config["hdu_indices"])
    energy = _energy_text(result)
    background = result.get("background_fit", {})
    coefficients = background.get("coefficients", [float("nan"), float("nan")])
    covariance = background.get("covariance", [[float("nan"), float("nan")], [float("nan"), float("nan")]])
    return "\n".join([
        f"{APP_NAME} {APP_VERSION} light curve export",
        f"Generated UTC: {provenance.get('generated_at_utc') or datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Runtime: Python {provenance.get('python_version', 'not specified')}; NumPy {provenance.get('numpy_version', 'not specified')}; Astropy {provenance.get('astropy_version', 'not specified')}",
        f"Platform: {provenance.get('platform', 'not specified')}; byte order: {provenance.get('byte_order', 'not specified')}",
        f"Source: {result['path']}",
        f"Sources: {' | '.join(result.get('paths', [result['path']]))}",
        f"Source SHA256: {provenance.get('source_sha256', {})}",
        f"Object: {metadata.get('object') or 'not specified'}",
        f"Observation ID: {metadata.get('obs_id') or 'not specified'}",
        f"Telescope: {metadata.get('telescope') or 'not specified'}",
        f"Instrument: {metadata.get('instrument') or 'not specified'}",
        f"Detector: {detector}",
        f"Selected HDUs: {hdus}",
        f"Time range: {config['time_start']} to {config['time_end']} s",
        f"Full-bin range: {result.get('effective_time_start')} to {result.get('effective_time_end')} s",
        f"Time reference: {'relative to TRIGTIME' if result.get('relative_time') else 'FITS TIME'}",
        f"Time system: {metadata.get('time_system') or 'not specified'}",
        f"Omitted partial tail: {result.get('excluded_tail', 0.0)} s",
        f"DT: {config['dt']} s",
        f"Energy selection: {energy}",
        f"GTI filter: {'applied' if config.get('use_gti') else 'off'}",
        f"FLAG filter: {config.get('flag_value') if config.get('filter_flag') else 'off'}",
        f"EVT_TYPE filter: {config.get('evt_type_value') if config.get('filter_evt_type') else 'off'}",
        f"Background fit: {'weighted linear' if background.get('performed') else 'off'}",
        f"Background coefficients reused: {'yes' if background.get('reused') else 'no'}",
        f"Background intervals (s): {background.get('windows_s', [])}",
        f"Background coefficients [a, b]: {coefficients}",
        f"Background covariance: {covariance}",
        f"Background fit bins: {background.get('n_fit_bins', 0)}",
        f"Background weighting: {background.get('weighting', 'not specified')}",
        f"Filtered events by source/HDU: {result.get('source_counts', {})}",
        f"Total filtered events: {len(result['events'])}",
        "Uncertainty: Poisson 1-sigma; net-rate error includes propagated background covariance",
        columns,
    ])


def _chart_title(result, mode, font_scale=100):
    config = result["config"]
    metadata = result.get("metadata", {})
    source_name = metadata.get("object") or Path(result["path"]).name
    obs_id = metadata.get("obs_id")
    heading = f"{source_name} - Light curve"
    if obs_id:
        heading += f" (OBS_ID {obs_id})"

    observatory = " / ".join(
        value for value in (metadata.get("telescope"), metadata.get("instrument")) if value
    ) or "not specified"
    detector = _compact_values(metadata.get("detectors", []))
    hdu_values = [f"[{item['index']}] {item['name']}" for item in metadata.get("selected_hdus", [])]
    hdus = _compact_values(hdu_values) if hdu_values else _compact_values(config["hdu_indices"])

    requested_start = _format_number(config["time_start"])
    requested_end = _format_number(config["time_end"])
    time_reference = "relative to TRIGTIME" if result.get("relative_time") else "FITS TIME"
    time_system = metadata.get("time_system")
    if time_system and not result.get("relative_time"):
        time_reference += f" ({time_system})"
    time_text = f"Time: {requested_start} to {requested_end} s, {time_reference}"
    excluded_tail = float(result.get("excluded_tail", 0.0) or 0.0)
    if excluded_tail > 1e-10:
        effective_end = _format_number(result["effective_time_end"])
        time_text += f"; full bins end at {effective_end} s"

    source_count = len(result.get("paths", []))
    source_text = f"{source_count} sources" if source_count > 1 else "1 source"
    identity_line = f"Observatory: {observatory}  |  Detector: {detector}  |  HDU: {hdus}  |  {source_text}"
    selection_line = (
        f"{time_text}  |  DT: {_format_number(config['dt'])} s  |  Energy: {_energy_text(result)}"
    )
    filter_parts = ["GTI applied" if config.get("use_gti") else "GTI off"]
    if config.get("filter_flag"):
        filter_parts.append(f"FLAG={config.get('flag_value')}")
    if config.get("filter_evt_type"):
        filter_parts.append(f"EVT_TYPE={config.get('evt_type_value')}")
    quantity = "Raw counts" if mode == "counts" else "Raw count rate"
    fit = result.get("background_fit", {})
    if fit.get("performed"):
        coefficients = fit.get("coefficients", [np.nan, np.nan])
        background_note = (
            f"background: a={_format_number(coefficients[0])}, b={_format_number(coefficients[1])}, "
            f"N={fit.get('n_fit_bins', 0)} bins"
        )
    else:
        background_note = "background off"
    note_line = (
        f"{'  |  '.join(filter_parts)}  |  N={len(result['events']):,} events  |  "
        f"{quantity}; {background_note}; Poisson 1-sigma errors"
    )
    return (
        "<div align='center'>"
        f"<span style='font-size:{11 * font_scale / 100:.2f}pt; font-weight:600'>{html.escape(heading)}</span><br>"
        f"<span style='font-size:{8.5 * font_scale / 100:.2f}pt'>{html.escape(identity_line)}</span><br>"
        f"<span style='font-size:{8.5 * font_scale / 100:.2f}pt'>{html.escape(selection_line)}</span><br>"
        f"<span style='font-size:{8 * font_scale / 100:.2f}pt; color:#555555'>{html.escape(note_line)}</span>"
        "</div>"
    )


def _energy_text(result):
    config = result["config"]
    if not config.get("apply_energy"):
        return "unfiltered"
    unit = result.get("metadata", {}).get("energy_unit") or "unit not specified"
    return f"{_format_number(config['energy_low'])} to {_format_number(config['energy_high'])} {unit}"


def _format_number(value):
    return f"{float(value):.8g}"


def _compact_values(values, limit=3):
    values = [str(value) for value in values if value]
    if not values:
        return "not specified"
    if len(values) <= limit:
        return ", ".join(values)
    return ", ".join(values[:limit]) + f", etc. ({len(values)} total)"


def _safe_name(value):
    value = str(value or "unknown")
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value).strip("_") or "unknown"


def _default_output_path(result, kind, suffix):
    paths = result.get("paths") or [result.get("path", "fitpeek")]
    base = Path(paths[0])
    metadata = result.get("metadata", {})
    detectors = metadata.get("detectors", [])
    source_text = _short_source_name(base, metadata)
    detector_text = _short_detector_name(detectors, len(paths))
    config = result.get("config", {})
    parts = [source_text, detector_text]
    parts.append(
        f"t{_short_number(config.get('time_start', 0))}_{_short_number(config.get('time_end', 0))}"
    )
    if config.get("apply_energy"):
        parts.append(
            f"e{_short_number(config.get('energy_low', 0))}_{_short_number(config.get('energy_high', 0))}"
        )
    parts.append({"event_sequence": "evt", "lightcurve": "lc"}.get(kind, "img" if suffix.lower() in (".png", ".jpg", ".jpeg") else _safe_name(kind)))
    candidate = base.with_name("_".join(parts) + suffix)
    if candidate.exists():
        signature = {
            "paths": [str(path) for path in paths],
            "config": config,
            "background": result.get("background_fit", {}),
        }
        digest = hashlib.sha1(json.dumps(signature, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:6]
        candidate = candidate.with_name(f"{candidate.stem}_h{digest}{candidate.suffix}")
    return str(candidate)


def _short_source_name(base, metadata):
    values = [base.name, metadata.get("object"), metadata.get("obs_id")]
    for value in values:
        match = re.search(r"(?:^|[^a-z0-9])(bn\d+[a-z]?)", str(value or ""), re.IGNORECASE)
        if match:
            return match.group(1).lower()
    for value in (metadata.get("object"), metadata.get("obs_id"), base.stem):
        token = _safe_name(value).lower()
        if token and token != "unknown":
            return token[:18]
    return "fitpeek"


def _short_detector_name(detectors, source_count):
    unique = list(dict.fromkeys(str(value) for value in detectors if value))
    if len(unique) > 1:
        return f"d{len(unique)}"
    if len(unique) == 1:
        value = unique[0]
        match = re.search(r"(?i)\b(NAI|BGO)[_ -]*0*(\d+)\b", value)
        if match:
            return f"{match.group(1)[0].lower()}{int(match.group(2))}"
        return _safe_name(value).lower()[:8]
    return f"s{source_count}" if source_count > 1 else "d1"


def _short_number(value):
    number = float(value)
    if abs(number) < 5e-12:
        number = 0.0
    return f"{number:.4g}".replace("+", "")


def _filename_values(values, fallback, limit=3):
    values = [_safe_name(value) for value in values if value not in (None, "")]
    if not values:
        return fallback
    unique = list(dict.fromkeys(values))
    if len(unique) <= limit:
        return "_".join(unique)
    return "_".join(unique[:limit]) + f"_etc{len(unique)}"

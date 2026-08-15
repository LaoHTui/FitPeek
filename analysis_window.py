from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QObject, QPointF, QRectF, QSize, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
    QGraphicsItem, QHBoxLayout, QLabel, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QProgressBar, QSizePolicy, QSpinBox,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from light_curve import AnalysisCancelled, compute_light_curve, downsample_envelope

EVENT_ROWS_ROLE = int(Qt.UserRole) + 1


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
            comments = "" if delimiter == "," else "# "
            if self.kind == "events":
                data = self.result["events"].reshape(-1, 1)
                header = "EVENT_TIME_S" if delimiter == "," else _text_header(self.result, "Column 0: Event time (s)")
            else:
                data = np.column_stack([
                    self.result["time_centers"], self.result["counts"], self.result["count_error"],
                    self.result["rate"], self.result["rate_error"],
                ])
                columns = "TIME_CENTER_S,COUNTS,COUNT_ERROR,RATE_PER_S,RATE_ERROR_PER_S"
                header = columns if delimiter == "," else _text_header(
                    self.result,
                    "Columns: TIME_CENTER_S COUNTS COUNT_ERROR RATE_PER_S RATE_ERROR_PER_S",
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


class ResponsiveChartView(QChartView):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class LightCurveWindow(QDialog):
    def __init__(self, reader, parent=None):
        super().__init__(parent, Qt.Window)
        self.reader = reader
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
        root.addLayout(source_row)

        events_layout = QVBoxLayout()
        events_toolbar = QHBoxLayout()
        select_all_button = QPushButton("All")
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
        self.event_list.setGridSize(QSize(148, 30))
        self.event_list.setSpacing(2)
        self.event_list.setMinimumHeight(96)
        self.event_list.setMaximumHeight(150)
        self.event_list.itemChanged.connect(self._update_event_summary)
        events_layout.addWidget(self.event_list)
        self.events_section = CollapsibleSection("Event HDUs", events_layout)
        root.addWidget(self.events_section)

        grid = QGridLayout()
        self.time_start = self._number_box(-1e15, 1e15, 6)
        self.time_end = self._number_box(-1e15, 1e15, 6)
        self.dt = self._number_box(1e-6, 1e9, 6)
        self.dt.setValue(0.01)
        grid.addWidget(QLabel("Time start (s)"), 0, 0)
        grid.addWidget(self.time_start, 0, 1)
        grid.addWidget(QLabel("Time end (s)"), 0, 2)
        grid.addWidget(self.time_end, 0, 3)
        grid.addWidget(QLabel("DT (s)"), 1, 0)
        grid.addWidget(self.dt, 1, 1)

        self.relative_time = QCheckBox("Relative to TRIGTIME")
        self.relative_time.toggled.connect(self._time_reference_changed)
        self.use_gti = QCheckBox("Apply GTI")
        self.filter_flag = QCheckBox("Filter FLAG")
        self.flag_value = QSpinBox()
        self.flag_value.setRange(-2147483648, 2147483647)
        self.flag_value.setValue(0)
        self.filter_flag.toggled.connect(self.flag_value.setEnabled)
        self.filter_evt_type = QCheckBox("Filter EVT_TYPE")
        self.evt_type_value = QSpinBox()
        self.evt_type_value.setRange(-2147483648, 2147483647)
        self.evt_type_value.setValue(1)
        self.filter_evt_type.toggled.connect(self.evt_type_value.setEnabled)
        grid.addWidget(self.relative_time, 2, 0, 1, 2)
        grid.addWidget(self.use_gti, 2, 2, 1, 2)

        self.apply_energy = QCheckBox("Energy filter")
        self.apply_energy.toggled.connect(self._energy_filter_toggled)
        self.energy_low = self._number_box(-1e12, 1e12, 4)
        self.energy_high = self._number_box(-1e12, 1e12, 4)
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
        grid.addLayout(controls, 5, 0, 1, 4)
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
        candidates = []
        available_columns = set()
        for info in self.reader.infos:
            names = {field.name.upper() for field in info.fields}
            if info.is_table and "TIME" in names and not info.display_name.upper().startswith("GTI"):
                candidates.append(info)
                available_columns.update(names)
                item = QListWidgetItem(f"[{info.index}] {info.display_name}")
                item.setData(Qt.UserRole, info.index)
                item.setData(EVENT_ROWS_ROLE, info.rows or 0)
                item.setToolTip(f"{info.display_name} | {info.rows or 0:,} rows")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                self.event_list.addItem(item)
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
        time_start, time_end = (0.0, 100.0)
        if gti_index is not None:
            fields = [field.name.upper() for field in self.reader.table_schema(gti_index)]
            if "START" in fields and "STOP" in fields:
                rows = self.reader.read_table_rows(gti_index, 0, min(10000, self.reader.infos[gti_index].rows or 0))
                if rows:
                    starts = [float(row[fields.index("START")]) for row in rows]
                    stops = [float(row[fields.index("STOP")]) for row in rows]
                    offset = float(trigtime) if trigtime is not None else 0.0
                    time_start, time_end = min(starts) - offset, max(stops) - offset
        self.time_start.setValue(time_start)
        self.time_end.setValue(time_end)

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

    @Slot(bool)
    def _time_reference_changed(self, relative):
        if self.trigtime is None:
            return
        offset = -self.trigtime if relative else self.trigtime
        self.time_start.setValue(self.time_start.value() + offset)
        self.time_end.setValue(self.time_end.value() + offset)

    @Slot(bool)
    def _energy_filter_toggled(self, enabled):
        available = self.apply_energy.isEnabled()
        self.energy_low.setEnabled(available and enabled)
        self.energy_high.setEnabled(available and enabled)

    def _config(self):
        indices = [
            int(self.event_list.item(row).data(Qt.UserRole))
            for row in range(self.event_list.count())
            if self.event_list.item(row).checkState() == Qt.Checked
        ]
        if not indices:
            raise ValueError("Select at least one event HDU")
        if self.time_start.value() >= self.time_end.value():
            raise ValueError("Time start must be smaller than time end")
        if self.apply_energy.isChecked() and self.energy_low.value() > self.energy_high.value():
            raise ValueError("E low must not exceed E high")
        return {
            "hdu_indices": indices,
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
        }

    def run_analysis(self):
        if self.analysis_thread is not None:
            return
        try:
            config = self._config()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
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
        self.status.setText(f"{len(result['events']):,} events | {len(result['counts']):,} full bins{tail_text}")
        self._set_save_enabled(True)
        self.refresh_chart()

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
        chart.setMargins(QMargins(10, 2, 10, 8))
        chart.addSeries(series)
        chart.legend().hide()
        axis_x = QValueAxis()
        axis_x.setTitleText("Time relative to TRIGTIME (s)" if self.result["relative_time"] else "FITS TIME (s)")
        axis_y = QValueAxis()
        axis_y.setTitleText("Counts / bin" if mode == "counts" else "Count rate / s")
        for axis in (axis_x, axis_y):
            axis.setLabelsBrush(QBrush(QColor("black")))
            axis.setTitleBrush(QBrush(QColor("black")))
            axis.setGridLinePen(QPen(QColor("#d9d9d9"), 1.0))
            axis.setLinePen(QPen(QColor("#444444"), 1.0))
            axis.setLabelsFont(QFont("Segoe UI", 9))
            axis_title_font = QFont("Segoe UI", 9)
            axis_title_font.setWeight(QFont.DemiBold)
            axis.setTitleFont(axis_title_font)
        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)
        config = self.result["config"]
        axis_start = float(self.result.get("effective_time_start", config["time_start"]))
        axis_end = float(self.result.get("effective_time_end", config["time_end"]))
        duration = axis_end - axis_start
        padding = max(float(config["dt"]) * 0.5, duration * 0.02)
        axis_x.setRange(axis_start - padding, axis_end + padding)
        axis_x.setTickCount(8)
        if len(full_y):
            y_min = float(np.min(full_y - full_error))
            y_max = float(np.max(full_y + full_error))
            if y_min == y_max:
                y_min -= 1.0
                y_max += 1.0
            axis_y.setRange(y_min, y_max)
            axis_y.applyNiceNumbers()
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

    @Slot()
    def _schedule_chart_refresh(self):
        if self.result:
            self.chart_resize_timer.start()

    def save_events(self):
        if not self.result:
            return
        default = str(Path(self.result["path"]).with_suffix("")) + "_events.csv"
        path, selected = QFileDialog.getSaveFileName(self, "Save filtered events", default, "CSV (*.csv);;Text (*.txt)")
        if path:
            self._start_export("events", _ensure_extension(path, selected))

    def save_light_curve(self):
        if not self.result:
            return
        default = str(Path(self.result["path"]).with_suffix("")) + "_lightcurve.csv"
        path, selected = QFileDialog.getSaveFileName(self, "Save light curve data", default, "CSV (*.csv);;Text (*.txt)")
        if path:
            self._start_export("lightcurve", _ensure_extension(path, selected))

    def save_image(self):
        if not self.result:
            return
        default = str(Path(self.result["path"]).with_suffix("")) + "_lightcurve.png"
        path, selected = QFileDialog.getSaveFileName(self, "Save light curve image", default, "PNG (*.png);;JPEG (*.jpg *.jpeg)")
        if path:
            path = _ensure_extension(path, selected)
            if not self.chart_view.grab().save(path):
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
    return "\n".join([
        "FitPeek light curve export",
        f"Source: {result['path']}",
        f"Selected HDUs: {config['hdu_indices']}",
        f"Time range: {config['time_start']} to {config['time_end']} s",
        f"Full-bin range: {result.get('effective_time_start')} to {result.get('effective_time_end')} s",
        f"Omitted partial tail: {result.get('excluded_tail', 0.0)} s",
        f"DT: {config['dt']} s",
        f"Energy range: {config['energy_low']} to {config['energy_high']}",
        f"FLAG filter: {config.get('flag_value') if config.get('filter_flag') else 'off'}",
        f"EVT_TYPE filter: {config.get('evt_type_value') if config.get('filter_evt_type') else 'off'}",
        f"Total filtered events: {len(result['events'])}",
        columns,
    ])

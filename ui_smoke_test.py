import csv
import os
import tempfile
from pathlib import Path

import numpy as np

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from astropy.io import fits
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDropEvent, QStandardItemModel
from PySide6.QtWidgets import QApplication, QToolBar

from app import APP_AUTHOR, APP_NAME, APP_REPOSITORY, APP_VERSION, MainWindow, ResponsiveTableView
from analysis_window import (
    ExportWorker, LightCurveWindow, _chart_title, _default_output_path,
    light_curve_settings_key,
)
from light_curve import compute_light_curve
from smoke_test import create_sample


def main():
    with tempfile.TemporaryDirectory() as test_dir, tempfile.TemporaryDirectory() as settings_dir:
        first = create_sample(os.path.join(test_dir, "test_sample.fits"))
        second = create_sample(os.path.join(test_dir, "test_sample_2.fits"))
        related = create_sample(os.path.join(test_dir, "test_sample_n1.fits"))
        fits.setval(second, "TELESCOP", value="FITPEEK-SECOND", ext=0)
        settings = QSettings(os.path.join(settings_dir, "fitpeek-test.ini"), QSettings.IniFormat)
        app = QApplication.instance() or QApplication([])
        window = MainWindow([], settings=settings)
        window.resize(1400, 800)
        window.show()
        assert not window.windowIcon().isNull()
        assert not window.findChildren(QToolBar)
        file_actions = {action.text() for action in window.file_menu.actions()}
        assert {"Open", "Remove selected file from session"} <= file_actions
        assert [action.text() for action in window.menuBar().actions()] == ["File", "View", "About"]
        assert {action.text() for action in window.about_menu.actions()} == {"About FitPeek..."}
        about = window.create_about_dialog()
        assert about.windowTitle() == f"About {APP_NAME}"
        assert about.version_label.text() == f"Version {APP_VERSION}"
        assert about.author_label.text() == APP_AUTHOR
        assert APP_REPOSITORY in about.repository_label.text()
        about.close()
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(os.fspath(first))])
        drop_event = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime_data, Qt.LeftButton, Qt.NoModifier)
        window.dropEvent(drop_event)
        assert drop_event.isAccepted()
        window.open_file(second, select=False)
        assert window.tree.topLevelItemCount() == 2
        assert window.recent_files()[0] == window._key(second)
        assert window.recent_files()[1] == window._key(first)

        first_root = window.tree.topLevelItem(0)
        window.tree.setCurrentItem(first_root)
        app.processEvents()
        assert window.summary_table.rowCount() >= 10
        assert window.gti_table.rowCount() == 1
        assert window.ebounds_table.rowCount() == 4

        context_menu = window._create_session_context_menu(first_root.child(3))
        context_actions = {action.text(): action for action in context_menu.actions() if action.text()}
        assert {
            "New Light Curve Window...", "Compare FITS Headers...", "Copy File Path", "Remove from Session",
        } <= context_actions.keys()
        assert context_actions["New Light Curve Window..."].isEnabled()
        context_actions["Copy File Path"].trigger()
        assert QApplication.clipboard().text() == window._key(first)
        context_menu.deleteLater()

        assert first_root.child(3).data(0, Qt.UserRole + 1) == 3, first_root.child(3).data(0, Qt.UserRole + 1)
        selected_compare = window.open_header_compare(first_root.child(3))
        assert selected_compare is not None
        assert selected_compare.left_combo.currentData() == (window._key(first), 3), selected_compare.left_combo.currentData()
        selected_compare.close()

        window.tree.setCurrentItem(first_root)
        app.processEvents()
        compare = window.open_header_compare()
        assert compare is not None
        assert compare.left_combo.currentData()[0] != compare.right_combo.currentData()[0]
        right_index = next(
            index for index in range(compare.right_combo.count())
            if compare.right_combo.itemData(index) == (window._key(second), 0)
        )
        compare.right_combo.setCurrentIndex(right_index)
        compare.refresh()
        assert any(
            compare.table.item(row, 0).text() == "TELESCOP"
            and compare.table.item(row, 3).text() == "Changed"
            for row in range(compare.table.rowCount())
        )
        compare.mode.setCurrentIndex(compare.mode.findData("different"))
        assert any(not compare.table.isRowHidden(row) for row in range(compare.table.rowCount()))
        compare.close()

        curve_one = window.open_light_curve()
        curve_two = window.open_light_curve()
        assert curve_one is not curve_two
        assert len(window.analysis_windows) == 2
        assert curve_one.windowFlags() & Qt.Window
        assert curve_one.windowTitle() != curve_two.windowTitle()
        curve_one.close()
        curve_two.close()
        app.processEvents()

        analysis = LightCurveWindow(window.reader, window)
        analysis.show()
        app.processEvents()
        expanded_plot_height = analysis.preview_stack.height()
        analysis.events_section.set_expanded(False)
        analysis.options_section.set_expanded(False)
        app.processEvents()
        assert not analysis.events_section.content.isVisible()
        assert not analysis.options_section.content.isVisible()
        assert analysis.preview_stack.height() > expanded_plot_height + 150
        assert analysis.preview_stack.width() > analysis.width() * 0.9
        assert analysis.event_list.count() == 1
        assert analysis.event_summary.text().startswith("1/1 HDUs")
        analysis._set_all_events(Qt.Unchecked)
        assert not analysis.run_button.isEnabled()
        analysis._set_all_events(Qt.Checked)
        assert analysis.run_button.isEnabled()
        assert analysis.background_enabled
        assert analysis.background_default_intervals
        assert len(analysis.background_windows) == 2
        assert analysis.time_start.value() == -30.0
        assert analysis.time_end.value() == 60.0
        analysis_result = compute_light_curve(window.reader.path, analysis._config())
        analysis._on_result(analysis_result)
        fitted_windows = [tuple(value) for value in analysis.background_windows]
        fitted_coefficients = list(analysis_result["background_fit"]["coefficients"])
        original_start = analysis.time_start.value()
        original_end = analysis.time_end.value()
        analysis.time_start.setValue(original_start + 0.05)
        analysis.time_end.setValue(original_end - 0.05)
        reused_config = analysis._config()
        assert "background_cached_fit" in reused_config
        reused_result = compute_light_curve(window.reader.path, reused_config)
        assert reused_result["background_fit"]["reused"]
        assert reused_result["background_fit"]["coefficients"] == fitted_coefficients
        assert [tuple(value) for value in reused_result["background_fit"]["windows_s"]] == fitted_windows
        analysis.time_start.setValue(original_start)
        analysis.time_end.setValue(original_end)
        assert analysis.preview_stack.currentIndex() == 1
        assert analysis.chart_view.chart().series()
        chart = analysis.chart_view.chart()
        series_points = chart.series()[0].points()
        assert "Light curve" in chart.title()
        assert "Detector: FP-DETECTOR" in chart.title()
        assert "DT: 0.01 s" in chart.title()
        assert "Energy: 2 to 10 keV" in chart.title()
        assert "Poisson 1-sigma" in chart.title()
        assert 0 < len(series_points) <= len(analysis_result["counts"]) * 2
        assert any(
            series_points[index].x() == series_points[index + 1].x()
            and series_points[index].y() != series_points[index + 1].y()
            for index in range(len(series_points) - 1)
        )
        assert chart.plotAreaBackgroundBrush().color().name() == "#ffffff"
        assert chart.series()[0].pen().color().name() == "#000000"
        assert len(chart.series()) == 2
        assert chart.series()[1].name() == "Linear background"
        assert chart.series()[1].pen().color().name() == "#00796b"
        assert chart.legend().isVisible()
        assert analysis.background_window_item is not None
        analysis_config = analysis_result["config"]
        assert all(
            analysis_config["time_start"] <= start < end <= analysis_config["time_end"]
            for start, end in analysis.background_window_item.windows
        )
        assert "background:" in chart.title()
        x_axis = chart.axes(Qt.Horizontal)[0]
        assert x_axis.min() < analysis_config["time_start"]
        assert x_axis.min() > analysis_config["time_start"] - (analysis_config["time_end"] - analysis_config["time_start"]) * 0.05
        assert x_axis.max() > analysis_config["time_end"]
        assert analysis.error_bar_item is not None
        assert analysis.trigger_item is not None
        assert analysis.save_events_button.isEnabled()
        events_csv = os.path.join(test_dir, "events.csv")
        light_curve_txt = os.path.join(test_dir, "lightcurve.txt")
        image_png = os.path.join(test_dir, "lightcurve.png")
        ExportWorker("events", events_csv, analysis_result).run()
        ExportWorker("lightcurve", light_curve_txt, analysis_result).run()
        assert os.path.getsize(events_csv) > 0
        assert os.path.getsize(light_curve_txt) > 0
        exported_text = open(light_curve_txt, encoding="utf-8").read()
        assert "Detector: FP-DETECTOR" in exported_text
        assert "Background fit: weighted linear" in exported_text
        assert "BACKGROUND_RATE_PER_S" in exported_text
        assert np.loadtxt(light_curve_txt).shape[1] == 9
        assert analysis.chart_view.grab().save(image_png)
        assert os.path.getsize(image_png) > 0

        combined_config = dict(analysis._config())
        combined_config["paths"] = [os.fspath(first), os.fspath(related)]
        combined_config["sources"] = [
            {"path": os.fspath(first), "hdu_indices": [3]},
            {"path": os.fspath(related), "hdu_indices": [3]},
        ]
        combined_result = compute_light_curve(first, combined_config)
        assert int(combined_result["counts"].sum()) == 2 * int(analysis_result["counts"].sum())
        assert len(combined_result["paths"]) == 2
        assert len(combined_result["provenance"]["source_sha256"]) == 2
        combined_default = _default_output_path(combined_result, "lightcurve", ".txt")
        assert combined_default.endswith("_lc.txt")
        assert len(os.path.basename(combined_default)) < 80
        assert "combined" not in os.path.basename(combined_default)
        combined_txt = os.path.join(test_dir, "combined.txt")
        ExportWorker("lightcurve", combined_txt, combined_result).run()
        combined_header = open(combined_txt, encoding="utf-8").read()
        assert f"FitPeek {APP_VERSION}" in combined_header
        assert "Generated UTC:" in combined_header
        assert "Source SHA256:" in combined_header
        Path(combined_default).touch()
        collision_default = _default_output_path(combined_result, "lightcurve", ".txt")
        assert "_h" in Path(collision_default).stem

        background_off = compute_light_curve(first, dict(analysis._config(), background_fit=False))
        assert np.isnan(background_off["background_rate"]).all()
        background_off_txt = os.path.join(test_dir, "background_off.txt")
        ExportWorker("lightcurve", background_off_txt, background_off).run()
        assert np.isnan(np.loadtxt(background_off_txt)[:, 5:]).all()

        dense_count = 12900
        dense_x = np.linspace(-10.0, 20.0, dense_count)
        dense_y = 240.0 + 20.0 * np.sin(dense_x)
        dense_result = dict(analysis_result)
        dense_result.update({
            "time_centers": dense_x,
            "counts": dense_y,
            "count_error": np.sqrt(dense_y),
            "rate": dense_y,
            "rate_error": np.sqrt(dense_y),
            "effective_time_start": -10.0,
            "effective_time_end": 20.0,
            "relative_time": True,
            "config": dict(analysis_config, time_start=-10.0, time_end=20.0, dt=30.0 / dense_count),
        })
        analysis.result = dense_result
        analysis.refresh_chart()
        assert analysis.preview_point_count < dense_count
        assert analysis.preview_error_count <= 500
        assert analysis.trigger_item is not None
        assert "full bins end" not in _chart_title(dense_result, "counts")
        analysis.time_start.setValue(-12.0)
        analysis.time_end.setValue(34.0)
        analysis.dt.setValue(0.02)
        analysis.y_mode.setCurrentIndex(analysis.y_mode.findData("rate"))
        analysis.close()
        app.processEvents()
        restored_analysis = LightCurveWindow(window.reader, window)
        assert restored_analysis.time_start.value() == -12.0
        assert restored_analysis.time_end.value() == 34.0
        assert restored_analysis.dt.value() == 0.02
        assert restored_analysis.y_mode.currentData() == "rate"
        restored_analysis.close()

        events_item = first_root.child(3)
        window.tree.setCurrentItem(events_item)
        app.processEvents()
        assert window.fields_table.columnCount() == 4
        window.range_start.setValue(151)
        window.range_end.setValue(175)
        window.go_to_range()
        model = window.data_view.model()
        source_model = model.sourceModel()
        assert source_model.start == 150
        assert model.rowCount() == 25
        window.data_filter.setText("174")
        app.processEvents()
        assert model.rowCount() == 1
        window.data_filter.clear()
        window.data_view.sortByColumn(1, Qt.DescendingOrder)
        app.processEvents()
        assert model.data(model.index(0, 1), Qt.DisplayRole) == "174"
        window.data_view.selectRow(0)
        selected_csv = os.path.join(test_dir, "selected.csv")
        assert window.export_selected_rows(selected_csv) == selected_csv
        with open(selected_csv, newline="", encoding="utf-8-sig") as exported:
            exported_rows = list(csv.reader(exported))
        assert exported_rows[0] == ["FITS_ROW", "TIME", "PI", "FLAG", "EVT_TYPE"]
        assert exported_rows[1][0] == "175"
        assert exported_rows[1][2] == "174"
        window.data_view.fit_columns()
        column_width = sum(window.data_view.columnWidth(column) for column in range(model.columnCount()))
        assert column_width >= window.data_view.viewport().width() - 4

        narrow_view = ResponsiveTableView()
        wide_model = QStandardItemModel(2, 20)
        wide_model.setHorizontalHeaderLabels([f"COLUMN_{number}" for number in range(20)])
        narrow_view.setModel(wide_model)
        narrow_view.resize(320, 180)
        narrow_view.show()
        app.processEvents()
        narrow_view.fit_columns()
        app.processEvents()
        assert narrow_view.horizontalScrollBar().maximum() > 0
        narrow_view.close()

        window.save_session()
        for reader in window.readers.values():
            reader.close()
        restored = MainWindow([], settings=settings)
        assert restored.tree.topLevelItemCount() == 2
        assert restored.tree.topLevelItem(1).text(0).endswith("[saved]")
        restored.apply_theme("dark")
        assert restored.settings.value("theme") == "dark"
        restored.apply_font_scale(125)
        assert restored.settings.value("fontScale") == 125
        assert restored.font_actions[125].isChecked()
        assert QApplication.font().pointSizeF() > restored._base_font_point_size
        restored.tree.setCurrentItem(restored.tree.topLevelItem(1))
        removed_path = restored.tree.topLevelItem(1).data(0, Qt.UserRole)
        removed_settings_key = light_curve_settings_key(removed_path)
        restored.settings.setValue(removed_settings_key, '{"time_start":1}')
        remove_menu = restored._create_session_context_menu(restored.tree.topLevelItem(1))
        next(action for action in remove_menu.actions() if action.text() == "Remove from Session").trigger()
        app.processEvents()
        assert restored.tree.topLevelItemCount() == 1
        assert restored.settings.value(removed_settings_key) is None
        for reader in restored.readers.values():
            reader.close()
    print("ui smoke test passed")


if __name__ == "__main__":
    main()

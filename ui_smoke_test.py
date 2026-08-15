import csv
import os
import tempfile

import numpy as np

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from astropy.io import fits
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDropEvent, QStandardItemModel
from PySide6.QtWidgets import QApplication, QToolBar

from app import MainWindow, ResponsiveTableView
from analysis_window import ExportWorker, LightCurveWindow
from light_curve import compute_light_curve
from smoke_test import create_sample


def main():
    with tempfile.TemporaryDirectory() as test_dir, tempfile.TemporaryDirectory() as settings_dir:
        first = create_sample(os.path.join(test_dir, "test_sample.fits"))
        second = create_sample(os.path.join(test_dir, "test_sample_2.fits"))
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

        compare = window.open_header_compare()
        assert compare is not None
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

        analysis = LightCurveWindow(window.reader)
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
        analysis_result = compute_light_curve(window.reader.path, analysis._config())
        analysis._on_result(analysis_result)
        assert analysis.preview_stack.currentIndex() == 1
        assert analysis.chart_view.chart().series()
        chart = analysis.chart_view.chart()
        series_points = chart.series()[0].points()
        assert chart.title() == ""
        assert len(series_points) == len(analysis_result["counts"]) * 2
        assert any(
            series_points[index].x() == series_points[index + 1].x()
            and series_points[index].y() != series_points[index + 1].y()
            for index in range(len(series_points) - 1)
        )
        assert chart.plotAreaBackgroundBrush().color().name() == "#ffffff"
        assert chart.series()[0].pen().color().name() == "#000000"
        x_axis = chart.axes(Qt.Horizontal)[0]
        analysis_config = analysis_result["config"]
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
        assert analysis.chart_view.grab().save(image_png)
        assert os.path.getsize(image_png) > 0

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
        analysis.close()

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
        restored.tree.setCurrentItem(restored.tree.topLevelItem(1))
        remove_menu = restored._create_session_context_menu(restored.tree.topLevelItem(1))
        next(action for action in remove_menu.actions() if action.text() == "Remove from Session").trigger()
        app.processEvents()
        assert restored.tree.topLevelItemCount() == 1
        for reader in restored.readers.values():
            reader.close()
    print("ui smoke test passed")


if __name__ == "__main__":
    main()

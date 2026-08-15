import csv
import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal, QTimer
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStatusBar, QStyle, QTabWidget, QTableView,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from table_model import FitsTableModel, TableFilterProxyModel

APP_NAME = "FitPeek"
ORG_NAME = "FitPeek"
MAX_PREVIEW_ROWS = 5000
MAX_SCIENCE_ROWS = 10000
MAX_RECENT_FILES = 10
SUPPORTED_EXTENSIONS = {".fits", ".fit", ".fits.gz", ".evt", ".pha", ".rsp", ".rsp2", ".rm"}
ROLE_PATH = int(Qt.UserRole)
ROLE_HDU = ROLE_PATH + 1


class SessionTree(QTreeWidget):
    delete_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ResponsiveTableView(QTableView):
    def setModel(self, model):
        super().setModel(model)
        self.schedule_column_fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_column_fit()

    def schedule_column_fit(self):
        QTimer.singleShot(0, self.fit_columns)

    def fit_columns(self):
        model = self.model()
        if model is None or model.columnCount() == 0:
            return
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setResizeContentsPrecision(100)
        self.resizeColumnsToContents()
        count = model.columnCount()
        widths = [max(88, self.columnWidth(column)) for column in range(count)]
        available = max(0, self.viewport().width() - 2)
        natural_total = sum(widths)
        if natural_total < available:
            extra, remainder = divmod(available - natural_total, count)
            widths = [width + extra + (1 if column < remainder else 0) for column, width in enumerate(widths)]
        for column, width in enumerate(widths):
            self.setColumnWidth(column, width)


def resource_path(relative_path):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative_path


class MainWindow(QMainWindow):
    def __init__(self, initial_paths=None, settings=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(resource_path("assets/fitpeek.png"))))
        self.resize(1360, 820)
        self.settings = settings or QSettings(ORG_NAME, APP_NAME)
        self.readers = {}
        self.roots = {}
        self.reader = None
        self.current_hdu = None
        self.science_loaded_for = None
        self.analysis_windows = []
        self.header_windows = []
        self._analysis_serial = 0
        self.setAcceptDrops(True)
        self._build_ui()
        self.apply_theme(self.settings.value("theme", "system"), persist=False)
        self.restore_session()
        for path in initial_paths or []:
            self.open_file(path)

    def _build_ui(self):
        self.file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.choose_files)
        self.file_menu.addAction(open_action)
        self.recent_menu = self.file_menu.addMenu("Open Recent")
        self.file_menu.addSeparator()
        remove_action = QAction("Remove selected file from session", self)
        remove_action.triggered.connect(self.close_selected_file)
        self.file_menu.addAction(remove_action)
        self._refresh_recent_menu()

        self.view_menu = self.menuBar().addMenu("View")
        light_curve_action = QAction("New Light Curve Window...", self)
        light_curve_action.setShortcut("Ctrl+L")
        light_curve_action.triggered.connect(self.open_light_curve)
        self.view_menu.addAction(light_curve_action)
        compare_headers_action = QAction("Compare FITS Headers...", self)
        compare_headers_action.triggered.connect(self.open_header_compare)
        self.view_menu.addAction(compare_headers_action)
        self.view_menu.addSeparator()
        self.theme_menu = self.view_menu.addMenu("Theme")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for label, mode in (("Follow system", "system"), ("Light", "light"), ("Dark", "dark")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, value=mode: self.apply_theme(value))
            self.theme_group.addAction(action)
            self.theme_menu.addAction(action)
            self.theme_actions[mode] = action

        self.tree = SessionTree()
        self.tree.setHeaderLabels(["FITS session"])
        self.tree.setMinimumWidth(360)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.itemSelectionChanged.connect(self.on_tree_selected)
        self.tree.itemExpanded.connect(self.on_tree_expanded)
        self.tree.delete_requested.connect(self.close_selected_file)
        self.tree.customContextMenuRequested.connect(self.show_session_context_menu)

        self.tabs = QTabWidget()
        self._build_summary_tab()
        self._build_header_tab()
        self._build_fields_tab()
        self._build_data_tab()
        self._build_science_tab()
        self.tabs.setTabVisible(self.science_tab_index, False)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.setCentralWidget(central)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.path_label = QLabel("No file selected")
        self.path_label.setStyleSheet("color:#64748b; padding-right:8px")
        self.status.addPermanentWidget(self.path_label, 1)

    def _build_summary_tab(self):
        self.summary_table = QTableWidget(0, 2)
        self.summary_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_tab_index = self.tabs.addTab(self.summary_table, "Summary")

    def _build_header_tab(self):
        self.header_table = QTableWidget(0, 4)
        self.header_table.setHorizontalHeaderLabels(["Keyword", "Value", "Type", "Comment"])
        self.header_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.header_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.header_table.horizontalHeader().setStretchLastSection(True)
        self.header_table.setAlternatingRowColors(True)
        self.header_raw = QPlainTextEdit()
        self.header_raw.setReadOnly(True)
        self.header_raw.setFont(QFont("Consolas", 10))
        page = QWidget()
        layout = QVBoxLayout(page)
        self.header_search = QLineEdit()
        self.header_search.setPlaceholderText("Search header keywords...")
        self.header_search.textChanged.connect(self.filter_header)
        layout.addWidget(self.header_search)
        layout.addWidget(self.header_table, 2)
        layout.addWidget(self.header_raw, 1)
        self.header_tab_index = self.tabs.addTab(page, "Header")

    def _build_fields_tab(self):
        self.fields_table = QTableWidget(0, 4)
        self.fields_table.setHorizontalHeaderLabels(["Name", "TFORM", "Unit", "Dimensions"])
        self.fields_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fields_table.setAlternatingRowColors(True)
        self.fields_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.fields_tab_index = self.tabs.addTab(self.fields_table, "Fields")

    def _build_data_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.window_combo = QComboBox()
        self.window_combo.addItem("First 100 rows", ("head", 100))
        self.window_combo.addItem("Last 100 rows", ("tail", 100))
        self.window_combo.currentIndexChanged.connect(self.change_window)
        self.range_start = QSpinBox()
        self.range_start.setPrefix("From ")
        self.range_start.setMinimum(1)
        self.range_end = QSpinBox()
        self.range_end.setPrefix("To ")
        self.range_end.setMinimum(1)
        self.range_button = QPushButton("Go")
        self.range_button.clicked.connect(self.go_to_range)
        self.data_info = QLabel()
        controls.addWidget(self.window_combo)
        controls.addSpacing(12)
        controls.addWidget(self.range_start)
        controls.addWidget(self.range_end)
        controls.addWidget(self.range_button)
        controls.addWidget(self.data_info)
        controls.addStretch()
        layout.addLayout(controls)
        data_tools = QHBoxLayout()
        data_tools.addWidget(QLabel("Filter"))
        self.data_filter = QLineEdit()
        self.data_filter.setPlaceholderText("Search all columns in the current preview")
        self.data_filter.setClearButtonEnabled(True)
        self.data_filter.textChanged.connect(self.filter_data)
        data_tools.addWidget(self.data_filter, 1)
        self.export_rows_button = QPushButton("Export selected rows...")
        self.export_rows_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.export_rows_button.setEnabled(False)
        self.export_rows_button.clicked.connect(self.export_selected_rows)
        data_tools.addWidget(self.export_rows_button)
        layout.addLayout(data_tools)
        self.data_view = ResponsiveTableView()
        self.data_view.setAlternatingRowColors(True)
        self.data_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.data_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.data_view.setSortingEnabled(True)
        self.data_view.horizontalHeader().setDefaultSectionSize(120)
        self.data_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.data_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.data_source_model = None
        self.data_proxy_model = None
        layout.addWidget(self.data_view)
        self.data_tab_index = self.tabs.addTab(page, "Data")

    def _build_science_tab(self):
        self.science_tabs = QTabWidget()
        gti_page = QWidget()
        gti_layout = QVBoxLayout(gti_page)
        self.gti_info = QLabel("No GTI loaded")
        self.gti_table = QTableWidget(0, 4)
        self.gti_table.setHorizontalHeaderLabels(["Interval", "START", "STOP", "Duration"])
        self.gti_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gti_table.setAlternatingRowColors(True)
        self.gti_table.horizontalHeader().setStretchLastSection(True)
        gti_layout.addWidget(self.gti_info)
        gti_layout.addWidget(self.gti_table)
        self.science_tabs.addTab(gti_page, "GTI")

        ebounds_page = QWidget()
        ebounds_layout = QVBoxLayout(ebounds_page)
        self.ebounds_info = QLabel("No EBOUNDS loaded")
        self.ebounds_table = QTableWidget(0, 4)
        self.ebounds_table.setHorizontalHeaderLabels(["CHANNEL", "E_MIN", "E_MAX", "Center energy"])
        self.ebounds_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ebounds_table.setAlternatingRowColors(True)
        self.ebounds_table.horizontalHeader().setStretchLastSection(True)
        ebounds_layout.addWidget(self.ebounds_info)
        ebounds_layout.addWidget(self.ebounds_table)
        self.science_tabs.addTab(ebounds_page, "EBOUNDS")
        self.science_tab_index = self.tabs.addTab(self.science_tabs, "GTI / EBOUNDS")

    def apply_theme(self, mode, persist=True):
        if mode not in ("system", "light", "dark"):
            mode = "system"
        schemes = {
            "system": Qt.ColorScheme.Unknown,
            "light": Qt.ColorScheme.Light,
            "dark": Qt.ColorScheme.Dark,
        }
        QApplication.styleHints().setColorScheme(schemes[mode])
        self.theme_actions[mode].setChecked(True)
        if persist:
            self.settings.setValue("theme", mode)
            self.settings.sync()

    def open_light_curve(self):
        if not self.reader:
            QMessageBox.information(self, "No FITS selected", "Select a FITS file in the session tree first.")
            return None
        from analysis_window import LightCurveWindow

        self._analysis_serial += 1
        window = LightCurveWindow(self.reader, self)
        window.setWindowTitle(f"Light Curve {self._analysis_serial} - {self.reader.path.name}")
        self.analysis_windows.append(window)
        window.destroyed.connect(
            lambda _object=None, current=window: self.analysis_windows.remove(current)
            if current in self.analysis_windows else None
        )
        offset = 28 * ((len(self.analysis_windows) - 1) % 8)
        origin = self.frameGeometry().topLeft()
        window.move(origin.x() + 60 + offset, origin.y() + 60 + offset)
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def open_header_compare(self):
        sources = []
        for key, root in self.roots.items():
            reader = self.ensure_reader(key, root)
            if not reader:
                continue
            for info in reader.infos:
                sources.append((reader.path, info.index, info.display_name, reader.header_cards(info.index)))
        if not sources:
            QMessageBox.information(self, "No FITS available", "Open at least one FITS file first.")
            return None
        from header_compare import HeaderCompareWindow

        current = None
        if self.reader:
            current = (self.reader.path, self.current_hdu if self.current_hdu is not None else 0)
        window = HeaderCompareWindow(sources, current, self)
        self.header_windows.append(window)
        window.destroyed.connect(
            lambda _object=None, current_window=window: self.header_windows.remove(current_window)
            if current_window in self.header_windows else None
        )
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def recent_files(self):
        paths = self.settings.value("recentFiles", [])
        if isinstance(paths, str):
            paths = [paths]
        return [self._key(path) for path in paths or [] if path]

    def _record_recent_file(self, path):
        key = self._key(path)
        paths = [recent for recent in self.recent_files() if recent != key]
        paths.insert(0, key)
        self.settings.setValue("recentFiles", paths[:MAX_RECENT_FILES])
        self.settings.sync()
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        self.recent_menu.clear()
        paths = self.recent_files()
        if not paths:
            empty_action = self.recent_menu.addAction("No recent files")
            empty_action.setEnabled(False)
        else:
            for number, path in enumerate(paths, 1):
                file_path = Path(path)
                action = self.recent_menu.addAction(f"{number}. {file_path.name}  [{file_path.parent}]")
                action.setToolTip(path)
                action.setEnabled(file_path.is_file())
                action.triggered.connect(lambda _checked=False, current=path: self.open_paths([current]))
        self.recent_menu.addSeparator()
        clear_action = self.recent_menu.addAction("Clear Recent Files")
        clear_action.setEnabled(bool(paths))
        clear_action.triggered.connect(self.clear_recent_files)

    def clear_recent_files(self):
        self.settings.remove("recentFiles")
        self.settings.sync()
        self._refresh_recent_menu()

    @staticmethod
    def _is_supported_path(path):
        name = os.fspath(path).lower()
        return any(name.endswith(extension) for extension in SUPPORTED_EXTENSIONS)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(url.isLocalFile() and self._is_supported_path(url.toLocalFile()) for url in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.isLocalFile() and self._is_supported_path(url.toLocalFile())
        ]
        if paths:
            self.open_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open FITS files", "",
            "FITS files (*.fits *.fit *.fits.gz *.evt *.pha *.rsp *.rsp2 *.rm);;All files (*)",
        )
        self.open_paths(paths)

    def open_paths(self, paths):
        opened = []
        for path in paths:
            reader = self.open_file(path, select=False)
            if reader:
                opened.append(reader)
        if opened:
            self.tree.setCurrentItem(self.roots[self._key(opened[-1].path)])
        return opened

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def add_saved_path(self, path):
        key = self._key(path)
        if key in self.roots:
            return self.roots[key]
        root = QTreeWidgetItem([Path(path).name + "  [saved]"])
        root.setData(0, ROLE_PATH, key)
        root.setToolTip(0, key)
        root.addChild(QTreeWidgetItem(["Select or expand to load..."]))
        self.tree.addTopLevelItem(root)
        self.roots[key] = root
        return root

    def open_file(self, path, select=True):
        key = self._key(path)
        root = self.roots.get(key) or self.add_saved_path(key)
        reader = self.ensure_reader(key, root, show_error=True)
        if reader and select:
            root.setExpanded(True)
            self.tree.setCurrentItem(root)
        if reader:
            self._record_recent_file(key)
        self.save_session()
        return reader

    def ensure_reader(self, path, root=None, show_error=False):
        key = self._key(path)
        if key in self.readers:
            return self.readers[key]
        root = root or self.roots.get(key)
        try:
            from fits_reader import FITSReader

            if not os.path.isfile(key):
                raise FileNotFoundError("The saved file no longer exists")
            reader = FITSReader.open(key)
            if reader.open_error:
                raise RuntimeError(reader.open_error)
            self.readers[key] = reader
            self.populate_root(root, reader)
            self.status.showMessage(f"Opened {reader.path.name} | {reader.file_size_text} | {reader.hdu_count} HDU(s)")
            return reader
        except Exception as exc:
            if root:
                root.setText(0, Path(key).name + "  [unavailable]")
                root.setToolTip(0, f"{key}\n{exc}")
            if show_error:
                QMessageBox.critical(self, "Cannot open FITS", f"Read failed:\n{exc}")
            else:
                self.status.showMessage(f"Cannot restore {Path(key).name}: {exc}")
            return None

    def populate_root(self, root, reader):
        root.takeChildren()
        root.setText(0, reader.path.name)
        root.setToolTip(0, str(reader.path))
        for meta in reader.infos:
            label = f"[{meta.index}] {meta.display_name}  {meta.hdu_type}"
            if meta.rows is not None:
                label += f"  {meta.rows:,} rows"
            elif meta.shape:
                label += f"  {meta.shape}"
            if meta.error:
                label += "  [read error]"
            item = QTreeWidgetItem([label])
            item.setData(0, ROLE_PATH, str(reader.path))
            item.setData(0, ROLE_HDU, meta.index)
            root.addChild(item)

    def on_tree_expanded(self, item):
        if item.parent() is None:
            path = item.data(0, ROLE_PATH)
            if path:
                self.ensure_reader(path, item)

    def on_tree_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        root = item if item.parent() is None else item.parent()
        path = root.data(0, ROLE_PATH)
        if not path:
            return
        reader = self.ensure_reader(path, root)
        if not reader:
            return
        self.reader = reader
        self.path_label.setText(str(reader.path))
        self.show_summary(reader)
        self.show_science(reader)
        index = item.data(0, ROLE_HDU)
        if item.parent() is None or index is None:
            self.current_hdu = None
            self.tabs.setCurrentIndex(0)
            self.clear_hdu_views()
            return
        self.current_hdu = int(index)
        try:
            is_table = reader.infos[self.current_hdu].is_table
            self.tabs.setTabEnabled(self.fields_tab_index, is_table)
            self.tabs.setTabEnabled(self.data_tab_index, is_table)
            self.show_header(self.current_hdu)
            self.show_fields(self.current_hdu)
            self.show_data(self.current_hdu)
        except Exception as exc:
            self.status.showMessage(f"HDU {index} read failed: {exc}")

    def show_session_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return
        menu = self._create_session_context_menu(item)
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _create_session_context_menu(self, item):
        self.tree.setCurrentItem(item)
        root = item if item.parent() is None else item.parent()
        path = root.data(0, ROLE_PATH)
        reader = self.readers.get(self._key(path)) if path else None

        menu = QMenu(self)
        light_curve_action = menu.addAction(
            self.style().standardIcon(QStyle.SP_MediaPlay), "New Light Curve Window..."
        )
        light_curve_action.setEnabled(bool(reader and self._supports_light_curve(reader)))
        light_curve_action.triggered.connect(self.open_light_curve)

        compare_action = menu.addAction(
            self.style().standardIcon(QStyle.SP_FileDialogDetailedView), "Compare FITS Headers..."
        )
        compare_action.setEnabled(bool(reader))
        compare_action.triggered.connect(self.open_header_compare)
        menu.addSeparator()

        expand_action = menu.addAction("Collapse File" if root.isExpanded() else "Expand File")
        expand_action.setEnabled(root.childCount() > 0)
        expand_action.triggered.connect(lambda: root.setExpanded(not root.isExpanded()))
        copy_path_action = menu.addAction("Copy File Path")
        copy_path_action.triggered.connect(
            lambda _checked=False, current=str(path): QApplication.clipboard().setText(current)
        )
        menu.addSeparator()
        remove_action = menu.addAction(
            self.style().standardIcon(QStyle.SP_TrashIcon), "Remove from Session"
        )
        remove_action.triggered.connect(self.close_selected_file)
        return menu

    @staticmethod
    def _supports_light_curve(reader):
        for info in reader.infos:
            names = {field.name.upper() for field in info.fields}
            if info.is_table and "TIME" in names and not info.display_name.upper().startswith("GTI"):
                return True
        return False

    def close_selected_file(self):
        items = self.tree.selectedItems()
        if not items:
            return
        root = items[0] if items[0].parent() is None else items[0].parent()
        path = root.data(0, ROLE_PATH)
        reader = self.readers.pop(self._key(path), None)
        if reader:
            reader.close()
        self.roots.pop(self._key(path), None)
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(root))
        self.reader = None
        self.current_hdu = None
        self.path_label.setText("No file selected")
        self.save_session()

    def restore_session(self):
        paths = self.settings.value("openFiles", [])
        if isinstance(paths, str):
            paths = [paths]
        for path in paths or []:
            self.add_saved_path(path)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def save_session(self):
        self.settings.setValue("openFiles", list(self.roots.keys()))
        self.settings.sync()

    def closeEvent(self, event):
        busy_exports = [window for window in self.analysis_windows if window.export_thread is not None]
        if busy_exports:
            QMessageBox.information(self, "Save in progress", "Wait for light curve exports to finish before closing FitPeek.")
            event.ignore()
            return
        busy_analyses = [window for window in self.analysis_windows if window.analysis_thread is not None]
        if busy_analyses:
            for window in busy_analyses:
                window.cancel_analysis()
            QMessageBox.information(self, "Analysis in progress", "Cancellation requested. Close FitPeek after the analysis stops.")
            event.ignore()
            return
        self.save_session()
        for reader in self.readers.values():
            reader.close()
        event.accept()

    def show_summary(self, reader):
        summary = reader.file_summary()
        self.summary_table.setRowCount(len(summary))
        for row, (name, value) in enumerate(summary.items()):
            self.summary_table.setItem(row, 0, QTableWidgetItem(name))
            self.summary_table.setItem(row, 1, QTableWidgetItem(str(value)))
        self.summary_table.resizeColumnToContents(0)

    def clear_hdu_views(self):
        self.header_table.setRowCount(0)
        self.header_raw.clear()
        self.fields_table.setRowCount(0)
        self._clear_data_model()
        self.data_filter.clear()
        self.export_rows_button.setEnabled(False)
        self.data_info.clear()
        self.tabs.setTabEnabled(self.fields_tab_index, False)
        self.tabs.setTabEnabled(self.data_tab_index, False)

    def show_header(self, index):
        cards = self.reader.header_cards(index)
        self.header_table.setRowCount(len(cards))
        self.header_raw.setPlainText("\n".join(card.raw for card in cards))
        for row, card in enumerate(cards):
            values = [card.key, card.value, type(card.value).__name__ if card.value is not None else "", card.comment]
            for col, value in enumerate(values):
                self.header_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.header_table.resizeColumnsToContents()

    def filter_header(self, text):
        needle = text.lower().strip()
        for row in range(self.header_table.rowCount()):
            visible = not needle or any(
                needle in (self.header_table.item(row, col).text().lower() if self.header_table.item(row, col) else "")
                for col in range(4)
            )
            self.header_table.setRowHidden(row, not visible)

    def show_fields(self, index):
        cols = self.reader.table_schema(index)
        self.fields_table.setRowCount(len(cols))
        for row, col in enumerate(cols):
            vals = [col.name, col.format, col.unit, col.dimensions]
            for column, value in enumerate(vals):
                self.fields_table.setItem(row, column, QTableWidgetItem("" if value is None else str(value)))
        self.fields_table.resizeRowsToContents()

    def show_data(self, index, start=None, count=None):
        meta = self.reader.infos[index]
        self._clear_data_model()
        if not meta.is_table or meta.rows is None:
            self.data_info.setText("No table data in this HDU")
            self._enable_range(False)
            return
        rows = int(meta.rows)
        self._enable_range(rows > 0)
        self.range_start.setMaximum(max(1, rows))
        self.range_end.setMaximum(max(1, rows))
        if start is None:
            mode, count = self.window_combo.currentData()
            start = 0 if mode == "head" else max(0, rows - count)
        count = min(int(count or 100), MAX_PREVIEW_ROWS)
        start = max(0, min(int(start), max(0, rows - 1)))
        count = min(count, max(0, rows - start))
        self.range_start.setValue(start + 1)
        self.range_end.setValue(max(start + 1, start + count))
        self.data_window_text = f"{rows:,} total | preview {start + 1:,}-{start + count:,}"
        model = FitsTableModel(self.reader, index, start, count)
        proxy = TableFilterProxyModel(self.data_view)
        model.setParent(proxy)
        proxy.setSourceModel(model)
        proxy.set_filter_text(self.data_filter.text())
        self.data_view.setModel(proxy)
        self.data_source_model = model
        self.data_proxy_model = proxy
        self.data_view.sortByColumn(-1, Qt.AscendingOrder)
        self.data_view.selectionModel().selectionChanged.connect(self._data_selection_changed)
        self._update_data_info()
        self.export_rows_button.setEnabled(False)
        if count <= 200:
            self.data_view.resizeColumnsToContents()

    def _clear_data_model(self):
        old_proxy = self.data_proxy_model
        self.data_view.setModel(None)
        self.data_proxy_model = None
        self.data_source_model = None
        if old_proxy is not None:
            old_proxy.deleteLater()

    def filter_data(self, text):
        model = self.data_view.model()
        if isinstance(model, TableFilterProxyModel):
            model.set_filter_text(text)
            self.data_view.clearSelection()
            self._update_data_info()

    def _update_data_info(self):
        model = self.data_view.model()
        if not isinstance(model, TableFilterProxyModel):
            return
        source_count = model.sourceModel().rowCount()
        if self.data_filter.text().strip():
            self.data_info.setText(f"{self.data_window_text} | {model.rowCount():,}/{source_count:,} matched")
        else:
            self.data_info.setText(self.data_window_text)

    def _data_selection_changed(self, *_):
        selection = self.data_view.selectionModel()
        self.export_rows_button.setEnabled(bool(selection and selection.selectedRows()))

    def export_selected_rows(self, path=None):
        proxy = self.data_view.model()
        selection = self.data_view.selectionModel()
        selected = selection.selectedRows() if selection else []
        if not isinstance(proxy, TableFilterProxyModel) or not selected:
            if path is None:
                QMessageBox.information(self, "No rows selected", "Select one or more table rows first.")
            return None
        if path is False:
            path = None
        if path is None:
            source_name = self.reader.path.stem if self.reader else "fitpeek"
            default_name = f"{source_name}_hdu{self.current_hdu}_rows.csv"
            path, _ = QFileDialog.getSaveFileName(
                self, "Export selected rows", default_name, "CSV files (*.csv)"
            )
            if not path:
                return None
        path = os.fspath(path)
        if not path.lower().endswith(".csv"):
            path += ".csv"

        source = proxy.sourceModel()
        selected_rows = sorted({index.row() for index in selected})
        with open(path, "w", newline="", encoding="utf-8-sig") as target:
            writer = csv.writer(target)
            headers = [source.headerData(column, Qt.Horizontal, Qt.DisplayRole) for column in range(source.columnCount())]
            writer.writerow(["FITS_ROW", *headers])
            for proxy_row in selected_rows:
                source_row = proxy.mapToSource(proxy.index(proxy_row, 0)).row()
                values = [
                    source.data(source.index(source_row, column), Qt.DisplayRole)
                    for column in range(source.columnCount())
                ]
                writer.writerow([source.start + source_row + 1, *values])
        self.status.showMessage(f"Exported {len(selected_rows)} selected row(s) to {Path(path).name}")
        return path

    def _enable_range(self, enabled):
        self.window_combo.setEnabled(enabled)
        self.range_start.setEnabled(enabled)
        self.range_end.setEnabled(enabled)
        self.range_button.setEnabled(enabled)

    def change_window(self):
        if self.current_hdu is not None and self.reader:
            self.show_data(self.current_hdu)

    def go_to_range(self):
        if self.current_hdu is None or not self.reader:
            return
        start = self.range_start.value()
        end = self.range_end.value()
        if end < start:
            QMessageBox.warning(self, "Invalid range", "The ending row must not be before the starting row.")
            return
        count = end - start + 1
        if count > MAX_PREVIEW_ROWS:
            QMessageBox.warning(self, "Range too large", f"A preview window can contain at most {MAX_PREVIEW_ROWS:,} rows.")
            return
        self.show_data(self.current_hdu, start - 1, count)

    def show_science(self, reader):
        key = self._key(reader.path)
        if self.science_loaded_for == key:
            return
        self.science_loaded_for = key
        has_gti = reader.find_hdu("GTI") is not None
        has_ebounds = reader.find_hdu("EBOUNDS") is not None
        self.tabs.setTabVisible(self.science_tab_index, has_gti or has_ebounds)
        self.science_tabs.setTabVisible(0, has_gti)
        self.science_tabs.setTabVisible(1, has_ebounds)
        self._show_gti(reader)
        self._show_ebounds(reader)

    def _show_gti(self, reader):
        index = reader.find_hdu("GTI")
        self.gti_table.setRowCount(0)
        if index is None:
            self.gti_info.setText("GTI extension not found")
            return
        fields = [field.name.upper() for field in reader.table_schema(index)]
        if "START" not in fields or "STOP" not in fields:
            self.gti_info.setText("GTI extension does not contain START and STOP columns")
            return
        total_rows = reader.infos[index].rows or 0
        shown = min(total_rows, MAX_SCIENCE_ROWS)
        rows = reader.read_table_rows(index, 0, shown)
        start_pos, stop_pos = fields.index("START"), fields.index("STOP")
        display = []
        intervals = []
        reversed_count = 0
        for number, row in enumerate(rows, 1):
            start, stop = float(row[start_pos]), float(row[stop_pos])
            duration = stop - start
            reversed_count += duration < 0
            intervals.append((start, stop))
            display.append((number, self._number(start), self._number(stop), self._number(duration)))
        overlap_count = 0
        previous_stop = None
        for start, stop in sorted(intervals):
            if previous_stop is not None and start < previous_stop:
                overlap_count += 1
            previous_stop = max(previous_stop, stop) if previous_stop is not None else stop
        total_duration = sum(max(0.0, stop - start) for start, stop in intervals)
        suffix = f" | first {shown:,} shown" if shown < total_rows else ""
        self.gti_info.setText(
            f"{total_rows:,} intervals | valid duration {self._number(total_duration)} | "
            f"overlaps {overlap_count} | reversed {reversed_count}{suffix}"
        )
        self._fill_table(self.gti_table, display)

    def _show_ebounds(self, reader):
        index = reader.find_hdu("EBOUNDS")
        self.ebounds_table.setRowCount(0)
        if index is None:
            self.ebounds_info.setText("EBOUNDS extension not found")
            return
        fields = [field.name.upper() for field in reader.table_schema(index)]
        required = ("CHANNEL", "E_MIN", "E_MAX")
        if any(name not in fields for name in required):
            self.ebounds_info.setText("EBOUNDS does not contain CHANNEL, E_MIN and E_MAX columns")
            return
        positions = [fields.index(name) for name in required]
        total_rows = reader.infos[index].rows or 0
        shown = min(total_rows, MAX_SCIENCE_ROWS)
        rows = reader.read_table_rows(index, 0, shown)
        display = []
        for row in rows:
            channel, e_min, e_max = (row[position] for position in positions)
            center = (float(e_min) + float(e_max)) / 2
            display.append((channel, self._number(e_min), self._number(e_max), self._number(center)))
        suffix = f" | first {shown:,} shown" if shown < total_rows else ""
        self.ebounds_info.setText(f"{total_rows:,} energy channels{suffix} | PI/PHA requires EBOUNDS conversion")
        self._fill_table(self.ebounds_table, display)

    @staticmethod
    def _fill_table(table, rows):
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                table.setItem(row_index, column, QTableWidgetItem(str(value)))
        if len(rows) <= 1000:
            table.resizeColumnsToContents()

    @staticmethod
    def _number(value):
        try:
            return format(float(value), ".12g")
        except (TypeError, ValueError):
            return str(value)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setWindowIcon(QIcon(str(resource_path("assets/fitpeek.png"))))
    initial_paths = list(sys.argv[1:])
    window = MainWindow()
    window.show()
    if initial_paths:
        QTimer.singleShot(0, lambda paths=initial_paths: window.open_paths(paths))
    return app.exec()


if __name__ == "__main__":
    main()

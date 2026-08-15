from numbers import Number

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt


class FitsTableModel(QAbstractTableModel):
    """Windowed model: only the requested preview rows are read from the FITS HDU."""

    def __init__(self, reader, hdu_index, start=0, count=100, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.hdu_index = hdu_index
        self.start = start
        self.count = count
        self.columns = []
        self.rows = []
        self.error = None
        self.refresh()

    def refresh(self):
        self.beginResetModel()
        try:
            fields = self.reader.table_schema(self.hdu_index)
            self.columns = [{"name": field.name} for field in fields]
            self.rows = self.reader.read_table_rows(self.hdu_index, self.start, self.count)
            self.error = None
        except Exception as exc:
            self.columns, self.rows = [], []
            self.error = str(exc)
        self.endResetModel()

    def set_window(self, start, count):
        self.start, self.count = max(0, int(start)), max(1, int(count))
        self.refresh()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            col = self.columns[section]
            return col.get("name", "") if isinstance(col, dict) else getattr(col, "name", "")
        if orientation == Qt.Vertical and 0 <= section < len(self.rows):
            return str(self.start + section + 1)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        try:
            value = self.rows[index.row()][index.column()]
            if role == Qt.UserRole:
                return value
            if role not in (Qt.DisplayRole, Qt.ToolTipRole):
                return None
            if value is None:
                return ""
            if hasattr(value, "item"):
                value = value.item()
            if isinstance(value, bytes):
                value = value.decode(errors="replace")
            if isinstance(value, (list, tuple)):
                value = "[" + ", ".join(str(v) for v in value) + "]"
            if isinstance(value, float):
                return format(value, ".12g")
            return str(value)
        except Exception as exc:
            return f"<error: {exc}>"

    def raw_value(self, row, column):
        return self.rows[row][column]


class TableFilterProxyModel(QSortFilterProxyModel):
    """Filters every visible column and keeps numeric columns numerically sortable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._terms = []
        self.setDynamicSortFilter(True)
        self.setSortCaseSensitivity(Qt.CaseInsensitive)

    def set_filter_text(self, text):
        self._terms = str(text).casefold().split()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._terms:
            return True
        model = self.sourceModel()
        values = [
            str(model.data(model.index(source_row, column, source_parent), Qt.DisplayRole) or "").casefold()
            for column in range(model.columnCount(source_parent))
        ]
        haystack = " ".join(values)
        return all(term in haystack for term in self._terms)

    def lessThan(self, left, right):
        model = self.sourceModel()
        left_value = model.raw_value(left.row(), left.column())
        right_value = model.raw_value(right.row(), right.column())
        if isinstance(left_value, Number) and isinstance(right_value, Number):
            return float(left_value) < float(right_value)
        if left_value is None:
            return right_value is not None
        if right_value is None:
            return False
        return str(left_value).casefold() < str(right_value).casefold()

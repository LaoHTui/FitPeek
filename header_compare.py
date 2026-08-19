from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class HeaderCompareWindow(QDialog):
    def __init__(self, sources, left_source=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.sources = list(sources)
        self.source_lookup = {(str(source[0]), source[1]): source for source in self.sources}
        self.setWindowTitle("FITS Header Compare")
        self.resize(1180, 680)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._build_ui(left_source)
        self.refresh()

    def _build_ui(self, left_source):
        root = QVBoxLayout(self)
        source_row = QHBoxLayout()
        self.left_combo = QComboBox()
        self.right_combo = QComboBox()
        for combo in (self.left_combo, self.right_combo):
            for path, hdu_index, hdu_name, _cards in self.sources:
                label = f"{path.name} :: [{hdu_index}] {hdu_name}"
                combo.addItem(label, (str(path), hdu_index))
                combo.setItemData(combo.count() - 1, str(path), Qt.ToolTipRole)
        source_row.addWidget(QLabel("Left"))
        source_row.addWidget(self.left_combo, 1)
        source_row.addSpacing(12)
        source_row.addWidget(QLabel("Right"))
        source_row.addWidget(self.right_combo, 1)
        root.addLayout(source_row)

        if left_source:
            left_identity = (str(left_source[0]), int(left_source[1]))
            left_index = next(
                (
                    index for index in range(self.left_combo.count())
                    if tuple(self.left_combo.itemData(index)) == left_identity
                ),
                -1,
            )
            if left_index >= 0:
                self.left_combo.setCurrentIndex(left_index)
        else:
            left_index = 0
            self.left_combo.setCurrentIndex(left_index)

        if self.right_combo.count() > 1:
            # Prefer a different FITS path, then fall back to the next HDU.
            left_identity = self.left_combo.currentData()
            right_index = next(
                (
                    index for index in range(self.right_combo.count())
                    if self.right_combo.itemData(index)[0] != left_identity[0]
                ),
                (self.left_combo.currentIndex() + 1) % self.right_combo.count(),
            )
            self.right_combo.setCurrentIndex(right_index)

        filter_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter keywords, values, or comments")
        self.search.setClearButtonEnabled(True)
        self.mode = QComboBox()
        self.mode.addItem("All cards", "all")
        self.mode.addItem("Differences only", "different")
        self.mode.addItem("Matching only", "same")
        self.summary = QLabel()
        filter_row.addWidget(self.search, 1)
        filter_row.addWidget(self.mode)
        filter_row.addWidget(self.summary)
        root.addLayout(filter_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Keyword", "Left value", "Right value", "Status", "Left comment", "Right comment",
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        self.left_combo.currentIndexChanged.connect(self.refresh)
        self.right_combo.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.apply_filter)
        self.mode.currentIndexChanged.connect(self.apply_filter)

    @staticmethod
    def _indexed_cards(cards):
        occurrences = {}
        order = []
        indexed = {}
        for card in cards:
            key = str(card.key or "<blank>")
            occurrences[key] = occurrences.get(key, 0) + 1
            identity = (key, occurrences[key])
            order.append(identity)
            indexed[identity] = card
        return order, indexed

    def _selected_source(self, combo):
        identity = combo.currentData()
        if not identity:
            return None
        return self.source_lookup.get((str(identity[0]), int(identity[1])))

    def refresh(self, *_):
        left_source = self._selected_source(self.left_combo)
        right_source = self._selected_source(self.right_combo)
        if not left_source or not right_source:
            self.table.setRowCount(0)
            self.summary.setText("No sources")
            return

        left_order, left_cards = self._indexed_cards(left_source[3])
        right_order, right_cards = self._indexed_cards(right_source[3])
        identities = left_order + [identity for identity in right_order if identity not in left_cards]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(identities))
        counts = {"Same": 0, "Changed": 0, "Only left": 0, "Only right": 0}
        for row, identity in enumerate(identities):
            left = left_cards.get(identity)
            right = right_cards.get(identity)
            if left is None:
                status = "Only right"
            elif right is None:
                status = "Only left"
            elif str(left.value) == str(right.value) and str(left.comment) == str(right.comment):
                status = "Same"
            else:
                status = "Changed"
            counts[status] += 1
            keyword = identity[0] if identity[1] == 1 else f"{identity[0]} [{identity[1]}]"
            values = [
                keyword,
                "" if left is None or left.value is None else left.value,
                "" if right is None or right.value is None else right.value,
                status,
                "" if left is None else left.comment,
                "" if right is None else right.comment,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.setSortingEnabled(True)
        different = counts["Changed"] + counts["Only left"] + counts["Only right"]
        self.summary.setText(f"{len(identities)} cards | {different} differences")
        self.apply_filter()

    def apply_filter(self, *_):
        needle = self.search.text().casefold().strip()
        mode = self.mode.currentData()
        for row in range(self.table.rowCount()):
            status = self.table.item(row, 3).text()
            mode_matches = (
                mode == "all"
                or (mode == "different" and status != "Same")
                or (mode == "same" and status == "Same")
            )
            text_matches = not needle or any(
                needle in self.table.item(row, column).text().casefold()
                for column in range(self.table.columnCount())
                if self.table.item(row, column)
            )
            self.table.setRowHidden(row, not (mode_matches and text_matches))

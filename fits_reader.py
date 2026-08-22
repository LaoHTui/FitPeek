"""Read-only, lazy FITS inspection helpers for FitPeek.

The reader intentionally keeps Astropy HDU objects open and uses ``memmap``
for uncompressed files.  It never writes to the source file and only reads
the selected table slice when ``read_table_rows`` is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

import numpy as np

try:  # Keep import errors useful when running the build bootstrapper.
    from astropy.io import fits
except Exception:  # pragma: no cover - exercised only without astropy
    fits = None  # type: ignore[assignment]


TRIGGER_KEYWORDS = (
    "TRIGTIME", "BST_TIME", "BURST_TIME", "TRIGGER_TIME",
    "TRIGGER", "GRB_TIME", "BURSTTIM", "T0",
)
_TRIGGER_EXCLUDED_NUMERIC_KEYS = {
    "BITPIX", "EXTVER", "EXPOSURE", "LIVETIME", "MJDREF",
    "MJDREFI", "MJDREFF", "NAXIS", "PCOUNT", "GCOUNT",
    "TELAPSE", "TIMEDEL", "TIMEPIXR", "TIMEZERO", "TZERO",
}


def resolve_trigger_time(hdul: Any) -> dict[str, Any]:
    """Resolve a trigger time while preserving the original header location."""
    explicit = []
    numeric = []
    bounds = []
    rank = {key: position for position, key in enumerate(TRIGGER_KEYWORDS)}
    for index, hdu in enumerate(hdul or []):
        header = getattr(hdu, "header", None)
        if header is None:
            continue
        for key in header:
            upper = str(key).upper().strip()
            try:
                value = float(header[key])
            except (TypeError, ValueError):
                continue
            if not np.isfinite(value):
                continue
            if upper in {"TSTART", "TSTOP", "TEND"}:
                bounds.append(value)
                continue
            comment = str(header.comments[key] or "").lower()
            trigger_hint = (
                upper in rank or "TRIG" in upper or upper.startswith("BST")
                or "trigger time" in comment or "burst time" in comment
            )
            if trigger_hint:
                explicit.append((rank.get(upper, len(rank)), upper, index, value))
            elif (
                upper not in _TRIGGER_EXCLUDED_NUMERIC_KEYS
                and not upper.startswith(("NAXIS", "TFORM", "TTYPE", "TUNIT", "TLMIN", "TLMAX"))
            ):
                numeric.append((value, upper, index))
    if explicit:
        _, key, index, value = min(explicit)
        return {"value": value, "keyword": key, "hdu_index": index, "method": "explicit keyword"}
    if len(bounds) >= 2:
        lo, hi = min(bounds), max(bounds)
        midpoint = (lo + hi) / 2.0
        inside = [item for item in numeric if lo <= item[0] <= hi]
        if inside:
            value, key, index = min(inside, key=lambda item: abs(item[0] - midpoint))
            return {
                "value": value, "keyword": key, "hdu_index": index,
                "method": "nearest numeric value to TSTART/TSTOP midpoint",
            }
    return {"value": None, "keyword": None, "hdu_index": None, "method": "not found"}


@dataclass
class HeaderCard:
    key: str
    value: Any = None
    comment: str = ""
    raw: str = ""

    def __str__(self) -> str:
        return self.raw or self.key


@dataclass
class TableField:
    name: str
    format: str = ""
    python_type: str = ""
    unit: str = ""
    dimensions: str = ""
    nullable: bool = False
    length: int = 0
    min_value: Any = None
    max_value: Any = None
    variable_length: bool = False
    error: str | None = None


@dataclass
class HDUInfo:
    index: int
    name: str
    hdu_type: str
    rows: int | None = None
    shape: tuple[int, ...] | None = None
    is_table: bool = False
    is_image: bool = False
    header_cards: list[HeaderCard] = field(default_factory=list)
    fields: list[TableField] = field(default_factory=list)
    error: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or ("PRIMARY" if self.index == 0 else f"HDU {self.index}")

    @property
    def summary(self) -> str:
        if self.error:
            return f"{self.display_name} ({self.hdu_type}) - {self.error}"
        if self.is_table and self.rows is not None:
            return f"{self.display_name} ({self.hdu_type}, {self.rows:,} rows)"
        if self.shape:
            return f"{self.display_name} ({self.hdu_type}, {self.shape})"
        return f"{self.display_name} ({self.hdu_type})"


class FITSReader:
    """Lifecycle and lazy access for one FITS path.

    ``open`` is a classmethod convenience; callers may also instantiate and
    call ``load``.  HDU metadata is best-effort: a malformed extension is
    represented by an ``HDUInfo`` with ``error`` while other extensions remain
    inspectable whenever Astropy can parse the file structure.
    """

    SUPPORTED_EXTENSIONS = {".fits", ".fit", ".fits.gz", ".evt", ".pha", ".rsp", ".rsp2", ".rm"}

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._hdulist: Any = None
        self._infos: list[HDUInfo] = []
        self.open_error: str | None = None
        self._compressed = self.path.name.lower().endswith(".gz")
        # The MVP window constructs ``FitsReader(path)`` directly, so load
        # metadata eagerly while retaining lazy table-row access.
        self.load()

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> "FITSReader":
        reader = cls(path)
        return reader

    def load(self) -> list[HDUInfo]:
        self.close()
        self._infos = []
        self.open_error = None
        if fits is None:
            self.open_error = "Astropy is not installed"
            return self._infos
        try:
            # Astropy can handle gzip paths directly.  memmap is disabled for
            # compressed streams because random access is unavailable there.
            self._hdulist = fits.open(
                str(self.path), mode="readonly", memmap=not self._compressed,
                lazy_load_hdus=False, ignore_missing_end=True,
            )
        except Exception as exc:
            self.open_error = f"Unable to open FITS: {exc}"
            return self._infos

        for index, hdu in enumerate(self._hdulist):
            try:
                self._infos.append(self._inspect_hdu(index, hdu))
            except Exception as exc:  # keep one bad extension from aborting load
                name = getattr(hdu, "name", "") or ("PRIMARY" if index == 0 else f"HDU {index}")
                self._infos.append(HDUInfo(index=index, name=str(name), hdu_type=type(hdu).__name__, error=str(exc)))
        return self._infos

    @property
    def infos(self) -> list[HDUInfo]:
        return self._infos

    @property
    def hdus(self) -> list[dict[str, Any]]:
        """Dictionary view retained for the initial Qt UI implementation."""
        return [
            {
                "index": info.index,
                "name": info.display_name,
                "type": info.hdu_type,
                "rows": info.rows,
                "shape": info.shape,
                "is_table": info.is_table,
                "is_image": info.is_image,
                "error": info.error,
            }
            for info in self._infos
        ]

    @property
    def hdu_count(self) -> int:
        return len(self._infos)

    @property
    def file_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def file_size_text(self) -> str:
        size = self.file_size
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
            size /= 1024

    def _inspect_hdu(self, index: int, hdu: Any) -> HDUInfo:
        header_cards = self._header_cards(getattr(hdu, "header", None))
        hdu_type = type(hdu).__name__
        name = str(getattr(hdu, "name", "") or ("PRIMARY" if index == 0 else f"HDU {index}"))
        info = HDUInfo(index=index, name=name, hdu_type=hdu_type, header_cards=header_cards)

        header = getattr(hdu, "header", None)
        # BinTableHDU/TableHDU expose columns. Read row count from NAXIS2 so
        # opening a multi-million-row table does not materialize its data.
        columns = getattr(hdu, "columns", None)
        if columns is not None:
            info.is_table = True
            try:
                info.rows = int(header.get("NAXIS2", 0)) if header is not None else None
            except Exception:
                info.rows = None
            info.fields = self._table_fields(hdu)
        elif header is not None and int(header.get("NAXIS", 0) or 0) > 0:
            info.is_image = True
            try:
                ndim = int(header.get("NAXIS", 0))
                info.shape = tuple(int(header.get(f"NAXIS{i}", 0)) for i in range(ndim, 0, -1))
            except Exception:
                info.shape = None
        return info

    @staticmethod
    def _header_cards(header: Any) -> list[HeaderCard]:
        if header is None:
            return []
        cards: list[HeaderCard] = []
        try:
            for card in header.cards:
                raw = str(card)
                # Astropy preserves key/value/comment and raw formatting.
                cards.append(HeaderCard(str(card.keyword), card.value, str(card.comment or ""), raw))
        except Exception:
            # Header may be partially corrupt; retain a useful textual view.
            try:
                for raw in str(header).splitlines():
                    cards.append(HeaderCard(raw[:8].strip(), raw[8:].strip(), raw=raw[:80]))
            except Exception:
                pass
        return cards

    @staticmethod
    def _table_fields(hdu: Any) -> list[TableField]:
        fields: list[TableField] = []
        columns = hdu.columns
        names = list(getattr(columns, "names", []) or [])
        for i, name in enumerate(names):
            try:
                col = columns[i]
                fmt = str(getattr(col, "format", "") or "")
                dtype = getattr(col, "dtype", None)
                pytype = str(getattr(dtype, "name", dtype) or "")
                dim = str(getattr(col, "dim", "") or "")
                unit = str(getattr(col, "unit", "") or "")
                fields.append(TableField(
                    name=str(name), format=fmt, python_type=pytype, unit=unit,
                    dimensions=dim, nullable=fmt.startswith("P") or fmt.startswith("Q"),
                    variable_length=fmt.startswith(("P", "Q")),
                ))
            except Exception as exc:
                fields.append(TableField(name=str(name), error=str(exc)))
        return fields

    def header_cards(self, hdu_index: int) -> list[HeaderCard]:
        return self._infos[hdu_index].header_cards if 0 <= hdu_index < len(self._infos) else []

    def header(self, hdu_index: int) -> list[HeaderCard]:
        """Alias used by the UI's header page."""
        return self.header_cards(hdu_index)

    def raw_header(self, hdu_index: int) -> list[str]:
        return [card.raw for card in self.header_cards(hdu_index)]

    def table_schema(self, hdu_index: int) -> list[TableField]:
        return self._infos[hdu_index].fields if 0 <= hdu_index < len(self._infos) else []

    def table_column_bounds(self, hdu_index: int, column_name: str) -> tuple[float, float] | None:
        """Return finite numeric bounds for one table column without copying rows."""
        if self._hdulist is None or not (0 <= hdu_index < len(self._hdulist)):
            return None
        hdu = self._hdulist[hdu_index]
        data = getattr(hdu, "data", None)
        names = list(getattr(getattr(hdu, "columns", None), "names", []) or [])
        match = next((name for name in names if str(name).upper() == str(column_name).upper()), None)
        if data is None or match is None:
            return None
        try:
            values = np.asarray(data[match], dtype=float)
            finite = values[np.isfinite(values)]
            if finite.size == 0:
                return None
            return float(np.min(finite)), float(np.max(finite))
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

    def table_columns(self, hdu_index: int) -> list[dict[str, Any]]:
        """Return serialisable field dictionaries for a QTableWidget."""
        result = []
        for field_info in self.table_schema(hdu_index):
            result.append({
                "name": field_info.name,
                "format": field_info.format,
                "python_type": field_info.python_type,
                "unit": field_info.unit,
                "shape": field_info.dimensions,
                "dimensions": field_info.dimensions,
                "length": field_info.length,
                "min": field_info.min_value,
                "max": field_info.max_value,
                "variable_length": field_info.variable_length,
                "error": field_info.error,
            })
        return result

    def find_hdu(self, *names: str) -> int | None:
        wanted = {name.upper() for name in names}
        for info in self._infos:
            current = info.display_name.upper()
            if current in wanted or any(current.startswith(name) for name in wanted):
                return info.index
        return None

    def header_value(self, keyword: str, default: Any = None) -> Any:
        keyword = keyword.upper()
        for index in range(len(self._infos)):
            for card in self.header_cards(index):
                if card.key.upper() == keyword:
                    return card.value
        return default

    def file_summary(self) -> dict[str, Any]:
        names = [info.display_name.upper() for info in self._infos]
        return {
            "File": self.path.name,
            "Path": str(self.path),
            "Size": self.file_size_text,
            "HDU count": self.hdu_count,
            "Event HDUs": sum(name.startswith("EVENTS") for name in names),
            "Has GTI": any(name.startswith("GTI") for name in names),
            "Has EBOUNDS": "EBOUNDS" in names,
            "Telescope": self.header_value("TELESCOP", ""),
            "Instrument": self.header_value("INSTRUME", ""),
            "Object": self.header_value("OBJECT", ""),
            "Time system": self.header_value("TIMESYS", ""),
            "Trigger time": self.trigger_time_info().get("value", ""),
            "Trigger time source": self._trigger_summary_text(),
            "RA (deg)": self.header_value("RA_OBJ", ""),
            "DEC (deg)": self.header_value("DEC_OBJ", ""),
        }

    def trigger_time_info(self) -> dict[str, Any]:
        """Resolve trigger time and retain its original FITS keyword/location.

        Explicit trigger-like keywords win.  When absent, numeric header values
        nearest the TSTART/TSTOP midpoint are used as a conservative fallback.
        """
        return resolve_trigger_time(self._hdulist)

    def _trigger_summary_text(self) -> str:
        info = self.trigger_time_info()
        if info.get("value") is None:
            return "not found (relative time disabled)"
        return f"{info.get('keyword')} in HDU {info.get('hdu_index')} ({info.get('method')})"

    def read_table_rows(self, hdu_index: int, start: int = 0, count: int = 100) -> list[tuple[Any, ...]]:
        """Read only ``count`` rows from a table HDU.

        Values are converted to plain Python values where practical so Qt's
        model can render NumPy scalars and variable-length arrays safely.
        """
        if self._hdulist is None:
            raise RuntimeError("FITS file is not open")
        if hdu_index < 0 or hdu_index >= len(self._hdulist):
            raise IndexError(hdu_index)
        if count <= 0:
            return []
        hdu = self._hdulist[hdu_index]
        data = getattr(hdu, "data", None)
        if data is None or getattr(hdu, "columns", None) is None:
            return []
        total = int(getattr(data, "shape", (0,))[0])
        start = max(0, min(int(start), total))
        end = min(total, start + int(count))
        result: list[tuple[Any, ...]] = []
        names = list(getattr(hdu.columns, "names", []) or [])
        for row in data[start:end]:
            values = []
            for name in names:
                try:
                    value = row[name]
                    if hasattr(value, "tolist"):
                        value = value.tolist()
                    elif hasattr(value, "item"):
                        value = value.item()
                except Exception as exc:
                    value = f"<error: {exc}>"
                values.append(value)
            result.append(tuple(values))
        return result

    def read_rows(self, hdu_index: int, start: int = 0, count: int = 100) -> list[tuple[Any, ...]]:
        return self.read_table_rows(hdu_index, start, count)

    def time_bounds(self, hdu_indices=None, relative_to_trigtime=False, time_column="TIME"):
        """Return the finite TIME bounds across selected event HDUs."""
        if self._hdulist is None and self.open_error is None:
            self.load()
        indices = list(hdu_indices) if hdu_indices is not None else range(len(self._hdulist or []))
        values = []
        for index in indices:
            if index < 0 or index >= len(self._hdulist or []):
                continue
            data = getattr(self._hdulist[index], "data", None)
            names = {str(name).upper(): name for name in (getattr(data, "names", []) or [])}
            column = str(time_column or "TIME").upper()
            if data is None or column not in names:
                continue
            times = np.asarray(data[names[column]], dtype=np.float64)
            times = times[np.isfinite(times)]
            if times.size:
                values.append((float(np.min(times)), float(np.max(times))))
        if not values:
            header_bounds = []
            for index in indices:
                if index < 0 or index >= len(self._hdulist or []):
                    continue
                header = getattr(self._hdulist[index], "header", None)
                if header is None:
                    continue
                start = header.get("TSTART")
                stop = header.get("TSTOP", header.get("TEND"))
                try:
                    start, stop = float(start), float(stop)
                except (TypeError, ValueError):
                    continue
                if np.isfinite(start) and np.isfinite(stop) and start < stop:
                    header_bounds.append((start, stop))
            if not header_bounds:
                return None
            values = header_bounds
        start = min(value[0] for value in values)
        end = max(value[1] for value in values)
        if relative_to_trigtime:
            trigtime = self.trigger_time_info().get("value")
            if trigtime is not None:
                start -= float(trigtime)
                end -= float(trigtime)
        return start, end

    @staticmethod
    def parse_card(card: HeaderCard | str) -> tuple[str, Any, str]:
        if isinstance(card, HeaderCard):
            return card.key, card.value, card.comment
        raw = str(card)
        key = raw[:8].strip()
        return key, raw[8:].strip(), ""

    def close(self) -> None:
        if self._hdulist is not None:
            try:
                self._hdulist.close(output_verify="ignore")
            except Exception:
                pass
            self._hdulist = None

    def __enter__(self) -> "FITSReader":
        if self._hdulist is None and self.open_error is None:
            self.load()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter cleanup
        self.close()


FitsReader = FITSReader

__all__ = ["FITSReader", "FitsReader", "HDUInfo", "HeaderCard", "TableField", "resolve_trigger_time"]

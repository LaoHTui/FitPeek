"""Targeted Fermi/GBM and GECAM event extraction.

Each input is validated against the satellite selected by the caller. Files
from another mission, or files with an incompatible product structure, are
reported as skipped instead of being interpreted heuristically.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from astropy.io import fits


class ExtractionCancelled(Exception):
    """Raised when the user requests cancellation between file operations."""


@dataclass(frozen=True)
class EnergyBand:
    low: float
    high: float

    @property
    def tag(self) -> str:
        if math.isinf(self.high):
            return "all"
        return f"{_number_tag(self.low)}_{_number_tag(self.high)}"


@dataclass
class EventData:
    path: Path
    object_name: str
    detector: str
    time: np.ndarray
    time_rel: np.ndarray
    energy: np.ndarray
    energy_low: np.ndarray
    energy_high: np.ndarray
    pha: np.ndarray
    gti: np.ndarray
    trigtime: float
    has_trigger: bool
    has_energy: bool


def _number_tag(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}".replace(".", "p")


def parse_energy_bands(value: str | Sequence[Sequence[float]]) -> list[EnergyBand]:
    if not isinstance(value, str):
        return [EnergyBand(float(pair[0]), float(pair[1])) for pair in value]
    text = value.strip()
    if not text or text.lower() in {"all", "*", "any"}:
        return [EnergyBand(0.0, math.inf)]
    bands = []
    for item in text.split(","):
        match = re.fullmatch(r"\s*([0-9.eE+-]+)\s*[-:]\s*([0-9.eE+-]+)\s*", item)
        if not match:
            raise ValueError(f"Cannot parse energy band '{item}'. Use LOW-HIGH, e.g. 8-50,50-300")
        low, high = float(match.group(1)), float(match.group(2))
        if not np.isfinite(low) or not np.isfinite(high) or low < 0 or high <= low:
            raise ValueError(f"Invalid energy band: {item}")
        bands.append(EnergyBand(low, high))
    return bands


def parse_background_windows(value: str | None) -> list[tuple[float, float]]:
    if value is None or not value.strip() or value.strip().lower() in {"none", "off", "0"}:
        return []
    windows = []
    for item in value.split(","):
        match = re.fullmatch(r"\s*([0-9.eE+-]+)\s*[-:]\s*([0-9.eE+-]+)\s*", item)
        if not match:
            raise ValueError(f"Cannot parse background window '{item}'")
        start, stop = float(match.group(1)), float(match.group(2))
        if not np.isfinite(start) or not np.isfinite(stop) or stop <= start:
            raise ValueError(f"Invalid background window: {item}")
        windows.append((start, stop))
    return windows


def discover_event_files(paths: Sequence[str | Path]) -> list[Path]:
    found: list[Path] = []
    for value in paths:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*") if p.is_file() and _is_fits(p)))
        elif path.is_file() and _is_fits(path):
            found.append(path)
    unique = []
    seen = set()
    for path in found:
        if path not in seen:
            unique.append(path); seen.add(path)
    if not unique:
        raise FileNotFoundError("No event FITS files were selected")
    return unique


def _is_fits(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".fits", ".fit", ".fits.gz", ".fit.gz", ".evt"))


def _find_event_hdus(hdul, satellite: str | None = None):
    found = []
    for hdu in hdul:
        names = {str(col).upper() for col in (hdu.columns.names or [])} if hasattr(hdu, "columns") else set()
        name = str(getattr(hdu, "name", "") or "").upper()
        if satellite == "gecam":
            event_named = bool(re.fullmatch(r"EVENTS\d+", name))
        elif satellite == "fermi":
            event_named = name in {"EVENTS", "EVENT"} or name.startswith("EVENTS")
        else:
            event_named = False
        if "TIME" in names and not ({"START", "STOP"} <= names) and event_named:
            found.append(hdu)
    return found


def _find_event_hdu(hdul, satellite: str | None = None):
    found = _find_event_hdus(hdul, satellite)
    if not found:
        raise ValueError("No table containing a TIME column was found")
    return found[0]


def _find_gti(hdul) -> np.ndarray:
    for hdu in hdul:
        names = {str(col).upper() for col in (hdu.columns.names or [])} if hasattr(hdu, "columns") else set()
        if "START" in names and "STOP" in names:
            return np.column_stack((np.asarray(hdu.data["START"], float), np.asarray(hdu.data["STOP"], float)))
    return np.empty((0, 2), dtype=float)


def _read_event_hdu(
    hdul, path: Path, events, use_gti: bool = True,
    detector_override: str | None = None, row_mask: np.ndarray | None = None,
) -> EventData:
    primary = hdul[0].header
    names = {str(name).upper(): str(name) for name in events.columns.names}
    time = np.asarray(events.data[names["TIME"]], dtype=float).reshape(-1)
    mask = np.ones(time.shape, dtype=bool) if row_mask is None else np.asarray(row_mask, dtype=bool).reshape(-1)
    if mask.shape != time.shape:
        raise ValueError("Event row mask does not match the event table")
    has_trigger = primary.get("TRIGTIME") is not None
    trigtime = float(primary.get("TRIGTIME", 0.0) or 0.0)
    gti = _find_gti(hdul)
    if use_gti and gti.size:
        gti_mask = np.zeros(time.shape, dtype=bool)
        for start, stop in gti:
            gti_mask |= (time >= start) & (time < stop)
        mask &= gti_mask
    time = time[mask]
    energy = np.full(time.shape, np.nan, dtype=float)
    energy_low = np.full(time.shape, np.nan, dtype=float)
    energy_high = np.full(time.shape, np.nan, dtype=float)
    pha = np.full(time.shape, np.nan, dtype=float)
    has_energy = False
    energy_name = next((names[key] for key in ("ENERGY", "E", "PI_ENERGY") if key in names), None)
    pha_name = next((names[key] for key in ("PHA", "PI", "CHANNEL") if key in names), None)
    if energy_name:
        energy = np.asarray(events.data[energy_name], dtype=float).reshape(-1)
        energy = energy[mask]
        energy_low = energy.copy()
        energy_high = energy.copy()
        has_energy = np.any(np.isfinite(energy))
    if pha_name:
        pha = np.asarray(events.data[pha_name], dtype=float).reshape(-1)
        pha = pha[mask]
        if not has_energy:
            for hdu in hdul:
                hnames = {str(col).upper(): str(col) for col in (hdu.columns.names or [])} if hasattr(hdu, "columns") else {}
                if {"CHANNEL", "E_MIN", "E_MAX"} <= set(hnames):
                    channels = np.asarray(hdu.data[hnames["CHANNEL"]], dtype=float)
                    lows = np.asarray(hdu.data[hnames["E_MIN"]], float)
                    highs = np.asarray(hdu.data[hnames["E_MAX"]], float)
                    order = np.argsort(channels)
                    channels, lows, highs = channels[order], lows[order], highs[order]
                    index = np.searchsorted(channels, pha)
                    valid = (index < len(channels)) & np.isfinite(pha)
                    valid &= channels[np.minimum(index, max(0, len(channels) - 1))] == pha
                    energy_low[valid] = lows[index[valid]]
                    energy_high[valid] = highs[index[valid]]
                    energy[valid] = (energy_low[valid] + energy_high[valid]) / 2.0
                    has_energy = bool(np.any(valid)); break
    object_name = str(primary.get("OBJECT", primary.get("SOURCE", path.stem))).strip() or path.stem
    detector = detector_override or str(events.header.get("DETNAM", events.header.get("DETECTOR", ""))).strip()
    detector = detector or str(events.name or primary.get("DETNAM", primary.get("DETECTOR", path.stem))).strip()
    detector = detector or path.stem
    return EventData(path, object_name, detector, time, time - trigtime, energy, energy_low, energy_high, pha, gti, trigtime, has_trigger, has_energy)


def _validate_satellite(hdul, satellite: str) -> None:
    satellite = satellite.lower().strip()
    if satellite not in {"fermi", "gecam"}:
        raise ValueError("Unsupported satellite; choose Fermi/GBM or GECAM")
    header = hdul[0].header
    telescope = str(header.get("TELESCOP", "")).upper().strip()
    instrument = str(header.get("INSTRUME", "")).upper().strip()
    datatype = str(header.get("DATATYPE", "")).upper().strip()
    names = {str(getattr(hdu, "name", "") or "").upper() for hdu in hdul}
    if satellite == "fermi":
        known = ("FERMI" in telescope or "GLAST" in telescope or instrument in {"GBM", "GLAST"})
        if not known:
            raise ValueError("not a Fermi/GBM file")
        if "EBOUNDS" not in names:
            raise ValueError("Fermi/GBM file has no EBOUNDS extension")
    else:
        known = telescope in {"GECAM-A", "GECAM-B", "HEBS"} or "GECAM" in telescope
        if not known:
            raise ValueError("not a GECAM file")
        if datatype and datatype != "EVT":
            raise ValueError(f"GECAM DATATYPE is {datatype}, expected EVT")
        if "EBOUNDS" not in names:
            raise ValueError("GECAM file has no EBOUNDS extension")
        if not any(re.fullmatch(r"EVENTS\d+", name) for name in names):
            raise ValueError("GECAM file has no EVENTSnn extensions")


def read_event_file_parts(path: str | Path, use_gti: bool = True, satellite: str = "fermi") -> list[EventData]:
    path = Path(path).expanduser().resolve()
    with fits.open(path, memmap=True) as hdul:
        satellite = satellite.lower().strip()
        _validate_satellite(hdul, satellite)
        event_hdus = _find_event_hdus(hdul, satellite)
        if not event_hdus:
            raise ValueError("No table containing a TIME column was found")
        primary_detector = str(hdul[0].header.get("DETNAM", hdul[0].header.get("DETECTOR", ""))).strip()
        primary = hdul[0].header
        trig = primary.get("TRIGTIME", primary.get("BST_TIME"))
        if trig is not None:
            primary["TRIGTIME"] = trig
        parts: list[EventData] = []
        for hdu in event_hdus:
            hdu_name = str(hdu.name or "").upper()
            detector = str(hdu.header.get("DETNAM", hdu.header.get("DETECTOR", ""))).strip()
            if satellite == "gecam" and not detector:
                number = re.search(r"(\d+)$", hdu_name)
                detector = f"{str(primary.get('INSTRUME', 'DET')).upper()}{number.group(1)}" if number else hdu_name
            detector = detector or (hdu_name if len(event_hdus) > 1 else primary_detector) or None
            names = {str(name).upper(): str(name) for name in hdu.columns.names}
            base_mask = np.ones(len(hdu.data), dtype=bool)
            if satellite == "gecam" and "FLAG" in names:
                base_mask &= np.asarray(hdu.data[names["FLAG"]]) < 10
            if satellite == "gecam" and "GAIN_TYPE" in names:
                gains = np.asarray(hdu.data[names["GAIN_TYPE"]])
                for gain_value, suffix in ((0, "H"), (1, "L")):
                    gain_mask = base_mask & (gains == gain_value)
                    if np.any(gain_mask):
                        parts.append(_read_event_hdu(hdul, path, hdu, use_gti, f"{detector}{suffix}", gain_mask))
            else:
                parts.append(_read_event_hdu(hdul, path, hdu, use_gti, detector, base_mask))
        return parts


def read_event_file(path: str | Path, use_gti: bool = True, satellite: str = "fermi") -> EventData:
    """Read the first event stream for backward compatibility."""
    return read_event_file_parts(path, use_gti=use_gti, satellite=satellite)[0]


def _energy_mask(data: EventData, band: EnergyBand, overlap: bool = False) -> np.ndarray:
    if math.isinf(band.high):
        return np.ones(data.time.shape, dtype=bool)
    if not data.has_energy:
        return np.zeros(data.time.shape, dtype=bool)
    finite = np.isfinite(data.energy_low) & np.isfinite(data.energy_high)
    if overlap:
        return finite & (data.energy_high > band.low) & (data.energy_low < band.high)
    return finite & (data.energy_low >= band.low) & (data.energy_high <= band.high)


def _time_edges(times: np.ndarray, dt: float, start: float | None, stop: float | None) -> np.ndarray:
    if not np.isfinite(dt) or dt <= 0: raise ValueError("Bin width must be positive")
    if times.size == 0 and (start is None or stop is None):
        raise ValueError("Cannot infer a time range from an empty event selection")
    lo = float(np.min(times)) if start is None else float(start)
    hi = (float(np.max(times)) + dt) if stop is None else float(stop)
    if hi <= lo: raise ValueError("Time range must have a positive duration")
    return lo + np.arange(int(np.ceil((hi - lo) / dt - 1e-12)) + 1) * dt


def make_lightcurve(
    times: np.ndarray, dt: float, start=None, stop=None, background_windows=(),
    available_start=None, available_stop=None,
):
    edges = _time_edges(times, dt, start, stop)
    counts, _ = np.histogram(times, bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2
    raw = counts.astype(float) / dt
    raw_err = np.sqrt(counts.astype(float)) / dt
    bg = np.zeros_like(raw); bg_err = np.zeros_like(raw)
    if background_windows:
        available_start = float(np.min(times)) if available_start is None and times.size else available_start
        available_stop = float(np.max(times)) if available_stop is None and times.size else available_stop
        fit_centers = []
        fit_counts = []
        for begin, end in background_windows:
            fit_start = max(float(begin), float(available_start)) if available_start is not None else float(begin)
            fit_stop = min(float(end), float(available_stop)) if available_stop is not None else float(end)
            if fit_stop <= fit_start:
                continue
            fit_edges = _time_edges(np.empty(0), dt, fit_start, fit_stop)
            window_counts, _ = np.histogram(times, bins=fit_edges)
            fit_centers.append((fit_edges[:-1] + fit_edges[1:]) / 2)
            fit_counts.append(window_counts.astype(float))
        if not fit_centers:
            raise ValueError("Background intervals do not overlap the available event data")
        x = np.concatenate(fit_centers)
        background_counts = np.concatenate(fit_counts)
        if x.size < 3 or np.unique(x).size < 2:
            raise ValueError("Background fitting requires at least three bins in the selected intervals")
        y = background_counts / dt
        fit_error = np.sqrt(np.maximum(background_counts, 1.0)) / dt
        design = np.column_stack((x, np.ones(x.shape)))
        weights = 1.0 / np.square(fit_error)
        normal = design.T @ (weights[:, None] * design)
        try:
            covariance = np.linalg.inv(normal)
        except np.linalg.LinAlgError as exc:
            raise ValueError("Background intervals cannot constrain a linear fit") from exc
        coef = covariance @ (design.T @ (weights * y))
        full = np.column_stack((centers, np.ones(centers.shape)))
        bg = full @ coef
        bg_err = np.sqrt(np.maximum(0, np.einsum("ij,jk,ik->i", full, covariance, full)))
    net = raw - bg
    return np.column_stack((centers, raw, raw_err, bg, bg_err, net, np.sqrt(raw_err ** 2 + bg_err ** 2)))


def _tag(value: str) -> str:
    match = re.search(r"(GRB\d{6})\d{0,3}", str(value).upper())
    if match: return match.group(1).lower()
    return re.sub(r"[^A-Za-z0-9]+", "", str(value).lower()) or "events"


def _detector_tag(value: str) -> str:
    text = str(value).upper().strip()
    match = re.fullmatch(r"(?:NAI|BGO)[_ -]?(\d\d)", text)
    if match: return ("n" if text.startswith("NAI") else "b") + str(int(match.group(1)))
    match = re.fullmatch(r"(GRD|CPD)[_ -]?(\d+)([HL])?", text)
    if match: return f"{match.group(1).lower()}{int(match.group(2))}{(match.group(3) or '').lower()}"
    return re.sub(r"[^A-Za-z0-9]+", "", text.lower()) or "detector"


def estimate_outputs(
    input_paths: Sequence[str | Path], energy_bands="all", bin_widths=(0.1,), *,
    satellite="fermi", mode="both", t_start=None, t_stop=None, overlap=False,
    write_combined=True,
) -> dict:
    """Estimate output count and text size without reading event payloads."""
    satellite = str(satellite).lower().strip()
    paths = discover_event_files(input_paths)
    bands = parse_energy_bands(energy_bands)
    widths = [float(value) for value in bin_widths]
    if satellite not in {"fermi", "gecam"} or mode not in {"both", "lc", "evt"}:
        raise ValueError("Invalid satellite or output mode")
    if not widths or any(not np.isfinite(value) or value <= 0 for value in widths):
        raise ValueError("At least one positive bin width is required")

    streams = []
    sources = set()
    skipped = 0
    total_event_rows = 0
    for path in paths:
        try:
            with fits.open(path, memmap=True, lazy_load_hdus=True) as hdul:
                _validate_satellite(hdul, satellite)
                event_hdus = _find_event_hdus(hdul, satellite)
                if not event_hdus:
                    raise ValueError("No compatible event extension")
                primary = hdul[0].header
                source = _tag(str(primary.get("OBJECT", primary.get("SOURCE", path.stem))))
                sources.add(source)
                for hdu in event_hdus:
                    names = {str(name).upper() for name in (hdu.columns.names or [])}
                    rows = int(hdu.header.get("NAXIS2", 0) or 0)
                    duration = None
                    begin = hdu.header.get("TSTART", primary.get("TSTART"))
                    end = hdu.header.get("TSTOP", primary.get("TSTOP"))
                    if begin is not None and end is not None and float(end) > float(begin):
                        duration = float(end) - float(begin)
                    gain_streams = 2 if satellite == "gecam" and "GAIN_TYPE" in names else 1
                    streams.extend((rows, duration) for _ in range(gain_streams))
                    total_event_rows += rows
        except Exception:
            skipped += 1

    band_count = len(bands)
    stream_count = len(streams)
    lc_files = stream_count * band_count * len(widths) if mode in {"both", "lc"} else 0
    evt_files = stream_count * band_count if mode in {"both", "evt"} else 0
    combined_files = len(sources) * band_count * len(widths) if write_combined and mode in {"both", "lc"} else 0
    data_files = lc_files + evt_files + combined_files

    explicit_duration = None
    if t_start is not None and t_stop is not None and float(t_stop) > float(t_start):
        explicit_duration = float(t_stop) - float(t_start)
    lc_bytes = 0
    if mode in {"both", "lc"}:
        durations = [explicit_duration or duration or 100.0 for _, duration in streams]
        for duration in durations:
            lc_bytes += band_count * sum(max(1, math.ceil(duration / width)) * 100 for width in widths)
        if combined_files:
            average_duration = sum(durations) / max(len(durations), 1)
            lc_bytes += len(sources) * band_count * sum(max(1, math.ceil(average_duration / width)) * 100 for width in widths)
    evt_bytes = 0
    if mode in {"both", "evt"}:
        duplication = band_count if overlap else 1
        evt_bytes = total_event_rows * duplication * 105
    overhead = data_files * 512 + 4096
    return {
        "data_files": data_files, "total_files": data_files + 1,
        "estimated_bytes": int(lc_bytes + evt_bytes + overhead),
        "streams": stream_count, "sources": len(sources), "skipped": skipped,
    }


def process_files(
    input_paths: Sequence[str | Path], output_dir: str | Path, energy_bands="all", bin_widths=(0.1,),
    *, mode="both", use_gti=True, relative_time=True, t_start=None, t_stop=None,
    background_windows=(), overlap=False, write_combined=True,
    progress: Callable[[int, int, str], None] | None = None,
    satellite: str = "fermi", cancel_check: Callable[[], bool] | None = None,
) -> dict:
    satellite = satellite.lower().strip()
    if satellite not in {"fermi", "gecam"}:
        raise ValueError("Unsupported satellite; choose Fermi/GBM or GECAM")
    paths = discover_event_files(input_paths)
    bands = parse_energy_bands(energy_bands)
    widths = [float(v) for v in bin_widths]
    if not widths or any(v <= 0 or not np.isfinite(v) for v in widths): raise ValueError("At least one positive bin width is required")
    if mode not in {"both", "lc", "evt"}: raise ValueError("Invalid extraction mode")
    data: list[EventData] = []
    skipped: list[dict[str, str]] = []
    for path in paths:
        if cancel_check and cancel_check():
            raise ExtractionCancelled()
        try:
            parts = read_event_file_parts(path, use_gti=use_gti, satellite=satellite)
        except Exception as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
            if progress:
                progress(0, max(len(paths), 1), f"Skipped {path.name}: {exc}")
            continue
        data.extend(parts)
    root = Path(output_dir).expanduser().resolve(); root.mkdir(parents=True, exist_ok=True)
    if not data:
        manifest = {
            "input": [str(p) for p in paths], "output": str(root), "files": [],
            "mode": mode, "satellite": satellite, "energy_bands_keV": [[b.low, b.high] for b in bands],
            "bin_widths_s": widths, "relative_time_requested": relative_time,
            "absolute_time_streams": [], "use_gti": use_gti, "overlap": overlap,
            "write_combined": write_combined, "detectors": [], "skipped": skipped,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if progress:
            progress(len(paths), max(len(paths), 1), f"Completed: skipped all {len(skipped)} selected files")
        return manifest
    source_groups: dict[str, list[EventData]] = {}
    for item in data:
        source_groups.setdefault(_tag(item.object_name), []).append(item)
    per_item_steps = len(bands) * ((len(widths) if mode in {"both", "lc"} else 0) + (1 if mode in {"both", "evt"} else 0))
    combined_steps = len(source_groups) * len(bands) * len(widths) if write_combined and mode in {"both", "lc"} else 0
    total = len(data) * per_item_steps + combined_steps
    done = 0; files = []; seen_names = set()
    def report(message):
        nonlocal done
        done += 1
        if progress: progress(done, max(total, 1), message)

    def safe_lightcurve(times, width, start, stop, availability_times=None):
        # An empty energy selection is valid output; keep the configured time
        # window when available, otherwise skip it with a clear error.
        if times.size == 0 and (start is None or stop is None):
            if t_start is not None and t_stop is not None:
                start, stop = t_start, t_stop
            else:
                raise ValueError("Selected energy band contains no events; set both time start and time stop")
        availability_times = times if availability_times is None else availability_times
        available_start = float(np.min(availability_times)) if availability_times.size else None
        available_stop = float(np.max(availability_times)) if availability_times.size else None
        return make_lightcurve(
            times, width, start, stop, background_windows,
            available_start=available_start, available_stop=available_stop,
        )
    for source, source_items in source_groups.items():
        seen_names = set()
        for item in source_items:
            if cancel_check and cancel_check():
                raise ExtractionCancelled()
            lc_root = root / f"{source}lc"; evt_root = root / f"{source}evt"
            item_relative = bool(relative_time and item.has_trigger)
            times = item.time_rel if item_relative else item.time
            start = t_start; stop = t_stop
            if start is None and times.size: start = float(np.min(times))
            if stop is None and times.size: stop = float(np.max(times)) + min(widths)
            detector = _detector_tag(item.detector); stem = detector
            if stem in seen_names: stem = f"{detector}_{item.path.stem[:18]}"
            seen_names.add(stem)
            for band in bands:
                if cancel_check and cancel_check():
                    raise ExtractionCancelled()
                selected = _energy_mask(item, band, overlap)
                if mode in {"both", "lc"}:
                    for width in widths:
                        lc = safe_lightcurve(times[selected], width, start, stop, times)
                        path = lc_root / band.tag / f"{stem}_{band.tag}_{_number_tag(width * 1000)}ms_lc.txt"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        np.savetxt(path, lc[:, [0, 1, 2, 3, 5, 6]], fmt="%.10f", header="time counts_rate counts_rate_err bg net_counts_rate net_counts_rate_err", comments="# ")
                        files.append(str(path.relative_to(root))); report(f"LC {item.path.name} / {band.tag} / {width:g}s")
                if mode in {"both", "evt"}:
                    event_path = evt_root / band.tag / f"{stem}_{band.tag}_evt.txt"; event_path.parent.mkdir(parents=True, exist_ok=True)
                    indices = np.flatnonzero(selected)
                    if item_relative:
                        event_values = np.column_stack((times[indices], item.time[indices], item.pha[indices], item.energy_low[indices], item.energy_high[indices], item.energy[indices])) if len(indices) else np.empty((0, 6))
                        event_header = "time_rel_s time_s pha e_min_keV e_max_keV energy_center_keV"
                    else:
                        event_values = np.column_stack((times[indices], item.pha[indices], item.energy_low[indices], item.energy_high[indices], item.energy[indices])) if len(indices) else np.empty((0, 5))
                        event_header = "time_s pha e_min_keV e_max_keV energy_center_keV"
                    np.savetxt(event_path, event_values, fmt="%.10f", header=event_header, comments="# ")
                    files.append(str(event_path.relative_to(root))); report(f"EVT {item.path.name} / {band.tag}")
        if write_combined and mode in {"both", "lc"}:
            lc_root = root / f"{source}lc"
            for band in bands:
                selected_times = []
                available_times = []
                group_relative = bool(relative_time and all(item.has_trigger for item in source_items))
                for item in source_items:
                    event_times = item.time_rel if group_relative else item.time
                    available_times.append(event_times)
                    selected_times.append(event_times[_energy_mask(item, band, overlap)])
                combined_times = np.concatenate(selected_times) if selected_times else np.empty(0, dtype=float)
                combined_available = np.concatenate(available_times) if available_times else np.empty(0, dtype=float)
                start = t_start if t_start is not None else (float(np.min(combined_times)) if combined_times.size else None)
                stop = t_stop if t_stop is not None else (float(np.max(combined_times)) + min(widths) if combined_times.size else None)
                for width in widths:
                    if cancel_check and cancel_check():
                        raise ExtractionCancelled()
                    lc = safe_lightcurve(combined_times, width, start, stop, combined_available)
                    path = lc_root / band.tag / f"combined_{band.tag}_{_number_tag(width * 1000)}ms_lc.txt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    np.savetxt(path, lc[:, [0, 1, 2, 3, 5, 6]], fmt="%.10f", header="time counts_rate counts_rate_err bg net_counts_rate net_counts_rate_err", comments="# ")
                    files.append(str(path.relative_to(root))); report(f"LC combined {source} / {band.tag} / {width:g}s")
    manifest = {"input": [str(p) for p in paths], "output": str(root), "files": files, "mode": mode, "satellite": satellite, "energy_bands_keV": [[b.low, b.high] for b in bands], "bin_widths_s": widths, "relative_time_requested": relative_time, "absolute_time_streams": [f"{item.path.name}:{item.detector}" for item in data if relative_time and not item.has_trigger], "use_gti": use_gti, "overlap": overlap, "write_combined": write_combined, "background_windows_s": [[float(start), float(stop)] for start, stop in background_windows], "background_model": "weighted linear" if background_windows else "disabled", "detectors": sorted({item.detector for item in data}), "skipped": skipped}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if progress: progress(max(total, done), max(total, 1), f"Completed {len(files)} output files")
    return manifest

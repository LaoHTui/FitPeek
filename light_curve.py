from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from astropy.io import fits


class AnalysisCancelled(RuntimeError):
    pass


def compute_light_curve(path, config, cancelled=lambda: False, progress=lambda value, text: None):
    start = float(config["time_start"])
    end = float(config["time_end"])
    dt = float(config["dt"])
    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError("Time start must be smaller than time end")
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("DT must be positive")
    bin_count = int(math.floor((end - start) / dt + 1e-12))
    if bin_count < 1:
        raise ValueError("The selected time range is shorter than one DT")
    if bin_count > 2_000_000:
        raise ValueError("The selected time range and DT create more than 2,000,000 bins")
    effective_end = start + bin_count * dt
    excluded_tail = max(0.0, end - effective_end)

    progress(2, "Opening FITS file...")
    event_parts = []
    source_counts = {}
    with fits.open(path, mode="readonly", memmap=not str(path).lower().endswith(".gz")) as hdul:
        trigtime = next((hdu.header.get("TRIGTIME") for hdu in hdul if hdu.header.get("TRIGTIME") is not None), None)
        relative_time = bool(config.get("relative_time") and trigtime is not None)
        time_offset = float(trigtime) if relative_time else 0.0
        allowed_channels = _allowed_channels(hdul, config)
        gti_intervals = _gti_intervals(hdul, time_offset) if config.get("use_gti") else []
        indices = [int(index) for index in config["hdu_indices"]]
        metadata = _observation_metadata(hdul, indices, config)
        for position, index in enumerate(indices):
            if cancelled():
                raise AnalysisCancelled("Cancelled")
            if index < 0 or index >= len(hdul):
                continue
            hdu = hdul[index]
            data = getattr(hdu, "data", None)
            names = list(getattr(data, "names", []) or [])
            lookup = {name.upper(): name for name in names}
            if data is None or "TIME" not in lookup:
                continue
            time_values = np.asarray(data[lookup["TIME"]], dtype=np.float64) - time_offset
            mask = np.isfinite(time_values) & (time_values >= start) & (time_values < effective_end)

            channel_name = next((lookup[name] for name in ("PI", "PHA") if name in lookup), None)
            if config.get("apply_energy"):
                if "ENERGY" in lookup:
                    energy = np.asarray(data[lookup["ENERGY"]], dtype=np.float64)
                    mask &= (energy >= config["energy_low"]) & (energy <= config["energy_high"])
                elif channel_name and allowed_channels is not None:
                    mask &= np.isin(np.asarray(data[channel_name]), allowed_channels)

            filter_flag = config.get("filter_flag", config.get("require_flag_zero", False))
            filter_evt_type = config.get("filter_evt_type", config.get("require_evt_type_one", False))
            if filter_flag and "FLAG" in lookup:
                mask &= np.asarray(data[lookup["FLAG"]]) == int(config.get("flag_value", 0))
            if filter_evt_type and "EVT_TYPE" in lookup:
                mask &= np.asarray(data[lookup["EVT_TYPE"]]) == int(config.get("evt_type_value", 1))
            if gti_intervals:
                inside = np.zeros(time_values.shape, dtype=bool)
                for gti_start, gti_stop in gti_intervals:
                    inside |= (time_values >= gti_start) & (time_values < gti_stop)
                mask &= inside

            selected = np.asarray(time_values[mask], dtype=np.float64)
            event_parts.append(selected)
            source_counts[str(getattr(hdu, "name", index))] = int(selected.size)
            progress(5 + int(65 * (position + 1) / max(1, len(indices))), f"Filtering {hdu.name}...")

    if cancelled():
        raise AnalysisCancelled("Cancelled")
    events = np.sort(np.concatenate(event_parts)) if event_parts else np.empty(0, dtype=np.float64)
    edges = start + np.arange(bin_count + 1, dtype=np.float64) * dt
    counts, _ = np.histogram(events, bins=edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    count_error = np.sqrt(counts.astype(np.float64))
    rate = counts.astype(np.float64) / dt
    rate_error = count_error / dt
    progress(100, "Complete")
    return {
        "path": str(Path(path)),
        "events": events,
        "time_centers": centers,
        "counts": counts,
        "count_error": count_error,
        "rate": rate,
        "rate_error": rate_error,
        "source_counts": source_counts,
        "trigtime": trigtime,
        "relative_time": relative_time,
        "effective_time_start": start,
        "effective_time_end": effective_end,
        "excluded_tail": excluded_tail,
        "metadata": metadata,
        "config": dict(config),
    }


def _observation_metadata(hdul, indices, config):
    """Collect provenance for display/export without inventing missing values."""
    selected_hdus = []
    detector_names = []
    energy_units = []
    for index in indices:
        if index < 0 or index >= len(hdul):
            continue
        hdu = hdul[index]
        selected_hdus.append({"index": index, "name": str(getattr(hdu, "name", "") or f"HDU {index}")})
        detector = hdu.header.get("DETNAM") or hdu.header.get("DETECTOR")
        if detector not in (None, ""):
            detector_names.append(str(detector).strip())
        columns = getattr(hdu, "columns", None)
        if config.get("apply_energy") and columns is not None:
            lookup = {str(name).upper(): position for position, name in enumerate(columns.names or [])}
            if "ENERGY" in lookup:
                unit = getattr(columns[lookup["ENERGY"]], "unit", None)
                if unit:
                    energy_units.append(str(unit).strip())

    if config.get("apply_energy") and not energy_units:
        try:
            ebounds = hdul["EBOUNDS"]
            lookup = {str(name).upper(): position for position, name in enumerate(ebounds.columns.names or [])}
            for column_name in ("E_MIN", "E_MAX"):
                if column_name in lookup:
                    unit = getattr(ebounds.columns[lookup[column_name]], "unit", None)
                    if unit:
                        energy_units.append(str(unit).strip())
        except (KeyError, IndexError, AttributeError):
            pass

    global_detector = _first_header_value(hdul, "DETNAM", "DETECTOR")
    if not detector_names and global_detector not in (None, ""):
        detector_names.append(str(global_detector).strip())
    return {
        "object": _first_header_value(hdul, "OBJECT", "SRC_NAME"),
        "obs_id": _first_header_value(hdul, "OBS_ID", "OBSID"),
        "telescope": _first_header_value(hdul, "TELESCOP"),
        "instrument": _first_header_value(hdul, "INSTRUME"),
        "detectors": _unique_nonempty(detector_names),
        "selected_hdus": selected_hdus,
        "energy_unit": "/".join(_unique_nonempty(energy_units)),
        "time_system": _first_header_value(hdul, "TIMESYS"),
        "date_obs": _first_header_value(hdul, "DATE-OBS"),
    }


def _first_header_value(hdul, *keywords):
    for hdu in hdul:
        for keyword in keywords:
            value = hdu.header.get(keyword)
            if value not in (None, ""):
                return str(value).strip()
    return ""


def _unique_nonempty(values):
    return list(dict.fromkeys(value for value in values if value))


def _allowed_channels(hdul, config):
    if not config.get("apply_energy"):
        return None
    try:
        data = hdul["EBOUNDS"].data
    except (KeyError, IndexError):
        return None
    lookup = {name.upper(): name for name in list(getattr(data, "names", []) or [])}
    if not all(name in lookup for name in ("CHANNEL", "E_MIN", "E_MAX")):
        return None
    e_min = np.asarray(data[lookup["E_MIN"]], dtype=np.float64)
    e_max = np.asarray(data[lookup["E_MAX"]], dtype=np.float64)
    mask = (e_min >= config["energy_low"]) & (e_max <= config["energy_high"])
    return np.asarray(data[lookup["CHANNEL"]])[mask]


def _gti_intervals(hdul, time_offset):
    try:
        data = hdul["GTI"].data
    except (KeyError, IndexError):
        return []
    lookup = {name.upper(): name for name in list(getattr(data, "names", []) or [])}
    if "START" not in lookup or "STOP" not in lookup:
        return []
    starts = np.asarray(data[lookup["START"]], dtype=np.float64) - time_offset
    stops = np.asarray(data[lookup["STOP"]], dtype=np.float64) - time_offset
    return [(float(start), float(stop)) for start, stop in zip(starts, stops) if np.isfinite(start) and np.isfinite(stop) and start < stop]


def downsample_envelope(x_values, y_values, max_points=10000):
    x_values = np.asarray(x_values)
    y_values = np.asarray(y_values)
    if len(x_values) <= max_points:
        return x_values, y_values
    group_count = max_points // 2
    boundaries = np.linspace(0, len(x_values), group_count + 1, dtype=int)
    indices = []
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        if stop <= start:
            continue
        segment = y_values[start:stop]
        low = start + int(np.argmin(segment))
        high = start + int(np.argmax(segment))
        indices.extend(sorted((low, high)))
    indices = np.asarray(indices, dtype=int)
    return x_values[indices], y_values[indices]

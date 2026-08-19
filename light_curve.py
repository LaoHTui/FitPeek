from __future__ import annotations

import math
import hashlib
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import astropy
from astropy.io import fits

from app_info import APP_NAME, APP_VERSION


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

    paths = _normalise_paths(path, config)
    progress(2, "Opening FITS file...")
    event_parts = []
    fit_event_parts = []
    source_counts = {}
    resolved_background_windows = []
    sources = config.get("sources") or [{"path": str(current), "hdu_indices": config.get("hdu_indices", [])} for current in paths]
    metadata_parts = []
    trigtime = None
    relative_time = False
    total_sources = max(1, len(sources))
    for source_position, source in enumerate(sources):
        source_path = Path(source["path"])
        indices = [int(index) for index in source.get("hdu_indices", [])]
        with fits.open(source_path, mode="readonly", memmap=not str(source_path).lower().endswith(".gz")) as hdul:
            current_trigtime = next((hdu.header.get("TRIGTIME") for hdu in hdul if hdu.header.get("TRIGTIME") is not None), None)
            if trigtime is None:
                trigtime = current_trigtime
            if config.get("relative_time") and current_trigtime is None:
                raise ValueError(f"{source_path.name} has no TRIGTIME and cannot be combined in relative-time mode")
            relative_time = bool(config.get("relative_time") and current_trigtime is not None)
            time_offset = float(current_trigtime) if relative_time else 0.0
            allowed_channels = _allowed_channels(hdul, config)
            gti_intervals = _gti_intervals(hdul, time_offset) if config.get("use_gti") else []
            metadata_parts.append(_observation_metadata(hdul, indices, config, source_path))
            for index in indices:
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
                mask = np.isfinite(time_values)
                finite_times = np.asarray(time_values[mask], dtype=np.float64)
                if finite_times.size:
                    available_start = float(np.min(finite_times))
                    available_end = float(np.max(finite_times)) + np.finfo(np.float64).eps
                else:
                    available_start = start
                    available_end = effective_end

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

                if config.get("background_fit"):
                    configured_windows = _background_windows(config.get("background_windows", []))
                    if configured_windows:
                        source_windows = configured_windows
                    elif config.get("background_automatic", True):
                        source_windows = automatic_background_windows(available_start, available_end)
                    else:
                        source_windows = ()
                    resolved_background_windows.extend(source_windows)
                else:
                    source_windows = ()

                selected_mask = mask & (time_values >= start) & (time_values < effective_end)
                selected = np.asarray(time_values[selected_mask], dtype=np.float64)
                event_parts.append(selected)
                source_counts[f"{source_path.name}:{getattr(hdu, 'name', index)}"] = int(selected.size)
                if config.get("background_fit") and source_windows:
                    fit_mask = selected_mask.copy()
                    for window_start, window_end in source_windows:
                        fit_mask |= mask & (time_values >= window_start) & (time_values < window_end)
                    fit_event_parts.append(np.asarray(time_values[fit_mask], dtype=np.float64))
                progress(5 + int(65 * (source_position + 1) / total_sources), f"Filtering {source_path.name}:{hdu.name}...")

    if cancelled():
        raise AnalysisCancelled("Cancelled")
    events = np.sort(np.concatenate(event_parts)) if event_parts else np.empty(0, dtype=np.float64)
    edges = start + np.arange(bin_count + 1, dtype=np.float64) * dt
    counts, _ = np.histogram(events, bins=edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    count_error = np.sqrt(counts.astype(np.float64))
    rate = counts.astype(np.float64) / dt
    rate_error = count_error / dt
    progress(82, "Fitting background...")
    fit_config = dict(config)
    if config.get("background_fit"):
        fit_config["background_windows"] = _merge_background_windows(resolved_background_windows)
        fit_config["background_automatic"] = bool(config.get("background_automatic", not config.get("background_windows")))
        fit_config["_background_resolved"] = True
    fit_centers = centers
    fit_counts = counts
    if config.get("background_fit") and fit_event_parts:
        # Keep target bins for burst-tail diagnostics; the fit mask excludes
        # them because they do not belong to any background window.
        fit_centers_parts = [centers]
        fit_counts_parts = [counts]
        for window_start, window_end in _merge_background_windows(resolved_background_windows):
            for segment_start, segment_end in _outside_segments(window_start, window_end, start, effective_end):
                window_edges = _window_edges(segment_start, segment_end, dt)
                if len(window_edges) < 2:
                    continue
                window_events = np.concatenate([
                    values[(values >= segment_start) & (values < segment_end)] for values in fit_event_parts
                    if values.size
                ]) if fit_event_parts else np.empty(0, dtype=np.float64)
                window_counts, _ = np.histogram(window_events, bins=window_edges)
                fit_centers_parts.append(0.5 * (window_edges[:-1] + window_edges[1:]))
                fit_counts_parts.append(window_counts.astype(np.float64))
        if fit_centers_parts:
            fit_centers = np.concatenate(fit_centers_parts)
            fit_counts = np.concatenate(fit_counts_parts)
    fit_count_error = np.sqrt(fit_counts.astype(np.float64))
    background = fit_linear_background(fit_centers, fit_counts, fit_count_error, dt, fit_config)
    if fit_centers is not centers:
        background["rate"] = np.asarray(background["rate"][: len(centers)])
        background["rate_error"] = np.asarray(background["rate_error"][: len(centers)])
        background["net_rate"] = np.asarray(background["net_rate"][: len(centers)])
        background["net_rate_error"] = np.asarray(background["net_rate_error"][: len(centers)])
    progress(100, "Complete")
    return {
        "path": str(Path(paths[0])) if len(paths) == 1 else os_path_list(paths),
        "paths": [str(current) for current in paths],
        "events": events,
        "time_centers": centers,
        "counts": counts,
        "count_error": count_error,
        "rate": rate,
        "rate_error": rate_error,
        "background_rate": background["rate"],
        "background_rate_error": background["rate_error"],
        "net_rate": background["net_rate"],
        "net_rate_error": background["net_rate_error"],
        "background_fit": background["fit"],
        "source_counts": source_counts,
        "trigtime": trigtime,
        "relative_time": relative_time,
        "effective_time_start": start,
        "effective_time_end": effective_end,
        "excluded_tail": excluded_tail,
        "metadata": _merge_metadata(metadata_parts, paths),
        "config": dict(config),
        "provenance": {
            "software": APP_NAME,
            "software_version": APP_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "numpy_version": np.__version__,
            "astropy_version": astropy.__version__,
            "byte_order": sys.byteorder,
            "source_sha256": {str(current): _sha256(current) for current in paths},
        },
    }


def fit_linear_background(time_centers, counts, count_error, dt, config):
    """Fit a weighted linear rate model over one or more off-source windows."""
    time_centers = np.asarray(time_centers, dtype=np.float64)
    counts = np.asarray(counts, dtype=np.float64)
    count_error = np.asarray(count_error, dtype=np.float64)
    nan_values = np.full(time_centers.shape, np.nan, dtype=np.float64)
    enabled = bool(config.get("background_fit", False))
    if not enabled:
        return {
            "rate": nan_values.copy(),
            "rate_error": nan_values.copy(),
            "net_rate": nan_values.copy(),
            "net_rate_error": nan_values.copy(),
            "fit": {
                "enabled": False,
                "performed": False,
                "model": "linear",
                "windows_s": [],
                "coefficients": [float("nan"), float("nan")],
                "covariance": [[float("nan"), float("nan")], [float("nan"), float("nan")]],
                "n_fit_bins": 0,
            },
        }

    cached = config.get("background_cached_fit")
    if cached and cached.get("performed"):
        coefficients = np.asarray(cached.get("coefficients", []), dtype=np.float64)
        covariance = np.asarray(cached.get("covariance", []), dtype=np.float64)
        if coefficients.shape == (2,) and covariance.shape == (2, 2):
            raw_rate = counts / float(dt)
            raw_error = count_error / float(dt)
            background_rate = np.polyval(coefficients, time_centers)
            background_variance = (
                time_centers ** 2 * covariance[0, 0]
                + covariance[1, 1]
                + 2.0 * time_centers * covariance[0, 1]
            )
            background_error = np.sqrt(np.maximum(background_variance, 0.0))
            fit = dict(cached)
            fit["reused"] = True
            return {
                "rate": background_rate,
                "rate_error": background_error,
                "net_rate": raw_rate - background_rate,
                "net_rate_error": np.sqrt(raw_error ** 2 + background_error ** 2),
                "fit": fit,
            }

    windows = _background_windows(config.get("background_windows", []))
    automatic = bool(config.get("background_automatic", not windows))
    if automatic:
        if not windows:
            windows = automatic_background_windows(float(config["time_start"]), float(config["time_end"]))
    raw_rate = counts / float(dt)
    raw_error = count_error / float(dt)
    fit_mask = _background_fit_mask(time_centers, raw_rate, raw_error, windows)
    if automatic and not config.get("_background_resolved") and np.count_nonzero(fit_mask) < 3:
        for fraction in (0.3, 0.4, 0.5):
            windows = automatic_background_windows(
                float(config["time_start"]), float(config["time_end"]), fraction=fraction,
            )
            fit_mask = _background_fit_mask(time_centers, raw_rate, raw_error, windows)
            if np.count_nonzero(fit_mask) >= 3:
                break
    fit_error = raw_error
    weighting = "Poisson sqrt(N), non-empty bins"
    warnings = []
    if np.count_nonzero(fit_mask) < 3:
        fit_mask = _background_window_mask(time_centers, windows)
        fit_mask &= np.isfinite(raw_rate)
        fit_error = np.sqrt(np.maximum(counts, 1.0)) / float(dt)
        weighting = "low-count fallback sqrt(max(N, 1)), including empty bins"
    if automatic:
        window_mask = _background_window_mask(time_centers, windows)
        center_mask = ~window_mask & np.isfinite(raw_rate)
        center_rates = raw_rate[center_mask]
        if center_rates.size >= 3:
            center_level = float(np.median(center_rates))
            center_mad = float(np.median(np.abs(center_rates - center_level)))
            center_scale = max(1.4826 * center_mad, np.sqrt(max(center_level, 0.0) / max(float(dt), 1e-12)))
            threshold = center_level + max(3.0 * center_scale, 0.5 * max(abs(center_level), 1.0 / max(float(dt), 1e-12)))
            suspicious = []
            for window_start, window_end in windows:
                window_mask = (time_centers >= window_start) & (time_centers < window_end) & np.isfinite(raw_rate)
                if np.any(window_mask) and float(np.median(raw_rate[window_mask])) > threshold:
                    suspicious.append([float(window_start), float(window_end)])
            if suspicious:
                warnings.append(
                    "Default edge background interval may include burst-tail emission: "
                    + str(suspicious)
                )
    n_fit_bins = int(np.count_nonzero(fit_mask))
    if n_fit_bins < 3:
        raise ValueError(
            "Background fitting requires at least three non-empty bins across the selected intervals"
        )
    coefficients, covariance = np.polyfit(
        time_centers[fit_mask],
        raw_rate[fit_mask],
        deg=1,
        w=1.0 / fit_error[fit_mask],
        cov=True,
    )
    background_rate = np.polyval(coefficients, time_centers)
    background_variance = (
        time_centers ** 2 * covariance[0, 0]
        + covariance[1, 1]
        + 2.0 * time_centers * covariance[0, 1]
    )
    background_error = np.sqrt(np.maximum(background_variance, 0.0))
    return {
        "rate": background_rate,
        "rate_error": background_error,
        "net_rate": raw_rate - background_rate,
        "net_rate_error": np.sqrt(raw_error ** 2 + background_error ** 2),
        "fit": {
            "enabled": True,
            "performed": True,
            "model": "weighted linear: rate(t) = a*t + b",
            "windows_s": [[float(start), float(end)] for start, end in windows],
            "coefficients": [float(value) for value in coefficients],
            "covariance": [[float(value) for value in row] for row in covariance],
            "n_fit_bins": n_fit_bins,
            "weighting": weighting,
            "warnings": warnings,
            "reused": False,
        },
    }


def automatic_background_windows(start, end, fraction=0.2, available_start=None, available_end=None):
    start = float(start)
    end = float(end)
    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError("Cannot create background intervals from an invalid time range")
    width = (end - start) * float(fraction)
    if available_start is None or available_end is None:
        return ((start, start + width), (end - width, end))
    available_start = float(available_start)
    available_end = float(available_end)
    if not math.isfinite(available_start) or not math.isfinite(available_end) or available_start >= available_end:
        return ((start, start + width), (end - width, end))
    windows = []
    if available_start < start:
        windows.append((available_start, min(start, available_start + width)))
    if available_end > end:
        windows.append((max(end, available_end - width), available_end))
    if not windows:
        return ((start, start + width), (end - width, end))
    if len(windows) < 2:
        # There is no off-source data on one side; retain the available side
        # and use the corresponding in-range edge as a deterministic fallback.
        fallback = (start, start + width) if not windows or windows[0][0] >= start else (end - width, end)
        windows.append(fallback)
    return tuple((float(left), float(right)) for left, right in windows if right > left)


def _window_edges(start, end, dt):
    count = int(math.floor((float(end) - float(start)) / float(dt) + 1e-12))
    if count < 1:
        return np.array([float(start), float(end)], dtype=np.float64)
    return float(start) + np.arange(count + 1, dtype=np.float64) * float(dt)


def _outside_segments(window_start, window_end, selected_start, selected_end):
    segments = []
    if window_start < selected_start:
        segments.append((window_start, min(window_end, selected_start)))
    if window_end > selected_end:
        segments.append((max(window_start, selected_end), window_end))
    return tuple((start, end) for start, end in segments if end > start)


def _merge_background_windows(windows):
    ordered = sorted(_background_windows(windows), key=lambda value: (value[0], value[1]))
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _background_fit_mask(time_centers, raw_rate, raw_error, windows):
    mask = _background_window_mask(time_centers, windows)
    mask &= np.isfinite(raw_rate) & np.isfinite(raw_error) & (raw_error > 0.0)
    return mask


def _background_window_mask(time_centers, windows):
    mask = np.zeros(time_centers.shape, dtype=bool)
    for window_start, window_end in windows:
        mask |= (time_centers >= window_start) & (time_centers < window_end)
    return mask


def _background_windows(values):
    windows = []
    for value in values or []:
        if len(value) != 2:
            raise ValueError("Each background interval must contain a start and end time")
        start, end = (float(value[0]), float(value[1]))
        if not math.isfinite(start) or not math.isfinite(end) or start >= end:
            raise ValueError(f"Invalid background interval: {value}")
        windows.append((start, end))
    return tuple(windows)


def _observation_metadata(hdul, indices, config, source_path=None):
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
        "source": str(source_path) if source_path else "",
    }


def _normalise_paths(path, config):
    values = config.get("paths") or path
    if isinstance(values, (str, Path)):
        values = [values]
    result = [Path(value) for value in values]
    if not result:
        raise ValueError("No FITS source selected")
    return result


def os_path_list(paths):
    return ";".join(str(path) for path in paths)


def _merge_metadata(parts, paths):
    if not parts:
        return {"sources": [str(path) for path in paths], "detectors": []}
    merged = dict(parts[0])
    merged["sources"] = [str(path) for path in paths]
    for key in ("detectors", "selected_hdus"):
        merged[key] = []
        for part in parts:
            merged[key].extend(part.get(key, []))
        if key == "detectors":
            merged[key] = _unique_nonempty(merged[key])
    for key in ("object", "obs_id", "telescope", "instrument", "time_system", "date_obs", "energy_unit"):
        values = _unique_nonempty([part.get(key, "") for part in parts])
        merged[key] = values[0] if len(values) == 1 else "/".join(values)
    return merged


def _sha256(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return "unavailable"


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

"""Targeted Fermi/GECAM and cancellation smoke tests for Extractor."""

from __future__ import annotations

import gzip
import shutil
import tempfile
from pathlib import Path

import numpy as np
from astropy.io import fits

from extractor import (
    ExtractionCancelled, _energy_mask, estimate_outputs, parse_energy_bands,
    process_files, read_event_file, read_event_file_parts,
)


def _ebounds():
    return fits.BinTableHDU.from_columns([
        fits.Column(name="CHANNEL", format="I", array=np.arange(4, dtype=np.int16)),
        fits.Column(name="E_MIN", format="E", unit="keV", array=np.array([2, 4, 6, 8], dtype=np.float32)),
        fits.Column(name="E_MAX", format="E", unit="keV", array=np.array([4, 6, 8, 10], dtype=np.float32)),
    ], name="EBOUNDS")


def _gti():
    return fits.BinTableHDU.from_columns([
        fits.Column(name="START", format="D", array=np.array([1000.0])),
        fits.Column(name="STOP", format="D", array=np.array([1001.1])),
    ], name="GTI")


def _fermi(path):
    primary = fits.PrimaryHDU()
    primary.header["TELESCOP"] = "GLAST"
    primary.header["INSTRUME"] = "GBM"
    primary.header["DETNAM"] = "NAI_00"
    primary.header["OBJECT"] = "GRB240101001"
    primary.header["TRIGTIME"] = 1000.25
    events = fits.BinTableHDU.from_columns([
        fits.Column(name="TIME", format="D", array=np.linspace(1000, 1001, 12)),
        fits.Column(name="PHA", format="I", array=np.resize(np.arange(4, dtype=np.int16), 12)),
    ], name="EVENTS")
    fits.HDUList([primary, _ebounds(), _gti(), events]).writeto(path, overwrite=True)
    return path


def _gecam(path):
    primary = fits.PrimaryHDU()
    primary.header["TELESCOP"] = "GECAM-B"
    primary.header["INSTRUME"] = "GRD"
    primary.header["DATATYPE"] = "EVT"
    primary.header["BURST_ID"] = "bn240101001"
    primary.header["BST_TIME"] = 1000.25
    columns = [
        fits.Column(name="TIME", format="D", array=np.linspace(1000, 1001, 12)),
        fits.Column(name="PI", format="I", array=np.resize(np.arange(4, dtype=np.int16), 12)),
        fits.Column(name="GAIN_TYPE", format="B", array=np.resize(np.array([0, 1], dtype=np.uint8), 12)),
        fits.Column(name="DEAD_TIME", format="E", array=np.zeros(12, dtype=np.float32)),
        fits.Column(name="EVT_TYPE", format="B", array=np.ones(12, dtype=np.uint8)),
        fits.Column(name="FLAG", format="B", array=np.array([0] * 10 + [10, 11], dtype=np.uint8)),
    ]
    fits.HDUList([primary, _ebounds(), _gti(),
                  fits.BinTableHDU.from_columns(columns, name="EVENTS01"),
                  fits.BinTableHDU.from_columns(columns, name="EVENTS04")]).writeto(path, overwrite=True)
    return path


def _other(path):
    primary = fits.PrimaryHDU()
    primary.header["TELESCOP"] = "OTHER"
    events = fits.BinTableHDU.from_columns([
        fits.Column(name="TIME", format="D", array=np.array([1.0, 2.0])),
        fits.Column(name="ENERGY", format="E", array=np.array([5.0, 6.0], dtype=np.float32)),
    ], name="EVENTS")
    fits.HDUList([primary, events]).writeto(path, overwrite=True)
    return path


def _fermi_background(path):
    primary = fits.PrimaryHDU()
    primary.header["TELESCOP"] = "GLAST"
    primary.header["INSTRUME"] = "GBM"
    primary.header["DETNAM"] = "NAI_01"
    primary.header["OBJECT"] = "BACKGROUND-TEST"
    primary.header["TRIGTIME"] = 1000.0
    times = np.arange(940.0, 1100.0, 0.5)
    events = fits.BinTableHDU.from_columns([
        fits.Column(name="TIME", format="D", array=times),
        fits.Column(name="PHA", format="I", array=np.zeros(times.size, dtype=np.int16)),
    ], name="EVENTS")
    fits.HDUList([primary, _ebounds(), events]).writeto(path, overwrite=True)
    return path


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fermi, gecam, other = _fermi(root / "fermi.fits"), _gecam(root / "gecam.fits"), _other(root / "other.fits")

        fermi_result = process_files([fermi], root / "fermi-output", satellite="fermi",
                                     energy_bands="2-10", bin_widths=[0.1], t_start=-0.3, t_stop=0.8)
        assert fermi_result["detectors"] == ["NAI_00"] and len(fermi_result["files"]) == 3

        parts = read_event_file_parts(gecam, satellite="gecam")
        assert {part.detector for part in parts} == {"GRD01H", "GRD01L", "GRD04H", "GRD04L"}
        assert all(part.has_trigger for part in parts) and sum(part.time.size for part in parts) == 20
        gecam_result = process_files([gecam], root / "gecam-output", satellite="gecam",
                                    energy_bands="2-10", bin_widths=[0.1], t_start=-0.3, t_stop=0.8,
                                    mode="evt", write_combined=False)
        assert len(gecam_result["files"]) == 4
        assert all("grd01" not in name for name in gecam_result["files"])
        assert any("grd1l_" in name for name in gecam_result["files"])
        estimate = estimate_outputs([gecam], "2-10", [0.1], satellite="gecam",
                                    mode="evt", write_combined=False)
        assert estimate["data_files"] == len(gecam_result["files"]) == 4
        assert estimate["total_files"] == 5 and estimate["estimated_bytes"] > 0

        # Background windows may sit outside the exported time range; the fit
        # must still use those events and write a real, non-zero model.
        background_file = _fermi_background(root / "background.fits")
        background_result = process_files(
            [background_file], root / "background-output", satellite="fermi",
            energy_bands="all", bin_widths=[1.0], mode="lc",
            t_start=-10, t_stop=20, background_windows=[(-50, -20), (70, 95)],
            write_combined=False,
        )
        background_lc = np.loadtxt(root / "background-output" / background_result["files"][0])
        assert np.all(background_lc[:, 3] > 0)
        assert np.allclose(background_lc[:, 1] - background_lc[:, 3], background_lc[:, 4])

        # With channel-overlap disabled, a requested band can legitimately
        # contain no events in the background windows. That is a valid zero
        # background model, not a batch-ending extraction error.
        zero_background_result = process_files(
            [background_file], root / "zero-background-output", satellite="fermi",
            energy_bands="8-10", bin_widths=[1.0], mode="lc",
            t_start=-10, t_stop=20, background_windows=[(-50, -20), (70, 95)],
            overlap=False, write_combined=False,
        )
        zero_background_lc = np.loadtxt(
            root / "zero-background-output" / zero_background_result["files"][0]
        )
        assert np.all(zero_background_lc[:, 1] == 0)
        assert np.all(zero_background_lc[:, 3] == 0)
        assert np.all(zero_background_lc[:, 4] == 0)

        mixed = process_files([fermi, gecam, other], root / "mixed-output", satellite="fermi",
                              energy_bands="all", bin_widths=[0.2], mode="evt", write_combined=False)
        assert len(mixed["files"]) == 1
        assert {Path(item["path"]).name for item in mixed["skipped"]} == {"gecam.fits", "other.fits"}

        all_skipped = process_files([gecam, other], root / "all-skipped-output", satellite="fermi")
        assert all_skipped["files"] == [] and len(all_skipped["skipped"]) == 2

        compressed = root / "fermi.fits.gz"
        with fermi.open("rb") as source, gzip.open(compressed, "wb") as target:
            shutil.copyfileobj(source, target)
        assert read_event_file(compressed, satellite="fermi").time.size == 12

        # A finite energy band must not silently accept every event when none
        # of the PHA values can be mapped through EBOUNDS.
        unmapped = _fermi(root / "unmapped.fits")
        with fits.open(unmapped, mode="update") as hdul:
            hdul[3].data["PHA"][:] = 99
        unmapped_data = read_event_file(unmapped, satellite="fermi")
        assert not unmapped_data.has_energy
        assert not np.any(_energy_mask(unmapped_data, parse_energy_bands("2-10")[0]))
        assert np.all(_energy_mask(unmapped_data, parse_energy_bands("all")[0]))

        try:
            process_files([fermi], root / "cancelled-output", satellite="fermi", cancel_check=lambda: True)
        except ExtractionCancelled:
            pass
        else:
            raise AssertionError("Expected cooperative cancellation")

    print("extractor smoke test passed")


if __name__ == "__main__":
    main()

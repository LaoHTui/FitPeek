import gzip
import hashlib
import shutil
import tempfile
from pathlib import Path

import numpy as np
from astropy.io import fits

from fits_reader import FITSReader
from light_curve import compute_light_curve


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_sample(out):
    out = Path(out)
    primary = fits.PrimaryHDU()
    primary.header["TELESCOP"] = "FITPEEK-TEST"
    primary.header["TRIGTIME"] = 1000.25
    ebounds = fits.BinTableHDU.from_columns([
        fits.Column(name="CHANNEL", format="I", array=np.arange(4, dtype=np.int16)),
        fits.Column(name="E_MIN", format="E", unit="keV", array=np.array([2, 4, 6, 8], dtype=np.float32)),
        fits.Column(name="E_MAX", format="E", unit="keV", array=np.array([4, 6, 8, 10], dtype=np.float32)),
    ], name="EBOUNDS")
    gti = fits.BinTableHDU.from_columns([
        fits.Column(name="START", format="D", unit="s", array=np.array([1000.0])),
        fits.Column(name="STOP", format="D", unit="s", array=np.array([1001.1])),
    ], name="GTI")
    events = fits.BinTableHDU.from_columns([
        fits.Column(name="TIME", format="D", unit="s", array=np.linspace(1000, 1001, 250)),
        fits.Column(name="PI", format="I", array=np.arange(250, dtype=np.int16)),
        fits.Column(name="FLAG", format="B", array=np.zeros(250, dtype=np.uint8)),
        fits.Column(name="EVT_TYPE", format="B", array=np.ones(250, dtype=np.uint8)),
    ], name="EVENTS")
    fits.HDUList([primary, ebounds, gti, events]).writeto(out, overwrite=True)
    return out


def main():
    with tempfile.TemporaryDirectory() as test_dir:
        out = create_sample(Path(test_dir) / "test_sample.fits")
        before = digest(out)
        with FITSReader.open(out) as reader:
            assert reader.hdu_count == 4
            assert reader.infos[3].rows == 250
            assert [f.name for f in reader.table_schema(3)] == ["TIME", "PI", "FLAG", "EVT_TYPE"]
            assert len(reader.read_table_rows(3, 0, 100)) == 100
            assert reader.read_table_rows(3, 249, 100)[0][1] == 249
        config = {
            "hdu_indices": [3],
            "time_start": -0.25,
            "time_end": 0.85,
            "dt": 0.01,
            "relative_time": True,
            "use_gti": True,
            "filter_flag": True,
            "flag_value": 0,
            "filter_evt_type": True,
            "evt_type_value": 1,
            "apply_energy": True,
            "energy_low": 2.0,
            "energy_high": 10.0,
        }
        result = compute_light_curve(out, config)
        assert len(result["events"]) == 4
        assert int(result["counts"].sum()) == 4
        assert len(result["counts"]) == 110
        rejected = dict(config, flag_value=1)
        assert len(compute_light_curve(out, rejected)["events"]) == 0
        partial = dict(
            config,
            time_end=0.745,
            dt=0.1,
            apply_energy=False,
        )
        partial_result = compute_light_curve(out, partial)
        assert len(partial_result["counts"]) == 9
        assert 0.09 < partial_result["excluded_tail"] < 0.1
        assert partial_result["counts"][-1] >= 0.8 * np.median(partial_result["counts"])
        assert digest(out) == before
        gz = Path(test_dir) / "test_sample.fits.gz"
        with out.open("rb") as source, gzip.open(gz, "wb") as target:
            shutil.copyfileobj(source, target)
        with FITSReader.open(gz) as reader:
            assert reader.hdu_count == 4
            assert len(reader.read_table_rows(3, 150, 100)) == 100
    print("smoke test passed")


if __name__ == "__main__":
    main()

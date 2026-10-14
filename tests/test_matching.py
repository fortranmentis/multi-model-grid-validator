"""Synthetic regression checks; no repository model data are needed."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
from netCDF4 import Dataset

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "지점_격자_자동매칭.py"
spec = importlib.util.spec_from_file_location("grid_matching", SCRIPT)
matching = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matching)


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def model(self, values, coordinates_2d=False):
        path = self.folder / "synthetic.nc"
        with Dataset(path, "w") as ds:
            ds.createDimension("time", len(values))
            ds.createDimension("y", 1)
            ds.createDimension("x", 2)
            ds.createVariable("time", "f8", ("time",))[:] = np.arange(len(values))
            dims = ("y", "x") if coordinates_2d else ("y",)
            ds.createVariable("lat", "f8", dims)[:] = [[0, 0]] if coordinates_2d else [0]
            dims = ("y", "x") if coordinates_2d else ("x",)
            ds.createVariable("lon", "f8", dims)[:] = [[0, 0.1]] if coordinates_2d else [0, 0.1]
            ds.createVariable("value", "f8", ("time", "y", "x"))[:] = values
        return {"file": str(path), "lat": "lat", "lon": "lon", "var": "value", "time": "time"}

    def process(self, info, name="TEST"):
        return matching.process_model((name, info, {"lon": [0.0], "lat": [0.0]}))

    def test_haversine_known_distances(self):
        self.assertAlmostEqual(float(matching.haversine(0, 0, 0, 0)), 0)
        self.assertAlmostEqual(float(matching.haversine(0, 0, 1, 0)), 111.1949, places=3)

    def test_radius_and_invalid_nearest(self):
        lat = np.array([[0.0, 0.0, 0.0]])
        lon = np.array([[0.0, 0.1, 1.0]])
        valid = np.array([[False, True, True]])
        self.assertEqual(matching.find_closest_valid_within_radius(lat, lon, valid, [0, 0]), (0, 1))
        self.assertIsNone(matching.find_closest_valid_within_radius(lat, lon, valid, [0, 0], 1))
        self.assertIsNone(matching.find_closest_valid_within_radius(lat, lon, np.zeros_like(valid), [0, 0]))

    def test_values_must_pass_every_available_time(self):
        info = self.model(np.array([[[2, 5]], [[0, 6]]], dtype=float))
        name, frame, error = self.process(info)
        self.assertEqual(name, "TEST")
        self.assertIsNone(error)
        self.assertEqual(frame.loc[0, "TEST_X"], 1)
        self.assertEqual(frame.loc[0, "TEST_Y"], 0)
        self.assertEqual(frame.loc[0, "result_TEST"], 5)

    def test_only_first_twenty_times_are_checked(self):
        values = np.ones((21, 1, 2)) * 3
        values[20, 0, 0] = 0
        _, frame, error = self.process(self.model(values))
        self.assertIsNone(error)
        self.assertEqual(frame.loc[0, "TEST_X"], 0)

    def test_two_dimensional_coordinates(self):
        _, frame, error = self.process(self.model(np.array([[[0, 7]]]), True))
        self.assertIsNone(error)
        self.assertEqual(frame.loc[0, "TEST_X"], 1)

    def test_wrf_skips_value_validation(self):
        info = self.model(np.zeros((2, 1, 2)))
        info["var"] = "variable_not_present"
        info["time"] = "time_not_present"
        _, frame, error = self.process(info, "WRF")
        self.assertIsNone(error)
        self.assertEqual(frame.loc[0, "WRF_X"], 0)
        self.assertNotIn("result_WRF", frame.columns)

    def test_no_valid_candidates_produces_nan(self):
        _, frame, error = self.process(self.model(np.zeros((2, 1, 2))))
        self.assertIsNone(error)
        self.assertTrue(frame[["TEST_X", "TEST_Y", "result_TEST"]].isna().all().all())

    def test_missing_and_unreadable_models_return_errors(self):
        info = {"file": str(self.folder / "missing.nc")}
        _, frame, error = self.process(info)
        self.assertIsNone(frame)
        self.assertIn("파일 없음", error)
        path = self.folder / "not_netcdf.nc"
        path.write_text("invalid NetCDF", encoding="utf-8")
        _, frame, error = self.process({"file": str(path)})
        self.assertIsNone(frame)
        self.assertIn("파일 열기 실패", error)

    def test_unsupported_variable_shape_returns_error(self):
        info = self.model(np.ones((2, 1, 2)))
        info["var"] = "lat"
        _, frame, error = self.process(info)
        self.assertIsNone(frame)
        self.assertIn("처리 실패", error)

    def test_current_value_policy_and_infinity_limitation(self):
        values = np.array([1.0, 0.0, -1.0, np.nan, np.inf, -np.inf])
        np.testing.assert_array_equal(matching.is_normal_value(values), [True, False, False, False, True, False])

    def test_main_with_all_models_missing(self):
        script = self.folder / SCRIPT.name
        shutil.copyfile(SCRIPT, script)
        points = pd.DataFrame({"lon": [129.0, 130.0], "lat": [35.0, 36.0]})
        points.to_excel(self.folder / "포인트.xlsx", index=False)
        # Isolated folder contains no model directory and cannot read real models.
        result = subprocess.run([sys.executable, str(script)], cwd=self.folder,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        actual = pd.read_csv(self.folder / "포인트지정.csv", encoding="euc-kr")
        # Excel/CSV may infer integral coordinates as int; preserve values/order.
        pd.testing.assert_frame_equal(actual, points, check_dtype=False)
        self.assertIn("WRFdm2", result.stdout.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()

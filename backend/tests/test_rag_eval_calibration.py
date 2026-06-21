import math
from scripts.rag_eval.calibration import calibrate, cohen_kappa


def test_kappa_perfect_and_chance():
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    # 完全相反 → kappa 为负。
    assert cohen_kappa([1, 1, 0, 0], [0, 0, 1, 1]) < 0
    assert math.isnan(cohen_kappa([], []))


def test_calibrate_reports():
    out = calibrate([1, 0, 1, 1], [1, 0, 0, 1])
    assert out["n"] == 4 and out["agreement"] == 0.75
    assert -1.0 <= out["kappa"] <= 1.0

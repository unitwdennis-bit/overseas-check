import pytest

import overseas


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """測試一律不准真的起 claude（一趟要錢又要十幾分鐘）。要測 run_one 的自己再蓋掉 Popen。
    09-28 就發生過：替身沒生效，單元測試真的叫起 Opus 5 跑了幾分鐘。"""
    def refuse(*args, **kwargs):
        raise AssertionError("測試裡不准真的啟動 claude，請先換掉 subprocess.Popen 或 run_one")
    monkeypatch.setattr(overseas.subprocess, "Popen", refuse)

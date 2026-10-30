#!/usr/bin/env python3
"""Mocked tests for run_guarded.run_case: validates the complete dispatch path
(weather identity → resource guard → launch → record) without launching E+."""
import os, sys, tempfile, shutil, hashlib, json
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Import WITHOUT executing (verify: the module-level code only defines functions/constants)
import run_guarded

def _make_epw(path, content=None):
    if content is None:
        content = "test weather data\n" * 100
    with open(path, "w") as f: f.write(content)
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def _make_case_dir(path):
    os.makedirs(path, exist_ok=True)
    idf = os.path.join(path, "test.idf")
    with open(idf, "w") as f: f.write("Version,9.6;\n")
    return path, idf

def _mock_guard_pass():
    """Return a mock that always passes the guard."""
    return {"decision": "PASS", "refusals": [], "failures": []}, True

def _mock_launch(cmd, **kwargs):
    """Mock subprocess.run that creates minimal E+ output files."""
    out_dir = cmd[cmd.index("-d") + 1]
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "eplusout.end"), "w") as f:
        f.write("EnergyPlus Completed Successfully--- 1 Warning; 0 Severe;\n")
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = ""
    mock.stderr = ""
    return mock

class TestRunCase:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.case_dir, self.idf_path = _make_case_dir(os.path.join(self.tmp, "case"))
        self.epw_path = os.path.join(self.tmp, "weather.epw")
        self.epw_sha = _make_epw(self.epw_path)
        self._guard_patch = patch.object(run_guarded, "guard", return_value=_mock_guard_pass())
        # Create a fake engine binary so sha256_head(EPLUS) works
        self.fake_engine = os.path.join(self.tmp, "fake_energyplus")
        with open(self.fake_engine, "w") as f: f.write("#!/bin/sh\necho 'mock E+'\n")
        self._engine_patch = patch.object(run_guarded, "EPLUS", self.fake_engine)

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def _run(self, epw=None, sha=None, launch=None):
        lp = launch or _mock_launch
        with self._guard_patch, self._engine_patch:
            run_guarded.run_case(self.case_dir, self.idf_path, "test",
                                 epw_path=epw, epw_sha=sha, _launch=lp)

    def test_correct_dispatch(self):
        """Correct weather + SHA → launch invoked with exact path, record matches."""
        launch_calls = []
        def track_launch(cmd, **kw):
            launch_calls.append(cmd)
            return _mock_launch(cmd, **kw)
        self._run(epw=self.epw_path, sha=self.epw_sha, launch=track_launch)
        assert len(launch_calls) == 1
        assert launch_calls[0][launch_calls[0].index("-w") + 1] == self.epw_path
        rec = json.load(open(os.path.join(self.case_dir, "RUN_RECORD.json")))
        assert rec["weather_sha256_full"] == self.epw_sha
        assert rec["weather_path"] == self.epw_path

    def test_missing_weather_refuses(self):
        try:
            self._run(epw=None, sha=self.epw_sha)
            raise AssertionError("should refuse")
        except SystemExit as e:
            assert "not set" in str(e) or "REFUSE" in str(e)

    def test_missing_sha_refuses(self):
        try:
            self._run(epw=self.epw_path, sha=None)
            raise AssertionError("should refuse")
        except SystemExit as e:
            assert "REFUSE" in str(e)

    def test_wrong_sha_refuses(self):
        wrong = "0" * 64
        try:
            self._run(epw=self.epw_path, sha=wrong)
            raise AssertionError("should refuse")
        except SystemExit as e:
            assert "mismatch" in str(e)

    def test_shortened_prefix_sha_refuses(self):
        short = self.epw_sha[:16]
        try:
            self._run(epw=self.epw_path, sha=short)
            raise AssertionError("should refuse shortened SHA")
        except SystemExit as e:
            assert "64 hex chars" in str(e)

    def test_nonexistent_weather_refuses(self):
        fake = os.path.join(self.tmp, "nonexistent.epw")
        try:
            self._run(epw=fake, sha=self.epw_sha)
            raise AssertionError("should refuse")
        except SystemExit as e:
            assert "does not exist" in str(e)

    def test_refused_never_launches(self):
        """Refused cases must not invoke the subprocess."""
        launch_calls = []
        def track(cmd, **kw):
            launch_calls.append(cmd); return _mock_launch(cmd, **kw)
        try:
            self._run(epw=None, sha=self.epw_sha, launch=track)
        except SystemExit:
            pass
        assert len(launch_calls) == 0, "subprocess should NOT be called on refusal"

    def test_import_does_not_execute(self):
        """Importing the module must not launch E+ or create outputs."""
        # The module was already imported at the top; if it had import-time
        # execution, the import would have raised SystemExit or created files.
        # Verify no OUTPUT dirs exist in the current working directory.
        assert not os.path.exists("OUTPUT")


if __name__ == "__main__":
    obj = TestRunCase()
    tests = [(k, v) for k, v in sorted(vars(TestRunCase).items()) if k.startswith("test_")]
    p = f = 0
    for name, fn in tests:
        obj.setup_method()
        try:
            fn(obj)
            print(f"  PASS: {name}"); p += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}"); f += 1
        finally:
            obj.teardown_method()
    print(f"\n{p} passed, {f} failed out of {len(tests)}")
    if f: sys.exit(1)

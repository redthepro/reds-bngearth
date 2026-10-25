"""Distribution boundaries: optional launch helpers and the fork's update channel survive packaging."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import build_app  # noqa: E402


class SourcePackageTest(unittest.TestCase):
    def test_archives_are_standalone_and_keep_the_update_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            subprocess.run([sys.executable, str(ROOT / "tools/package.py"), "--out", tmp, "--beammp"],
                           check=True, capture_output=True, text=True)
            version = (ROOT / "VERSION").read_text().strip()
            top = f"BeamNG.Earth-{version}"
            with zipfile.ZipFile(out / (top + ".zip")) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn(top + "/bmplaunch.py", archive.namelist())
                self.assertFalse(any("/tests/" in name for name in archive.namelist()))
                archive.extractall(out / "unpacked")
            unpacked = out / "unpacked" / top
            # Isolated mode excludes both this checkout and PYTHONPATH: the extracted files must suffice.
            code = ("import json,sys; from pathlib import Path; p=Path(sys.argv[1]); "
                    "sys.path[:0]=[str(p),str(p/'server')]; import launcher,bmplaunch,updates; "
                    "print(json.dumps({'channel':updates.repository(str(p),{}),"
                    "'helper':str(Path(bmplaunch.__file__).resolve()),"
                    "'entry':callable(launcher.beammp_up)}))")
            result = subprocess.run([sys.executable, "-I", "-c", code, str(unpacked)], cwd=tmp,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            got = json.loads(result.stdout)
            self.assertEqual(got["channel"], (ROOT / "UPDATE_REPO").read_text().strip())
            self.assertEqual(got["helper"], str((unpacked / "bmplaunch.py").resolve()))
            self.assertTrue(got["entry"])
            with zipfile.ZipFile(out / f"BeamNG.Earth-BeamMP-Server-{version}.zip") as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("Resources/Server/BeamNGEarth/main.lua", archive.namelist())
                with zipfile.ZipFile(io.BytesIO(archive.read("Resources/Client/beamng_earth.zip"))) as mod:
                    self.assertIsNone(mod.testzip())
                    self.assertIn("lua/ge/extensions/beamngEarth/serverlist.lua", mod.namelist())


class FrozenPackageTest(unittest.TestCase):
    def test_both_platform_bundles_keep_beammp_and_the_selected_update_channel(self):
        cases = [({}, (ROOT / "UPDATE_REPO").read_text().strip()),
                 ({"GITHUB_REPOSITORY": "owner/ci-fork"}, "owner/ci-fork"),
                 ({"GITHUB_REPOSITORY": "owner/ci-fork", "BNGE_UPDATE_REPO": "owner/override"}, "owner/override")]
        for windows in (False, True):
            for env, channel in cases:
                with self.subTest(windows=windows, env=env), tempfile.TemporaryDirectory() as tmp, \
                        mock.patch.object(build_app, "IS_WIN", windows), \
                        mock.patch.object(build_app, "SEP", ";" if windows else ":"), \
                        mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.dict(sys.modules, {"peerwork": SimpleNamespace(source_id=lambda _: "test-code-id")}), \
                        mock.patch.object(build_app.subprocess, "run") as run:
                    build_app.pyinstaller(tmp, "icon.ico", onefile=windows, ver="1.6.2-beta")
                    command = run.call_args.args[0]
                    imports = [command[i + 1] for i, arg in enumerate(command) if arg == "--hidden-import"]
                    self.assertIn("bmplaunch", imports)
                    self.assertIn("--onefile" if windows else "--onedir", command)
                    sep = ";" if windows else ":"
                    self.assertIn(str(Path(tmp) / "UPDATE_REPO") + sep + ".", command)
                    self.assertEqual((Path(tmp) / "UPDATE_REPO").read_text().strip(), channel)


if __name__ == "__main__":
    unittest.main()

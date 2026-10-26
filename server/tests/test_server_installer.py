"""The server installer preserves operator config and confines resource updates."""
import importlib.util
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('earth_server_installer', ROOT / 'beammp/install_server.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
voice_spec = importlib.util.spec_from_file_location('earth_voice_installer', ROOT / 'deploy/voice/install.py')
voice_installer = importlib.util.module_from_spec(voice_spec)
voice_spec.loader.exec_module(voice_installer)


class InstallerTest(unittest.TestCase):
    def test_dry_run_install_and_recoverable_backup_preserve_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, target = Path(tmp) / 'package', Path(tmp) / 'server'
            client = Path('Resources/Client/beamng_earth.zip')
            plugin = Path('Resources/Server/BeamNGEarth/main.lua')
            (package / client).parent.mkdir(parents=True)
            (package / plugin).parent.mkdir(parents=True)
            with zipfile.ZipFile(package / client, 'w') as z:
                z.writestr('lua/ge/extensions/beamngEarth/core.lua', 'new')
            (package / plugin).write_text('new plugin')
            (package / 'VERSION').write_text('1.8.2')
            (target / plugin).parent.mkdir(parents=True)
            (target / plugin).write_text('old plugin')
            config = b'[General]\nPort=30814\nAuthKey="operator-owned-value"\n'
            (target / 'ServerConfig.toml').write_bytes(config)
            with patch.object(installer.socket, 'create_connection', side_effect=OSError):
                installer.install_resources(package, target, dry_run=True)
                self.assertFalse((target / client).exists())
                self.assertEqual((target / plugin).read_text(), 'old plugin')
                installer.install_resources(package, target)
            self.assertEqual((target / 'ServerConfig.toml').read_bytes(), config)
            self.assertEqual((target / plugin).read_text(), 'new plugin')
            self.assertEqual((target / client).read_bytes(), (package / client).read_bytes())
            self.assertEqual(len(list((target / 'backups').glob('earth-*/Resources/Server/BeamNGEarth/main.lua'))), 1)
            self.assertFalse(list((target / 'backups').rglob('ServerConfig.toml')))

    def test_quadlet_uses_private_registry_and_real_host_identity(self):
        text = voice_installer.unit_text(PurePosixPath('/home/player/voice data'), PurePosixPath('/home/player/registry'),
                                         'localhost/beamng-earth-voice:1.8.2', 64739)
        self.assertIn('UserNS=keep-id:uid=1000,gid=1000', text)
        self.assertIn('PublishPort=0.0.0.0:64739:64738/tcp', text)
        self.assertIn('"/home/player/registry":/game:z', text)
        self.assertNotIn('PublishPort=6502', text)
        with self.assertRaises(ValueError):
            voice_installer.unit_text('/tmp/line\nbreak', '/tmp/game', 'local', 64738)


if __name__ == '__main__': unittest.main()

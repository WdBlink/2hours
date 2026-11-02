"""Downloaded binaries and models must match pinned SHA-256 values, or be refused."""
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BLURT_SCRIPTS = ROOT / 'vendor' / 'blurt' / 'scripts'
INSTALL_JS = ROOT / 'vendor' / 'cubox' / 'cli' / 'scripts' / 'install.js'


def load_transcribe():
    sys.path.insert(0, str(BLURT_SCRIPTS))
    try:
        sys.modules.pop('transcribe', None)
        if importlib.util.find_spec('numpy'):
            # Use the real numpy and leave sys.modules alone: numpy 2.x refuses a second load per process.
            return importlib.import_module('transcribe')
        # download() does not use numpy; stub it only when the optional dependency is absent.
        with mock.patch.dict(sys.modules, {'numpy': types.ModuleType('numpy')}):
            return importlib.import_module('transcribe')
    finally:
        sys.path.remove(str(BLURT_SCRIPTS))


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class ModelDownloadChecksumTests(unittest.TestCase):
    def setUp(self):
        self.transcribe = load_transcribe()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.models = Path(self.directory.name)
        patcher = mock.patch.object(self.transcribe, 'MODELS_DIR', self.models)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, bodies):
        def urlopen(request, timeout):
            return FakeResponse(bodies[request.full_url])
        return mock.patch.object(self.transcribe.urllib.request, 'urlopen', side_effect=urlopen)

    def test_every_source_has_a_sha256(self):
        for rel, sources in self.transcribe.MODEL_FILES.items():
            self.assertTrue(sources, rel)
            for url, expected in sources:
                self.assertTrue(url.startswith('https://'), url)
                self.assertRegex(expected, r'^[0-9a-f]{64}$', url)

    def test_mismatched_download_is_rejected_and_next_source_used(self):
        good = b'verified model bytes'
        sources = [('https://a.example/m', hashlib.sha256(good).hexdigest()),
                   ('https://b.example/m', hashlib.sha256(good).hexdigest())]
        with mock.patch.dict(self.transcribe.MODEL_FILES, {'m.onnx': sources}), \
                self.serve({'https://a.example/m': b'tampered', 'https://b.example/m': good}):
            dest = self.transcribe.download('m.onnx')
        self.assertEqual(dest.read_bytes(), good)
        self.assertFalse((self.models / 'm.onnx.part').exists())

    def test_all_sources_mismatched_fails_closed(self):
        sources = [('https://a.example/m', '0' * 64), ('https://b.example/m', '0' * 64)]
        with mock.patch.dict(self.transcribe.MODEL_FILES, {'m.onnx': sources}), \
                self.serve({'https://a.example/m': b'x', 'https://b.example/m': b'y'}), \
                mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit):
                self.transcribe.download('m.onnx')
        self.assertIn('SHA-256 mismatch', stderr.getvalue())
        self.assertFalse((self.models / 'm.onnx').exists())
        self.assertFalse((self.models / 'm.onnx.part').exists())


@unittest.skipUnless(shutil.which('node'), 'node is required for the Cubox installer checks')
class CuboxInstallerChecksumTests(unittest.TestCase):
    def node(self, script, *args):
        return subprocess.run(['node', '-e', script, str(INSTALL_JS), *args], capture_output=True, text=True, timeout=30)

    def test_every_supported_archive_is_pinned(self):
        version = json.loads((INSTALL_JS.parents[1] / 'package.json').read_text())['version']
        result = self.node('console.log(JSON.stringify(require(process.argv[1]).SHA256))')
        self.assertEqual(result.returncode, 0, result.stderr)
        table = json.loads(result.stdout)
        for platform in ('darwin', 'linux', 'windows'):
            for arch in ('amd64', 'arm64'):
                ext = '.zip' if platform == 'windows' else '.tar.gz'
                name = f'cubox-cli-{version}-{platform}-{arch}{ext}'
                self.assertRegex(table.get(name, ''), r'^[0-9a-f]{64}$', name)

    def test_mismatch_and_unknown_archive_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'cubox.zip'
            archive.write_bytes(b'not the release archive')
            check = ('const m = require(process.argv[1]);'
                     'try { m.verifyArchive(process.argv[2], process.argv[3]); console.log("accepted"); }'
                     'catch (e) { console.log("rejected: " + e.message); }')
            mismatch = self.node(check, str(archive), 'cubox-cli-1.0.10-windows-amd64.zip')
            unknown = self.node(check, str(archive), 'cubox-cli-9.9.9-windows-amd64.zip')
        self.assertIn('rejected: SHA-256 mismatch', mismatch.stdout, mismatch.stderr)
        self.assertIn('rejected: No pinned SHA-256', unknown.stdout, unknown.stderr)

    def test_installer_verifies_before_extracting(self):
        source = INSTALL_JS.read_text(encoding='utf-8')
        install = source[source.index('function install()'):]
        self.assertLess(install.index('verifyArchive(archivePath, archiveName)'), install.index('Expand-Archive'))
        self.assertLess(install.index('verifyArchive(archivePath, archiveName)'), install.index('tar -xzf'))


if __name__ == '__main__':
    unittest.main()

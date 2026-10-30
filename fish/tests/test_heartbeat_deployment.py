"""Verify paired module replacement and rollback using a disposable installation."""
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
import deploy_heartbeat_update as update


class HeartbeatDeploymentTests(unittest.TestCase):
    def test_install_and_validation_failure_preserve_data_and_running_state(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root, stage = Path(tmp)/'root', Path(tmp)/'stage'
                root.mkdir()
                stage.mkdir()
                (root/'config.toml').write_text('[server]\ndb_path="fish.db"\n')
                (root/'.api-token').write_text('test-token')
                (root/'unrelated.py').write_text('# unchanged')
                manifest = {'before': {}, 'after': {}}
                for name in update.TARGETS:
                    (root/name).write_text('# old '+name)
                    (stage/name).write_text('# new '+name)
                    manifest['before'][name] = update.digest(root/name)
                    manifest['after'][name] = update.digest(stage/name)
                (stage/'heartbeat-manifest.json').write_text(json.dumps(manifest))
                with closing(sqlite3.connect(root/'fish.db')) as conn:
                    conn.execute('CREATE TABLE evidence (value TEXT)')
                    conn.execute("INSERT INTO evidence VALUES ('preserve me')")
                    conn.commit()
                with patch.object(update.deploy, 'ROOT', root), patch.object(update, 'running', return_value=False), \
                     patch.object(update.deploy, 'start') as start, patch.object(update.deploy, 'stop') as stop, \
                     patch.object(update, 'smoke', side_effect=RuntimeError('validation failed') if fail else None), \
                     redirect_stdout(io.StringIO()):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'validation failed'):
                            update.install(stage)
                    else:
                        update.install(stage)
                start.assert_not_called()
                stop.assert_not_called()
                for name in update.TARGETS:
                    self.assertEqual(update.digest(root/name), manifest['before' if fail else 'after'][name])
                self.assertEqual((root/'.api-token').read_text(), 'test-token')
                self.assertEqual((root/'unrelated.py').read_text(), '# unchanged')
                with closing(sqlite3.connect(root/'fish.db')) as conn:
                    self.assertEqual(conn.execute('SELECT value FROM evidence').fetchone()[0], 'preserve me')

"""Exercise delivery shell gates against real, isolated Git repositories."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='release delivery shell gates are verified on Linux CI; Windows/MSYS path handling differs')

ROOT = Path(__file__).resolve().parents[2]


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')}
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True, env=env).strip()


@pytest.mark.parametrize('payload_changed', [False, True])
def test_publish_ignores_only_provenance(tmp_path, payload_changed):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    (tmp_path / '.build-provenance.txt').write_text('source_commit: abc123\nconverted_at: old\n')
    (tmp_path / 'plugin.json').write_text('payload')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'baseline')
    (tmp_path / '.build-provenance.txt').write_text('source_commit: abc123\nconverted_at: new\n')
    if payload_changed:
        (tmp_path / 'plugin.json').write_text('changed')
    git(tmp_path, 'add', '.')
    workflow = yaml.safe_load((ROOT / '.github/workflows/publish-dist.yml').read_text())
    script = workflow['jobs']['publish']['steps'][-1]['run']
    condition = next(line.strip()[3:-6] for line in script.splitlines()
                     if line.strip().startswith('if git -C "$dist" diff'))
    result = subprocess.run(['bash', '-c', condition], env={**os.environ, 'dist': str(tmp_path), 'MSYS_NO_PATHCONV': '1'})
    assert result.returncode == (1 if payload_changed else 0)


@pytest.mark.parametrize(('payload_changed', 'provenance_change', 'expected'), [
    (False, 'timestamp', 0),
    (True, 'timestamp', 1),
    (False, 'source', 1),
    (False, 'converter', 1),
    (False, 'missing', 1),
    (False, 'no-timestamp', 1),
])
def test_release_requires_matching_payload(tmp_path, payload_changed, provenance_change, expected):
    remote = tmp_path / 'remote'
    remote.mkdir()
    git(remote, 'init', '-q', '-b', 'master')
    git(remote, 'config', 'user.name', 'Fixture')
    git(remote, 'config', 'user.email', 'fixture@example.invalid')
    (remote / 'plugin.json').write_text('payload')
    (remote / 'README.md').write_text('readme')
    (remote / 'LICENSE').write_text('license')
    (remote / 'skills').mkdir()
    (remote / 'skills' / 'demo').mkdir()
    (remote / 'skills' / 'demo' / 'SKILL.md').write_text('skill')
    (remote / 'config').mkdir()
    (remote / 'config' / 'models.json').write_text('{}')
    (remote / '.build-provenance.txt').write_text(
        'source_commit: abc123\nconverter: sha-a\nconverted_at: source\n'
    )
    git(remote, 'add', '.')
    git(remote, 'commit', '-qm', 'source')
    git(remote, 'checkout', '--orphan', 'dist')
    # The orphan checkout keeps the source tree in the index and working tree, so
    # the package files are already present. Drop only the provenance file and let
    # the cases below write it (or omit it for 'missing').
    git(remote, 'rm', '-q', '-f', '.build-provenance.txt')
    (remote / 'plugin.json').write_text('changed' if payload_changed else 'payload')
    if provenance_change == 'missing':
        pass
    elif provenance_change == 'no-timestamp':
        (remote / '.build-provenance.txt').write_text(
            'source_commit: abc123\nconverter: sha-a\n'
        )
    else:
        source = 'def456' if provenance_change == 'source' else 'abc123'
        converter = 'sha-b' if provenance_change == 'converter' else 'sha-a'
        (remote / '.build-provenance.txt').write_text(
            f'source_commit: {source}\nconverter: {converter}\nconverted_at: dist\n'
        )
    (remote / '.gitattributes').write_text('* text=auto eol=lf')
    git(remote, 'add', '.')
    git(remote, 'commit', '-qm', 'package')
    git(remote, 'checkout', 'master')
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '-q', str(remote), str(checkout)], check=True)
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    env_file = tmp_path / 'env'
    workflow = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())
    script = next(s['run'] for s in workflow['jobs']['build']['steps']
                  if s.get('name') == 'Verify release source and published package')
    result = subprocess.run(['bash', '-c', script], cwd=checkout,
                            env={**os.environ, 'RUNNER_TEMP': str(runtime),
                                 'GITHUB_ENV': str(env_file)}, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    if expected == 0:
        assert f'PACKAGE_SHA={git(remote, "rev-parse", "dist")}' in env_file.read_text()

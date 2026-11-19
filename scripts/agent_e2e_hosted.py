#!/usr/bin/env python3
"""Bootstrap a disposable, source-built LazyMind on a GitHub-hosted VM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from types import SimpleNamespace

import agent_e2e as e2e
import agent_e2e_platform as platform

ROOT = Path(__file__).resolve().parents[1]


def run(command, *, cwd=ROOT, env=None, timeout=1800):
    print('Running ' + Path(command[0]).name + ' ' + command[1], flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout)


def paths(root):
    return SimpleNamespace(root=root, runtime=root / 'runtime', build=root / 'build',
                           home=root / 'connector', output=root / 'results',
                           manager=root / 'bin' / platform.executable_name('local-runtime-manager'))


def runtime_args(p):
    return ['--profile', 'local', '--repo-root', str(ROOT), '--runtime-root', str(p.runtime),
            '--build-root', str(p.build), '--resources-root', str(ROOT)]


def environment(p, create=False):
    private = p.root / 'runtime-environment.json'
    if create and not private.exists():
        values = {name: secrets.token_urlsafe(32) for name in
                  ('LAZYMIND_BOOTSTRAP_ADMIN_PASSWORD', 'LAZYMIND_JWT_SECRET',
                   'LAZYMIND_AUTH_SERVICE_INTERNAL_TOKEN', 'LAZYMIND_FILE_WATCHER_AGENT_TOKEN')}
        e2e.private_config(private, json.dumps(values))
    values = json.loads(private.read_text(encoding='utf-8'))
    for value in values.values():
        print('::add-mask::' + value, flush=True)
    env = dict(os.environ)
    env.update(values)
    env.update(LAZYMIND_RAG_ENABLED='false', LAZYMIND_LOCAL_ENABLE_EVO='false',
               LAZYMIND_BOOTSTRAP_ADMIN_USERNAME='ci-admin', LAZYMIND_LOCAL_UP_TIMEOUT='600',
               LAZYMIND_RUNTIME_ROOT=str(p.runtime), LAZYMIND_LOCAL_BUILD_ROOT=str(p.build),
               CGO_ENABLED='0' if platform.IS_WINDOWS else '1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return env


def start(p):
    p.manager.parent.mkdir(parents=True, exist_ok=True)
    env = environment(p, create=True)
    run(['go', 'build', '-o', str(p.manager), '.'], cwd=ROOT / 'local/local-runtime-manager', env=env)
    run([str(p.manager), 'up', *runtime_args(p)], env=env, timeout=2700)
    args = SimpleNamespace(output=str(p.output))
    e2e.build_cli(args)
    cli = p.output / 'bin' / platform.executable_name('lazymind')
    # The normal connector credential store obtains a real local admin session.
    # Never manufacture JWTs or copy credentials from another runtime.
    p.home.mkdir(mode=0o700, exist_ok=True)
    client = e2e.MCP(str(cli), e2e.clean_env(p.home, 'http://localhost:8090', 'codex'), timeout=120)
    try:
        listing = client.call('workflow.list')
        workflows = [{k: item.get(k) for k in ('workflow_id', 'revision_id', 'revision_no')}
                     for item in listing.get('workflows', [])
                     if item.get('workflow_id') in {case[0] for case in e2e.CASES.values()}]
        if len(workflows) != 3:
            raise RuntimeError('Expected all three built-in workflows in fresh runtime')
        for item in workflows:
            client.call('workflow.get', workflow_id=item['workflow_id'], revision_id=item['revision_id'])
    finally:
        client.close()
    e2e.write_json(p.output / 'hosted-runtime.json', {
        'status': 'passed', 'platform': sys.platform, 'authentication': 'normal local admin-session bootstrap',
        'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'workflows': workflows, 'business_cases_executed': False})
    print('Fresh LazyMind runtime and all three MCP workflow revisions verified.', flush=True)
    with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as stream:
        for key, value in {'HOSTED_ROOT': p.root, 'LAZYMIND_HOME': p.home,
                           'LAZYMIND_SERVER_URL': 'http://localhost:8090'}.items():
            stream.write(f'{key}={value}\n')


def stop(p):
    if p.manager.exists() and (p.root / 'runtime-environment.json').exists():
        run([str(p.manager), 'down', *runtime_args(p)], env=environment(p), timeout=180)


def diagnose(p):
    """Expose bounded, redacted startup errors, never the private runtime tree."""
    for path in sorted(p.runtime.rglob('*.log')):
        if path.is_file():
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[-15:]
            print(str(path.relative_to(p.runtime)), flush=True)
            for line in lines:
                print(e2e.safe_diagnostic(line), flush=True)


def install_agents(p):
    """Install pinned vendor distributions, without a desktop login dependency."""
    tools = p.root / 'agent-tools'
    tools.mkdir(exist_ok=True)
    for name in ('package.json', 'package-lock.json'):
        shutil.copy2(ROOT / 'scripts/ci-agent-tools' / name, tools / name)
    npm = shutil.which('npm.cmd' if platform.IS_WINDOWS else 'npm')
    node = shutil.which('node')
    npm_command = [node, str(Path(npm).parent / 'node_modules/npm/bin/npm-cli.js')] if platform.IS_WINDOWS else [npm]
    run([*npm_command, 'ci', '--no-audit', '--no-fund'], cwd=tools, timeout=900)
    modules = tools / 'node_modules'
    commands = {'codex': [node, str(modules / '@openai/codex/bin/codex.js')],
                'deepseek-harness': [node, str(modules / '@deepseek-ai/dsh/lib/bin.js')]}
    # These URLs and hashes are the vendor's public /v2/update metadata.
    # Keep a fixed version; do not silently follow a new latest installer.
    distributions = {
        'darwin-x64': ('zip', 'f17292ee0a9702670d739679b76d3a84a012dbd232f45c6479bf37cc00cf402c'),
        'win32-x64-user': ('exe', '38841a16a766e488eeba9adf322b92239e69cbb7a309dac7a35fee495496441c')}
    target = 'win32-x64-user' if platform.IS_WINDOWS else 'darwin-x64'
    ext, digest = distributions[target]
    url = f'https://download.codebuddy.ai/workbuddy/saas/{target}/WorkBuddy-{target}-5.11.4.44121853-0026dff0.{ext}'
    archive = p.root / ('workbuddy-download.' + ext)
    print('Downloading official WorkBuddy 5.11.4.44121853 for ' + target, flush=True)
    with urllib.request.urlopen(url, timeout=120) as response, archive.open('wb') as stream:
        shutil.copyfileobj(response, stream)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Official WorkBuddy installer SHA-256 mismatch')
    buddy = p.root / 'workbuddy'
    if platform.IS_WINDOWS:
        # WorkBuddy's Windows distribution uses the standard Inno Setup installer.
        run([str(archive), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
             '/MERGETASKS=!runcodebuddy,!desktopicon,!quicklaunchicon', '/DIR=' + str(buddy)], timeout=600)
    else:
        with zipfile.ZipFile(archive) as source:
            # Only the genuine bundled CLI is needed for headless CI.
            for entry in source.infolist():
                if '/app.asar.unpacked/cli/' in entry.filename and not entry.is_dir():
                    rel = Path(entry.filename)
                    if rel.is_absolute() or '..' in rel.parts:
                        raise RuntimeError('Unsafe WorkBuddy archive entry')
                    source.extract(entry, buddy)
    launchers = list(buddy.rglob('codebuddy'))
    launchers = [path for path in launchers if path.is_file() and path.parent.name == 'bin'
                 and (path.parent.parent / 'product.json').is_file()]
    if len(launchers) != 1:
        # Give public layout clues, without dumping app files or user data.
        print('WorkBuddy CLI candidates: ' + json.dumps([str(x.relative_to(buddy)) for x in buddy.rglob('*codebuddy*')][:20]))
        raise RuntimeError('Expected one genuine WorkBuddy bundled CLI')
    commands['workbuddy'] = [node, str(launchers[0])]
    archive.unlink()
    env = dict(os.environ)
    env.update(DSH_HOME=str(p.root / 'dsh'), WORKBUDDY_CONFIG_DIR=str(p.root / 'workbuddy-config'))
    subprocess.run(commands['deepseek-harness'] + ['--profile', 'lazymind-ci', '--from-default-profile', 'headless', '--dump-config'],
                   env=env, timeout=120, check=True, stdout=subprocess.DEVNULL)
    e2e.private_config(p.root / 'agent-commands.json', json.dumps(commands))
    versions = {name: e2e.version(command) for name, command in commands.items()}
    e2e.write_json(p.output / 'installed-agents.json', {'versions': versions, 'workbuddy_distribution_sha256': digest})
    if any(value == 'unavailable' for value in versions.values()):
        raise RuntimeError('An installed Agent could not report its version')
    with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as stream:
        for key, value in {'HOSTED_ROOT': p.root, 'LAZYMIND_CI_AGENT_COMMANDS': p.root / 'agent-commands.json',
                           'DSH_HOME': p.root / 'dsh', 'WORKBUDDY_CONFIG_DIR': p.root / 'workbuddy-config'}.items():
            stream.write(f'{key}={value}\n')


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('This bootstrap runs only on a disposable GitHub Actions VM.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'diagnose', 'install-agents'))
    parser.add_argument('--root', type=Path, default=Path(os.environ['RUNNER_TEMP']) / 'lazymind-hosted')
    args = parser.parse_args()
    root = args.root.resolve()
    if root.is_relative_to(ROOT):
        raise SystemExit('Runtime secrets and credentials must be outside the checkout.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    {'start': start, 'stop': stop, 'diagnose': diagnose, 'install-agents': install_agents}[args.action](paths(root))


if __name__ == '__main__':
    main()

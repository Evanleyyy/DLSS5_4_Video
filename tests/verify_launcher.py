"""Exercise the actual launcher with small owned fixtures, without extracting models."""
import os
from pathlib import Path
import subprocess
import time
import uuid
import sys

ROOT = Path(__file__).resolve().parents[1]
directory = ROOT / 'build' / ('launcher-tests-' + uuid.uuid4().hex)
directory.mkdir(parents=True)
compiler = r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
build_id = 'abcdef0123456789abcd'
info = directory / 'BuildInfo.cs'
info.write_text('internal static class BuildInfo { public const string Id = "' + build_id + '"; }')
launcher = directory / 'launcher.exe'
subprocess.run([compiler, '/nologo', '/target:winexe', '/platform:x64', '/codepage:65001',
                '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                '/out:' + str(launcher), str(ROOT / 'packaging/LargePackageLauncher.cs'),
                str(ROOT / 'packaging/RuntimeCache.cs'), str(info)], check=True)
child_source = directory / 'Probe.cs'
child_source.write_text('''using System; using System.IO; using System.Threading;
class Probe { static int Main(string[] args) {
    File.WriteAllText(args[0] + ".started", "ready");
    for (int i = 0; i < 1000 && !File.Exists(args[0]); i++) Thread.Sleep(20);
    return Int32.Parse(args[1]);
} }''')
child = directory / 'probe.exe'
subprocess.run([compiler, '/nologo', '/target:exe', '/out:' + str(child), str(child_source)], check=True)
runtime = directory / 'runtime'
folder = runtime / build_id
environment = dict(os.environ, DLSS5_CACHE_ROOT=str(runtime), DLSS5_LOG_DIR=str(directory / 'logs'))
processes = []


def create_cache():
    (folder / 'app').mkdir(parents=True, exist_ok=True)
    (folder / 'ready.txt').write_text(build_id)
    (folder / 'app/DLSS5_App.exe').write_bytes(child.read_bytes())


def start(name, code=0):
    release = directory / name
    process = subprocess.Popen([str(launcher), str(release), str(code)], env=environment,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    processes.append((process, release))
    deadline = time.monotonic() + 10
    while not Path(str(release) + '.started').exists():
        assert process.poll() is None, '启动器提前退出'
        assert time.monotonic() < deadline, '测试子进程未启动'
        time.sleep(.02)
    return process, release


def stop(pair, code=0):
    process, release = pair
    release.write_text('exit')
    assert process.wait(timeout=15) == code


def cleanup():
    result = subprocess.run([str(launcher), '--clean-runtime-cache', build_id], env=environment,
                            capture_output=True, encoding='utf-8-sig', creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert '关闭' in result.stdout


try:
    create_cache()
    stop(start('ordinary'))
    assert folder.is_dir(), '普通退出不应清理缓存'
    print('通过：真实启动器普通退出保留缓存')
    first, second = start('first'), start('second')
    sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
    from cache_manager import runtime_entries
    entries, warnings = runtime_entries(runtime)
    assert len(entries) == 1 and not warnings, ('使用中的运行缓存必须可统计', warnings)
    cleanup()
    stop(first)
    assert (folder / 'app/DLSS5_App.exe').is_file(), '另一窗口仍在使用'
    stop(second)
    assert not folder.exists()
    print('通过：真实启动器多开、手动请求、最后退出清理')
    create_cache()
    failing = start('nonzero', 17)
    cleanup()
    stop(failing, 17)
    assert not folder.exists()
    print('通过：子进程非零退出仍执行已请求的清理')
    environment['DLSS5_LAUNCHER_PATH'] = str(launcher)
    subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), str(ROOT / 'tests/gui_cache_smoke.py')],
                   env=environment, check=True)
    print('通过：图形界面调用真实启动器清理历史缓存')
finally:
    for process, release in processes:
        if process.poll() is None:
            release.write_text('exit')
            process.wait(timeout=30)

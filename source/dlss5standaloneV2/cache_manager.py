"""Explicit cache inventory and bounded deletion; exported files are never targets."""
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading

VERSION = re.compile(r'[0-9a-f]{20}')
_locks = {}
_locks_guard = threading.Lock()


def safe_path(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'路径包含链接或联接，已跳过：{item}')
    return path


@contextmanager
def video_cache_guard(video):
    """Serialize cache generation/export/cleanup across app instances on Windows."""
    if not video:
        yield
        return
    name = hashlib.sha256(os.path.normcase(os.path.realpath(video)).encode('utf-8')).hexdigest()[:32]
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateMutexW(None, False, 'Local\\DLSS5_Video_' + name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
            if not acquired:
                raise RuntimeError('此视频正在另一个窗口中处理、导出或清理，请稍后重试。')
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        with _locks_guard:
            lock = _locks.setdefault(name, threading.RLock())
        if not lock.acquire(blocking=False):
            raise RuntimeError('此视频缓存正在使用，请稍后重试。')
        try:
            yield
        finally:
            lock.release()


def uses_video_cache(function):
    @wraps(function)
    def guarded(video, *args, **kwargs):
        with video_cache_guard(video):
            return function(video, *args, **kwargs)
    return guarded


def runtime_root():
    return Path(os.environ.get('DLSS5_CACHE_ROOT') or
                Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'DLSS5Standalone' / 'runtime')


def folder_size(folder):
    folder = safe_path(folder)
    total = 0
    for child in folder.iterdir():
        safe_path(child)
        total += folder_size(child) if child.is_dir() else child.stat().st_size
    return total


def runtime_entries(root):
    root = safe_path(root)
    entries, warnings = [], []
    if not root.exists():
        return entries, warnings
    for folder in sorted(root.iterdir()):
        if not VERSION.fullmatch(folder.name):
            continue
        try:
            safe_path(folder)
            owned = any(safe_path(folder / name).is_file() and
                        (folder / name).read_text(encoding='utf-8-sig').strip() == folder.name
                        for name in ('runtime-owner.txt', 'ready.txt'))
            if not owned:
                continue
            entries.append({'type': 'runtime', 'id': folder.name, 'path': str(folder),
                            'root': str(root), 'size': folder_size(folder),
                            'pending': (folder / 'cleanup-request.txt').is_file(),
                            'current': folder.name == os.environ.get('DLSS5_RUNTIME_ID')})
        except (OSError, ValueError) as error:
            warnings.append(f'{folder.name}：{error}')
    return entries, warnings


def video_files(video, kind):
    if kind not in ('depth', 'flow', 'dlss'):
        raise ValueError('未知视频缓存类型')
    video = Path(os.path.abspath(video))
    folder = safe_path(video.with_name(video.stem + '_' + kind))
    if not folder.exists():
        return folder, []
    marker = safe_path(folder / 'cache.json')
    if not marker.is_file():
        raise ValueError(f'缺少缓存记录，已保留：{folder}')
    record = json.loads(marker.read_text(encoding='utf-8'))['record']
    if (os.path.normcase(os.path.abspath(record['source'])) != os.path.normcase(str(video)) or
            record['options']['kind'] != kind):
        raise ValueError(f'缓存记录与视频不匹配，已保留：{folder}')
    extensions = {'depth': ('jpg', 'png'), 'flow': ('flo',), 'dlss': ('png',)}[kind]
    pattern = re.compile(r'\d{6,}\.(' + '|'.join(extensions) + ')')
    files = [safe_path(path) for path in sorted(folder.iterdir()) if pattern.fullmatch(path.name)]
    if any(not path.is_file() for path in files):
        raise ValueError(f'缓存帧不是普通文件，已保留：{folder}')
    return folder, files + [marker]


def inventory(video=None):
    entries, warnings = runtime_entries(runtime_root())
    try:
        entries.extend(sr_cache_entries())
    except (OSError, ValueError) as error:
        warnings.append(str(error))
    if video:
        for kind in ('depth', 'flow', 'dlss'):
            try:
                folder, files = video_files(video, kind)
                if files:
                    entries.append({'type': kind, 'path': str(folder), 'video': video,
                                    'size': sum(path.stat().st_size for path in files)})
            except (OSError, ValueError, KeyError, TypeError) as error:
                warnings.append(str(error))
    return entries, warnings


def sr_cache_entries():
    import sr_settings
    root = safe_path(sr_settings.data_root() / 'sr-cache')
    entries = []
    if root.is_dir():
        for folder in root.iterdir():
            safe_path(folder)
            if not re.fullmatch(r'job-[0-9a-f]{32}', folder.name) or not folder.is_dir():
                continue
            try:
                owner = json.loads((folder / 'owner.json').read_text())
                if owner.get('application') != 'DLSS5Standalone':
                    continue
                entries.append({'type': 'sr', 'path': str(folder), 'size': folder_size(folder)})
            except (OSError, ValueError):
                continue
    return entries


def clean_sr_cache(entry):
    import psutil
    import sr_settings
    root = safe_path(sr_settings.data_root() / 'sr-cache')
    folder = safe_path(entry['path'])
    if folder.parent != root or not re.fullmatch(r'job-[0-9a-f]{32}', folder.name):
        raise ValueError('不是本软件的超分缓存目录')
    with video_cache_guard(str(root)):
        owner = json.loads((folder / 'owner.json').read_text())
        if owner.get('application') != 'DLSS5Standalone':
            raise ValueError('超分缓存归属不匹配')
        if owner.get('pid') != os.getpid() and psutil.pid_exists(owner.get('pid', -1)):
            raise RuntimeError('其他软件窗口正在使用此缓存，请关闭对应窗口后再清理')
        if (folder / 'active').exists():
            raise RuntimeError('超分任务正在使用此缓存')
        removed = 0
        # Recheck every concrete generated file, never follow links or touch model directories.
        def remove_files(directory):
            nonlocal removed
            for path in list(directory.iterdir()):
                safe_path(path)
                if path.is_dir():
                    remove_files(path)
                    path.rmdir()
                else:
                    removed += path.stat().st_size
                    path.unlink()
        remove_files(folder)
        folder.rmdir()
        return removed


def clean_video(video, kind):
    with video_cache_guard(video):
        folder, files = video_files(video, kind)  # Revalidate immediately before removing files.
        removed = 0
        for path in files:
            safe_path(path)
            size = path.stat().st_size
            path.unlink()  # One explicit generated file at a time; no recursive deletion.
            removed += size
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()  # Empty directory only; unrelated files/subfolders stay in place.
        return removed


def clean_runtime(entry, cancel=False):
    launcher = os.environ.get('DLSS5_LAUNCHER_PATH')
    if not launcher or not Path(launcher).is_file():
        raise RuntimeError('请使用新版独立 EXE 清理运行环境缓存。')
    environment = dict(os.environ, DLSS5_CACHE_ROOT=entry['root'])
    result = subprocess.run([launcher, '--cancel-runtime-cleanup' if cancel else '--clean-runtime-cache', entry['id']],
                            env=environment, capture_output=True, encoding='utf-8-sig', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    message = result.stdout.strip() or result.stderr.strip() or '启动器未返回清理结果'
    if result.returncode not in (0, 2, 3):
        raise RuntimeError(message)
    return result.returncode, message


def format_size(value):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1000 or unit == 'TB':
            return f'{value:.2f} {unit}'
        value /= 1000

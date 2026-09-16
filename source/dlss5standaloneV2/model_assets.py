"""Local-first, verified model preparation shared by setup, GUI and inference."""
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time
import urllib.error
import urllib.request

import sr_settings
import task_control


def load_manifest():
    directories = [sr_settings.app_root() / directory for directory in ('说明', 'packaging')]
    if getattr(sys, 'frozen', False):
        directories.append(Path(sys._MEIPASS) / 'model-manifests')
    for directory in directories:
        path = directory / 'sr-assets-manifest.json'
        if path.is_file():
            return json.loads(path.read_text(encoding='utf-8'))
    raise FileNotFoundError('缺少模型校验清单，请重新安装程序。')


def assets(engines, manifest=None):
    manifest = load_manifest() if manifest is None else manifest
    if set(engines) - {'pisa', 'seedvr2', 'vosr', 'guidance', 'depth', 'flow'}:
        raise ValueError('未知的模型组件')
    result = []
    for entry in manifest['models']:
        name = entry['path']
        if name.split('/')[0] not in engines or name.endswith('/README.md'):
            continue
        if name == 'pisa/pisa_sr.pkl':
            url = 'https://drive.usercontent.google.com/download?id=1PWFM5zUd6lNMOyKX64anvUXhKEOCrf8L&export=download&confirm=t'
        elif name.startswith('pisa/sd21/'):
            # Keep legacy content hashes, but only contact the original publisher.
            url = 'https://huggingface.co/stabilityai/stable-diffusion-2-1-base/resolve/main/' + name.removeprefix('pisa/sd21/')
        else:
            engine, relative = name.split('/', 1)
            repo = {'vosr': 'CSWRY/VOSR', 'seedvr2': 'numz/SeedVR2_comfyUI'}[engine]
            url = f'https://huggingface.co/{repo}/resolve/{manifest["weight_repositories"][repo]}/{relative}'
        result.append({**entry, 'url': url})
    result.extend(entry for entry in manifest.get('guidance_models', [])
                  if 'guidance' in engines or entry['kind'] in engines)
    if not result:
        raise ValueError('所选组件在模型清单中没有文件，请重新安装程序。')
    for entry in result:
        name = PurePosixPath(entry['path'])
        if (name.is_absolute() or '..' in name.parts or '\\' in entry['path'] or ':' in entry['path']
                or not re.fullmatch('[0-9a-f]{64}', entry['sha256']) or entry['size'] <= 0):
            raise ValueError('模型清单格式不正确')
    return result


def _inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('模型路径超出所选目录')
    return path


@contextmanager
def _locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with _inside(root, '.prepare.lock').open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            lock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            lock()
        except OSError as error:
            raise RuntimeError('另一窗口正在准备此目录的模型，请稍后重试。') from error
        try:
            yield
        finally:
            unlock()


def _stamp(path, entry):
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, entry['sha256']]


def _valid(path, entry, checkpoint, force=False):
    if not path.is_file() or path.stat().st_size != entry['size']:
        return False
    stamp = _stamp(path, entry)
    receipt = sr_settings.data_root() / 'model-checks' / (hashlib.sha256(str(path.resolve()).encode()).hexdigest() + '.json')
    try:
        if not force and json.loads(receipt.read_text(encoding='utf-8')) == stamp:
            return True
    except (OSError, ValueError):
        pass
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            checkpoint()
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    if digest.hexdigest() != entry['sha256'] or _stamp(path, entry) != stamp:
        return False
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(stamp), encoding='utf-8')
    return True


def _download(path, entry, checkpoint, report):
    # Publish only complete, verified files under their loadable model names.
    partial = _inside(path.parent, path.name + '.part')
    size = entry['size']
    last_error = '下载未完成'
    for attempt in range(3):
        checkpoint()
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == size and _valid(partial, entry, checkpoint, force=True):
            os.replace(partial, path)
            return
        if offset >= size:
            partial.write_bytes(b'')
            offset = 0
        headers = {'User-Agent': 'DLSS5Standalone-model-preparer', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        try:
            with urllib.request.urlopen(urllib.request.Request(entry['url'], headers=headers), timeout=30) as response:
                if response.status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not match or int(match[1]) != offset or int(match[3]) != size:
                        partial.write_bytes(b'')
                        raise ValueError('服务器续传范围不匹配，准备重新下载')
                elif response.status == 200:
                    offset = 0  # A server without Range support must restart, not append.
                else:
                    raise ValueError('服务器返回了不支持的下载响应')
                last_report = 0.
                with partial.open('ab' if offset else 'wb') as handle:
                    while True:
                        checkpoint()
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        if offset + len(block) > size:
                            raise ValueError('下载内容超过清单大小')
                        handle.write(block)
                        offset += len(block)
                        if time.monotonic() - last_report > .25:
                            report(f'下载 {offset / 1048576:.1f}/{size / 1048576:.1f} MB')
                            last_report = time.monotonic()
            report('校验下载文件')
            if not _valid(partial, entry, checkpoint, force=True):
                if partial.stat().st_size == size:
                    partial.write_bytes(b'')
                raise ValueError('模型大小或 SHA-256 校验失败')
            os.replace(partial, path)
            return
        except urllib.error.HTTPError as error:
            last_error = f'官方源返回 HTTP {error.code}'
            if error.code in (401, 403, 404):
                raise RuntimeError(f'{entry["path"]}：{last_error}，请从官方页面确认访问权限，或选择已有本地模型目录。\n来源：{entry["url"]}') from None
            if error.code == 416:
                partial.write_bytes(b'')
        except (OSError, ValueError, http.client.HTTPException) as error:
            # Low-level URL errors may contain proxy credentials.
            last_error = str(error) if isinstance(error, ValueError) else type(error).__name__
        if attempt < 2:
            report('连接中断，正在重试')
            for _ in range(10):
                checkpoint()
                time.sleep(.1)
    raise RuntimeError(f'{entry["path"]}：{last_error}。已保留下载进度，请检查网络后重试。')


def prepare(engines, settings=None, progress=None, *, check_only=False, force_verify=False, checkpoint=None):
    checkpoint = checkpoint or task_control.checkpoint
    root = sr_settings.model_root(settings)
    entries = assets(engines)
    result = {'reused': 0, 'downloaded': 0, 'missing': [], 'paths': {}}
    with _locked(root):
        for index, entry in enumerate(entries):
            checkpoint()
            def report(stage):
                if progress:
                    progress(index, len(entries), f'{stage}：{entry["path"]}')
            report('检查本地模型')
            path = _inside(root, entry['path'])
            candidates = [path]
            if entry.get('legacy_path'):
                base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
                candidates.append(_inside(base, entry['legacy_path']))
            for candidate in candidates:
                if _valid(candidate, entry, checkpoint, force_verify):
                    result['reused'] += 1
                    result['paths'][entry['path']] = str(candidate)
                    break
            else:
                if check_only:
                    result['missing'].append(entry['path'])
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                _download(path, entry, checkpoint, report)
                _valid(path, entry, checkpoint)
                result['downloaded'] += 1
                result['paths'][entry['path']] = str(path)
        if progress:
            progress(len(entries), len(entries), '本地模型检查完成' if check_only else '模型已就绪')
    return result


def guidance_path(kind):
    cfg = sr_settings.load_preferences()
    return next(iter(prepare([kind], cfg)['paths'].values()))

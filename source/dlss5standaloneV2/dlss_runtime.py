"""Versioned local neural-rendering runtimes. Importing never loads a DLL."""
from functools import lru_cache
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess
import sys

import sr_settings

LABELS = {
    'auto': '自动选择（按显卡）',
    'bundled': '原版 310.8.0（40 系）',
    '310.8.SF-v2': '310.8.SF-v2（30 系兼容，实验版）',
    '310.8.SF': '310.8.SF（30 系兼容，旧版）',
}


def package_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def runtime_root():
    return sr_settings.app_root() / 'runtime' / 'dlssnr'


def normalize_version(value):
    if value not in LABELS:
        raise ValueError('未知的 DLSS 模型版本：' + str(value))
    return value


@lru_cache(maxsize=32)
def _file_info(path, size, mtime):
    """Read bounded CUDA fatbin records; architecture presence is not a run test."""
    architectures = set()
    with open(path, 'rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if size == 0:
            return digest, ()
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            offset = 0
            while True:
                start = data.find(b'\x50\xed\x55\xba', offset)
                if start < 0:
                    break
                offset = start + 4
                if start + 16 > size:
                    continue
                version, header, payload = struct.unpack_from('<HHQ', data, start + 4)
                position, end = start + header, start + header + payload
                if version != 1 or header < 16 or end > size or payload == 0:
                    continue
                while position + 32 <= end:
                    kind, entry_version, entry_header, entry_size = struct.unpack_from('<HHIQ', data, position)
                    if not 32 <= entry_header <= 4096 or not entry_size or position + entry_header + entry_size > end:
                        break
                    # kind 2 is a compiled cubin. PTX requires a separate JIT compatibility test.
                    sm = struct.unpack_from('<I', data, position + 28)[0]
                    if kind == 2 and entry_version == 0x101 and 50 <= sm <= 130:
                        architectures.add(sm)
                    position += entry_header + entry_size
    return digest, tuple(sorted(architectures))


def file_info(path):
    path = Path(path)
    stat = path.stat()
    digest, architectures = _file_info(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    return {'sha256': digest, 'architectures': list(architectures), 'bytes': stat.st_size}


@lru_cache(maxsize=1)
def gpu_info():
    """Use the same DXGI adapter policy as the supplied host: reject ambiguous GPUs."""
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=name,compute_cap,driver_version,memory.total',
                                 '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        rows = [line.split(',') for line in result.stdout.splitlines() if line.strip()]
        if len(rows) != 1 or len(rows[0]) != 4:
            return {'name': '多显卡或未识别', 'sm': None, 'driver': '', 'memory_mib': None}
        name, capability, driver, memory = [part.strip() for part in rows[0]]
        major, minor = capability.split('.')
        return {'name': name, 'sm': int(major) * 10 + int(minor), 'driver': driver, 'memory_mib': int(memory)}
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
        return {'name': '未识别（请检查 NVIDIA 驱动）', 'sm': None, 'driver': '', 'memory_mib': None}


def version_path(version):
    normalize_version(version)
    if version == 'bundled':
        return package_dir() / 'nvngx_dlssnr.dll'
    return runtime_root() / version / 'nvngx_dlssnr.dll'


def resolve(version='auto', gpu=None):
    normalize_version(version)
    gpu = gpu_info() if gpu is None else gpu
    sm = gpu['sm']
    if version == 'auto':
        if sm is None:
            raise ValueError('无法确定显卡架构，请手动选择模型版本并运行自检')
        if sm == 89:
            version = 'bundled'
        elif sm in (75, 86, 120):
            version = '310.8.SF-v2'
        else:
            raise ValueError(f'暂未提供适配 sm_{sm} 的运行库')
    path = version_path(version)
    if not path.is_file():
        raise FileNotFoundError(f'模型版本 {version} 未安装：{path}')
    info = file_info(path)
    manifest = runtime_root() / 'manifest.json'
    if manifest.is_file():
        expected = json.loads(manifest.read_text(encoding='utf-8'))['runtimes'].get(version, {}).get('sha256')
        if expected and expected != info['sha256']:
            raise ValueError(f'模型版本 {version} 校验失败，请重新准备运行库')
    if not info['architectures']:
        raise ValueError(f'模型版本 {version} 未发现可识别的 CUDA 内核')
    if sm is not None and sm not in info['architectures']:
        raise ValueError(f'模型版本 {version} 不包含当前显卡 sm_{sm} 内核，请选择兼容版本')
    return {'id': version, 'path': str(path), **info}


def fingerprint(settings):
    if settings.get('super_resolution', {}).get('engine', 'dlss') != 'dlss' or settings.get('overall_weight', 1) == 0:
        return []
    layers = [settings] + ([settings['second_layer']] if settings.get('second_layer') else [])
    result = []
    for layer in layers:
        version = layer.get('runtime_version', 'auto')
        try:
            info = resolve(version)
            result.append({'id': info['id'], 'sha256': info['sha256']})
        except (OSError, ValueError, KeyError) as error:
            result.append({'id': version, 'unavailable': str(error)})
    return result


def load_preferences():
    try:
        value = json.loads((sr_settings.data_root() / 'dlss-runtime.json').read_text(encoding='utf-8'))
        return [normalize_version(value.get(key, 'auto')) for key in ('first', 'second')]
    except (OSError, ValueError, TypeError, AttributeError):
        return ['auto', 'auto']


def save_preferences(first, second):
    path = sr_settings.data_root() / 'dlss-runtime.json'
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps({'first': normalize_version(first), 'second': normalize_version(second)},
                                     ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)

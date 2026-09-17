"""Fetch pinned community runtimes; inference itself never contacts the network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))
import dlss_runtime

ASSETS = {
    '310.8.SF-v2': ('1da35941894994eb087e017577829e492454e9bae3a6a9397027069ceb74955c',
                    '6eb209e764f39872625debd6abaf45e2bb6322f6f270f781f70c059ae30b3927'),
    '310.8.SF': ('4de991bf3a1cf5ba95c6621c2a5203299e823ea11bf1702513281b85aa4dc449',
                 '4c5bd1171c7336b4b04fb394de51da285ab6ead6f922d7afdec163f71c319d74'),
}


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description='准备固定版本 DLSS 运行库，后续可离线运行')
    parser.add_argument('--archive-dir', type=Path, help='优先复用本地已校验压缩包')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    root = dlss_runtime.runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    manifest = {'schema': 1, 'runtimes': {}}
    bundled = dlss_runtime.version_path('bundled')
    manifest['runtimes']['bundled'] = {**dlss_runtime.file_info(bundled),
        'source': '原项目 purkatyy/DLSS5- V2 发布包', 'validation': 'RTX 4090 Laptop'}
    for version, (archive_hash, dll_hash) in ASSETS.items():
        path = dlss_runtime.version_path(version)
        url = f'https://github.com/RankFTW/rhi-repo/releases/download/dlssnr-{version}/nvngx_dlssnr_{version}.zip'
        if not path.is_file() or digest(path) != dll_hash:
            if args.check_only:
                raise SystemExit('运行库缺失或校验失败：' + version)
            local = args.archive_dir / (version + '.zip') if args.archive_dir else None
            archive = local if local and local.is_file() and digest(local) == archive_hash else None
            if archive is None:
                archive = root / (version + '.zip')
                for attempt in range(3):
                    try:
                        with urllib.request.urlopen(url, timeout=60) as source, archive.open('wb') as output:
                            shutil.copyfileobj(source, output, 1024 * 1024)
                        if digest(archive) != archive_hash:
                            raise ValueError('下载文件哈希不匹配')
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2)
            if digest(archive) != archive_hash:
                raise ValueError('压缩包校验失败：' + version)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.dll.part')
            with zipfile.ZipFile(archive) as package:
                with package.open('nvngx_dlssnr.dll') as source, temporary.open('wb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            if digest(temporary) != dll_hash:
                raise ValueError('运行库校验失败：' + version)
            os.replace(temporary, path)
        info = dlss_runtime.file_info(path)
        if 86 not in info['architectures']:
            raise ValueError('运行库没有识别到 sm_86 内核：' + version)
        manifest['runtimes'][version] = {**info, 'source': url, 'archive_sha256': archive_hash,
            'validation': '包含 sm_86 内核；30 系实机待验证', 'license': 'NVIDIA 专有组件，社区适配版'}
        print('校验通过：', version, info['architectures'], flush=True)
    destination = root / 'manifest.json'
    if not args.check_only:
        temporary = destination.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, destination)
    print('运行库已准备，可断网推理。')


if __name__ == '__main__':
    main()

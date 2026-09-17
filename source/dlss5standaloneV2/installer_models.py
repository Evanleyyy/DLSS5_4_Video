"""Install verified local model files beside setup; never download implicitly."""
import hashlib
import json
import os
from pathlib import Path
import uuid

import model_assets
import sr_settings
import task_control

COMPONENTS = {'guidance': '深度与光流', 'pisa': 'PiSA-SR',
              'seedvr2': 'SeedVR2', 'vosr': 'VOSR 2.0'}


def local_candidates(source, entry):
    """Bounded, documented layouts; no parent-directory or whole-drive scan."""
    source = Path(source).resolve()
    roots = [source, source / 'models', source / 'sr-models', source / 'runtime/sr-models']
    candidates = [model_assets._inside(root, entry['path']) for root in roots]
    # Single weight files alongside setup are accepted only with the expected hash.
    candidates.extend(model_assets._inside(root, Path(entry['path']).name) for root in roots[:2])
    if entry.get('legacy_path'):
        candidates.extend(model_assets._inside(root, entry['legacy_path'])
                          for root in (source, source / '_internal'))
    return list(dict.fromkeys(candidates))


def _copy_verified(source, destination, entry, checkpoint, progress):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = model_assets._inside(destination.parent, destination.name + '.install-' + uuid.uuid4().hex + '.part')
    digest, count = hashlib.sha256(), 0
    try:
        with source.open('rb') as reader, temporary.open('xb') as writer:
            while True:
                checkpoint()
                block = reader.read(8 * 1024 * 1024)
                if not block:
                    break
                writer.write(block)
                digest.update(block)
                count += len(block)
                if count > entry['size']:
                    raise ValueError('本地模型在复制过程中改变，请重新检查。')
                progress(count, entry['size'])
        if count != entry['size'] or digest.hexdigest() != entry['sha256']:
            raise ValueError('本地模型复制校验失败，未替换已有文件。')
        checkpoint()
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def install_local(source, engines=None, settings=None, progress=None, checkpoint=None, extra_roots=()):
    """Reuse installed files, copy local matches, report missing files; offline only."""
    checkpoint = checkpoint or task_control.checkpoint
    engines = list(dict.fromkeys(engines or COMPONENTS))
    if set(engines) - COMPONENTS.keys():
        raise ValueError('未知的安装模型组件')
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError('安装包所在目录无效')
    root = sr_settings.model_root(settings)
    entries = model_assets.assets(engines)
    result = {'source': str(source), 'destination': str(root), 'reused': 0, 'copied': 0,
              'missing': [], 'components': {key: {'name': COMPONENTS[key], 'total': 0,
              'reused': 0, 'copied': 0, 'missing': [], 'missing_bytes': 0} for key in engines}}
    with model_assets._locked(root):
        for index, entry in enumerate(entries):
            checkpoint()
            component = result['components'][entry['path'].split('/')[0]]
            component['total'] += 1
            def report(message):
                if progress:
                    progress(index, len(entries), message + '：' + entry['path'])
            report('检查已安装模型')
            destination = model_assets._inside(root, entry['path'])
            installed = [destination]
            if entry.get('legacy_path'):
                import sys
                bundle = Path(getattr(sys, '_MEIPASS', Path(model_assets.__file__).resolve().parent))
                installed.append(model_assets._inside(bundle, entry['legacy_path']))
            if any(model_assets._valid(path, entry, checkpoint) for path in installed):
                result['reused'] += 1
                component['reused'] += 1
                continue
            report('查找安装包旁的模型')
            candidates = local_candidates(source, entry)
            candidates.extend(model_assets._inside(Path(path), entry['path']) for path in extra_roots)
            for candidate in dict.fromkeys(candidates):
                if model_assets._valid(candidate, entry, checkpoint):
                    _copy_verified(candidate, destination, entry, checkpoint,
                        lambda done, total: report(f'安装本地模型 {done / 1048576:.0f}/{total / 1048576:.0f} MB'))
                    model_assets._valid(destination, entry, checkpoint)
                    result['copied'] += 1
                    component['copied'] += 1
                    break
            else:
                result['missing'].append(entry['path'])
                component['missing'].append(entry['path'])
                component['missing_bytes'] += entry['size']
    if progress:
        progress(len(entries), len(entries), '本地模型检查完成')
    record = sr_settings.data_root() / 'model-installation.json'
    temporary = record.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, record)
    return result

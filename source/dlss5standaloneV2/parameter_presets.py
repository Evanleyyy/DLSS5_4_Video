"""Named parameter snapshots, stored independently of media and frame caches."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import uuid

import dlss_layers
import sr_settings


def normalize_name(name):
    if not isinstance(name, str):
        raise ValueError('请输入预设名称')
    name = name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise ValueError('预设名称需为 1～80 个字符，不能包含换行或控制字符')
    return name


def normalize_parameters(parameters):
    if not isinstance(parameters, dict) or not isinstance(parameters.get('settings'), dict):
        raise ValueError('预设参数格式不正确')
    settings = dlss_layers.normalize_settings(parameters['settings'])
    # Preserve the second panel even when its processing stage is disabled.
    second = parameters.get('second_layer_parameters', settings['second_layer'] or {})
    if not isinstance(second, dict):
        raise ValueError('第二层预设参数格式不正确')
    second = dlss_layers.normalize_settings(second)
    second = {key: second[key] for key in dlss_layers.DEFAULTS}
    if settings['second_layer'] is not None:
        settings['second_layer'] = second.copy()
    return {'settings': settings, 'second_layer_parameters': second}


class PresetStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else sr_settings.data_root() / 'parameter_presets.json'

    def load(self):
        try:
            raw = self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return {'version': 1, 'active_id': None, 'presets': []}
        try:
            data = json.loads(raw)
            if data['version'] != 1 or not isinstance(data['presets'], list):
                raise ValueError('不支持的预设文件版本或格式')
            ids, names, presets = set(), set(), []
            for item in data['presets']:
                identifier = item['id']
                name = normalize_name(item['name'])
                if not isinstance(identifier, str) or not identifier or identifier in ids or name.casefold() in names:
                    raise ValueError('预设名称或编号重复')
                ids.add(identifier)
                names.add(name.casefold())
                presets.append({'id': identifier, 'name': name,
                                'parameters': normalize_parameters(item['parameters'])})
            active = data.get('active_id')
            if active is not None and active not in ids:
                raise ValueError('当前预设不存在')
            return {'version': 1, 'active_id': active, 'presets': presets}
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            raise ValueError(f'预设文件无法读取，原文件已保留：{self.path}\n{error}') from error

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # OS locks are released on process exit; the tiny lock file can stay.
        with self.path.with_suffix('.lock').open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
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
                raise ValueError('另一个窗口正在保存预设，请稍后重试') from error
            try:
                yield
            finally:
                unlock()

    def _write(self, data):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix=self.path.name + '.',
                                             suffix='.tmp', dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def save(self, name, parameters, identifier=None):
        name = normalize_name(name)
        parameters = normalize_parameters(parameters)
        with self._locked():
            data = self.load()
            if identifier is not None and not any(item['id'] == identifier for item in data['presets']):
                raise ValueError('当前预设已被删除，请新增预设')
            if any(item['name'].casefold() == name.casefold() and item['id'] != identifier
                   for item in data['presets']):
                raise ValueError('已有同名预设，请更换名称；更新当前预设请点击“保存预设”')
            item = {'id': identifier or uuid.uuid4().hex, 'name': name, 'parameters': parameters}
            if identifier is None:
                data['presets'].append(item)
            else:
                data['presets'] = [item if old['id'] == identifier else old for old in data['presets']]
            data['active_id'] = item['id']
            self._write(data)
        return data

    def activate(self, identifier):
        with self._locked():
            data = self.load()
            if not any(item['id'] == identifier for item in data['presets']):
                raise ValueError('预设已被删除，请重新选择')
            data['active_id'] = identifier
            self._write(data)
        return data

    def delete(self, identifier):
        with self._locked():
            data = self.load()
            if not any(item['id'] == identifier for item in data['presets']):
                raise ValueError('预设已被删除')
            data['presets'] = [item for item in data['presets'] if item['id'] != identifier]
            if data['active_id'] == identifier:
                data['active_id'] = None
            self._write(data)
        return data

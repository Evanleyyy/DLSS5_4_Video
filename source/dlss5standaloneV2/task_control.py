"""Cooperative task pauses, scoped to a worker thread or an offline subprocess."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time


_local = threading.local()


def current():
    return getattr(_local, 'control', None)


@contextmanager
def bind(control):
    previous = current()
    _local.control = control
    try:
        yield
    finally:
        _local.control = previous


def checkpoint():
    control = current()
    if control is not None:
        control.checkpoint()


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temporary, path)


def _read(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {} if default is None else default


class PauseControl:
    def __init__(self, changed=None):
        self._condition = threading.Condition()
        self._state = 'running'
        self._token = 0
        self._remote = None
        self._changed = changed or (lambda state: None)

    @property
    def state(self):
        with self._condition:
            return self._state

    def _set_state(self, state):
        if self._state != state:
            self._state = state
            self._changed(state)

    def _publish(self):
        if self._remote is not None:
            _write(self._remote / 'pause-request.json', {
                'requested': self._state in ('pausing', 'paused'),
                'token': self._token, 'parent_pid': os.getpid()})

    def pause(self):
        with self._condition:
            if self._state == 'running':
                self._token += 1
                self._set_state('pausing')
                self._publish()

    def resume(self):
        with self._condition:
            if self._state in ('pausing', 'paused'):
                self._set_state('running')
                self._publish()
                self._condition.notify_all()

    def finish(self):
        with self._condition:
            self._set_state('finished')
            self._publish()
            self._condition.notify_all()

    def checkpoint(self):
        with self._condition:
            while self._state in ('pausing', 'paused'):
                self._set_state('paused')
                self._condition.wait()

    @contextmanager
    def remote(self, directory):
        with self._condition:
            self._remote = Path(directory)
            self._publish()
        try:
            yield
        finally:
            with self._condition:
                self._remote = None

    def sync_remote(self):
        """A request is not a pause until the subprocess acknowledges this token."""
        with self._condition:
            if self._remote and self._state == 'pausing':
                acknowledgement = _read(self._remote / 'pause-ack.json')
                if acknowledgement.get('token') == self._token:
                    self._set_state('paused')
            return self._state == 'paused'


def _parent_alive(pid):
    if not pid:
        return True
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, int(pid))
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID; access denied is inconclusive.
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False


class FilePauseControl:
    """A worker pauses only between complete operations, never inside a GPU call."""
    def __init__(self, directory, synchronize=lambda: None, parent_pid=None):
        self.directory = Path(directory)
        self.synchronize = synchronize
        self.parent_pid = parent_pid

    def checkpoint(self):
        request_path = self.directory / 'pause-request.json'
        request = _read(request_path)
        if not _parent_alive(self.parent_pid or request.get('parent_pid')):
            raise RuntimeError('主程序已退出，停止超分任务')
        if not request.get('requested'):
            return
        self.synchronize()
        token = None
        while request.get('requested'):
            if not _parent_alive(self.parent_pid or request.get('parent_pid')):
                raise RuntimeError('主程序已退出，停止暂停中的超分任务')
            if token != request['token']:
                token = request['token']
                _write(self.directory / 'pause-ack.json', {'token': token})
            time.sleep(.1)
            request = _read(request_path, request)

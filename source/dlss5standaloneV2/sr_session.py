"""A supervised offline worker accepts sequential jobs while retaining one model."""
import json
import os
from pathlib import Path
import subprocess
import time
from contextlib import nullcontext

import task_control
from process_lifetime import owned_process


class ResidentWorker:
    def __init__(self, command, environment, directory):
        self.directory = Path(directory)
        self.log_path = self.directory / 'inference.log'
        self.log = self.log_path.open('w', encoding='utf-8')
        self.process = self.ownership = None
        (self.directory / 'active').touch()
        try:
            command = [*command, '--serve', str(self.directory), str(os.getpid())]
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            self.ownership = owned_process(command, env=environment, cwd=self.directory,
                stdout=self.log, stderr=self.log, creationflags=flags)
            self.process = self.ownership.__enter__()
        except BaseException:
            self.close()
            raise

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def run(self, request, total, progress=None):
        directory = request.parent
        result_path, error_path = directory / 'result.json', directory / 'error.txt'
        control = task_control.current()
        remote = control.remote(directory) if control is not None else nullcontext()
        with remote:
            temporary = self.directory / 'next.tmp'
            temporary.write_text(json.dumps({'request': str(request)}), encoding='utf-8')
            os.replace(temporary, self.directory / 'next.json')
            elapsed, previous_time, last = 0., time.monotonic(), None
            if progress:
                progress(0, total, '提交至本地模型会话')
            while True:
                if error_path.exists():
                    raise RuntimeError(error_path.read_text(encoding='utf-8') + '\n日志：' + str(self.log_path))
                if result_path.exists():
                    task_control.checkpoint()
                    return json.loads(result_path.read_text(encoding='utf-8'))
                if not self.alive:
                    raise RuntimeError('超分模型进程已退出，请重试。日志：' + str(self.log_path))
                now = time.monotonic()
                paused = control.sync_remote() if control is not None else False
                if not paused:
                    elapsed += now - previous_time
                previous_time = now
                if elapsed > 24 * 3600:
                    raise TimeoutError('超分处理超过 24 小时，已停止')
                if progress:
                    try:
                        value = json.loads((directory / 'progress.json').read_text(encoding='utf-8'))
                        if value != last:
                            progress(value['done'], value['total'], value['stage'])
                            last = value
                    except (OSError, ValueError, KeyError):
                        pass
                time.sleep(.05)

    def close(self):
        try:
            if self.alive:
                (self.directory / 'stop').touch()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                if self.ownership is not None:
                    self.ownership.__exit__(None, None, None)
                    self.ownership = None
                self.process = None
            finally:
                (self.directory / 'active').unlink(missing_ok=True)
                self.log.close()

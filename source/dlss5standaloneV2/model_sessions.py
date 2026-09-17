"""Application-owned models; task scopes borrow them without unloading weights."""
import atexit
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import weakref

_current = ContextVar('model_session_owner', default=None)
_owners = weakref.WeakSet()


class ModelSessions:
    def __init__(self):
        self.dlss = self.sr = None
        self.size = self.sr_key = None
        self.lock = threading.RLock()
        _owners.add(self)

    def close_dlss(self):
        with self.lock:
            live, self.dlss = self.dlss, None
            self.size = None
            if live is not None:
                live.close()

    def close_sr(self):
        with self.lock:
            worker, self.sr = self.sr, None
            self.sr_key = None
            if worker is not None:
                worker.close()

    def close(self):
        try:
            self.close_dlss()
        finally:
            self.close_sr()

    def acquire_dlss(self, width, height, settings):
        import dlss_layers
        import pipeline
        with self.lock:
            self.close_sr()
            pipeline.release_guidance_models()
            if self.dlss is not None and (self.size != (width, height) or self.dlss._closed):
                self.close_dlss()
            if self.dlss is None:
                self.dlss = dlss_layers.LayeredLive(width, height, settings)
                self.size = (width, height)
            else:
                self.dlss.update(settings)
            return _DlssLease(self, self.dlss)

    def acquire_sr(self, key, factory):
        with self.lock:
            self.close_dlss()
            if self.sr is not None and (key != self.sr_key or not self.sr.alive):
                self.close_sr()
            if self.sr is None:
                self.sr = factory()
                self.sr_key = key
            return self.sr


class _DlssLease:
    def __init__(self, owner, live):
        self.owner, self.live = owner, live

    def update(self, settings):
        self.live.update(settings)

    def process(self, *args, **kwargs):
        try:
            return self.live.process(*args, **kwargs)
        except BaseException:
            self.owner.close_dlss()
            raise

    def close(self):
        # The task is done; the application retains the native sessions.
        pass


def current():
    return _current.get()


@contextmanager
def bind(owner):
    token = _current.set(owner)
    try:
        yield owner
    finally:
        _current.reset(token)


def acquire_dlss(width, height, settings):
    owner = current()
    if owner is not None:
        return owner.acquire_dlss(width, height, settings)
    import dlss_layers
    return dlss_layers.LayeredLive(width, height, settings)


def release_for_guidance():
    # A new auxiliary model needs GPU memory; cached depth/flow needs no eviction.
    owner = current()
    if owner is not None:
        owner.close()


def close_all():
    for owner in list(_owners):
        try:
            owner.close()
        except Exception:
            pass


atexit.register(close_all)

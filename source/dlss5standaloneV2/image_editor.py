"""Image-space selection and viewport geometry, independent of Tk and GPU state."""
import zlib

import cv2
import numpy as np


class Viewport:
    def __init__(self):
        self.reset()

    def reset(self):
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0

    def transform(self, width, height, canvas_width, canvas_height):
        scale = min(canvas_width / width, canvas_height / height) * self.zoom
        return scale, (canvas_width - width * scale) / 2 + self.pan_x, (canvas_height - height * scale) / 2 + self.pan_y

    def zoom_at(self, factor, x, y, width, height, cw, ch):
        scale, ox, oy = self.transform(width, height, cw, ch)
        ix, iy = (x - ox) / scale, (y - oy) / scale
        self.zoom = max(0.1, min(32.0, self.zoom * factor))
        new_scale = min(cw / width, ch / height) * self.zoom
        self.pan_x = x - ix * new_scale - (cw - width * new_scale) / 2
        self.pan_y = y - iy * new_scale - (ch - height * new_scale) / 2


class SelectionMask:
    """Keep the original hard selection; feathering is reversible and cached."""
    def __init__(self, width, height):
        self.data = np.zeros((height, width), np.uint8)
        self.undo_stack = []
        self.redo_stack = []
        self.revision = 0
        self._cached_key = None
        self._cached_alpha = None
        self._stroke_before = None

    def _snapshot(self):
        return zlib.compress(self.data.tobytes(), 1)

    def _remember(self, stack, item):
        stack.append(item)
        # Bound history even for large photographs or imported noisy masks.
        while len(stack) > 20 or (len(stack) > 1 and sum(map(len, stack)) > 64 * 1024**2):
            stack.pop(0)

    def begin_stroke(self):
        self._stroke_before = self._snapshot()

    def end_stroke(self):
        if self._stroke_before is not None:
            if self._stroke_before != self._snapshot():
                self._remember(self.undo_stack, self._stroke_before)
                self.redo_stack.clear()
            self._stroke_before = None

    def paint(self, start, end, diameter, erase=False):
        h, w = self.data.shape
        radius = max(1, int(round(diameter / 2)))
        start, end = tuple(map(lambda v: int(round(v)), start)), tuple(map(lambda v: int(round(v)), end))
        # Clip before passing user-controlled coordinates to OpenCV.
        hit, p1, p2 = cv2.clipLine((-radius, -radius, w + 2 * radius, h + 2 * radius), start, end)
        if not hit:
            return
        color = 0 if erase else 255
        cv2.line(self.data, p1, p2, color, 2 * radius, cv2.LINE_8)
        cv2.circle(self.data, p1, radius, color, -1)
        cv2.circle(self.data, p2, radius, color, -1)
        self.revision += 1

    def replace(self, value):
        self.begin_stroke()
        self.data[:] = value
        self.revision += 1
        self.end_stroke()

    def undo(self):
        return self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        return self._restore(self.redo_stack, self.undo_stack)

    def _restore(self, source, destination):
        if not source:
            return False
        self._remember(destination, self._snapshot())
        self.data[:] = np.frombuffer(zlib.decompress(source.pop()), np.uint8).reshape(self.data.shape)
        self.revision += 1
        return True

    def alpha(self, feather):
        radius = max(0, int(round(float(feather))))
        key = self.revision, radius
        if key != self._cached_key:
            if radius:
                # Replicate at the photograph boundary so a full selection stays full.
                self._cached_alpha = cv2.GaussianBlur(self.data, (2 * radius + 1, 2 * radius + 1),
                                                     max(radius / 3, 0.1), borderType=cv2.BORDER_REPLICATE)
            else:
                self._cached_alpha = self.data.copy()
            self._cached_key = key
        return self._cached_alpha


def blend_selection(original, processed, alpha, protect=False):
    """Blend in bounded row blocks; alpha never includes the display overlay."""
    if original.shape != processed.shape or alpha.shape != original.shape[:2]:
        raise ValueError("Image and selection dimensions must match")
    result = np.empty_like(original)
    for y in range(0, original.shape[0], 128):
        a = alpha[y:y + 128, :, None].astype(np.float32) / 255.0
        if protect:
            a = 1.0 - a
        result[y:y + 128] = np.rint(original[y:y + 128] * (1.0 - a) + processed[y:y + 128] * a).clip(0, 255).astype(np.uint8)
    return result

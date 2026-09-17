"""Restore local source color while retaining the renderer's fine spatial detail."""
import math

import cv2
import numpy as np


MASK_SCOPES = {'result': '整个处理结果', 'color': '仅原色彩保留（DLSS）'}


def normalize(settings=None):
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError('原色彩保留参数必须是一组设置')
    strength = float(settings.get('strength', 0))
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('原色彩保留强度必须在 0% 到 100% 之间')
    scope = settings.get('mask_scope', 'result')
    if scope not in MASK_SCOPES:
        raise ValueError('未知的原色彩保留遮罩作用')
    return {'strength': strength, 'mask_scope': scope}


def apply(original, rendered, strength):
    """Add the low-frequency source-minus-render residual, never blend source texture.

    RGB and BGR share the same channel-independent operation. A halo around each
    row block makes filtering identical across block boundaries with bounded scratch
    memory. Transparent source pixels do not contribute hidden RGB to the correction.
    """
    strength = normalize({'strength': strength})['strength']
    if (not isinstance(original, np.ndarray) or not isinstance(rendered, np.ndarray)
            or original.shape != rendered.shape or original.ndim != 3
            or original.shape[2] not in (3, 4) or min(original.shape[:2]) < 1
            or original.dtype != np.uint8 or rendered.dtype != np.uint8):
        raise ValueError('颜色保留需要尺寸一致的 RGB/BGR 或 RGBA/BGRA 图像')
    if strength == 0:
        return rendered
    height, width = original.shape[:2]
    sigma = max(2.0, min(16.0, min(height, width) / 128.0))
    radius = math.ceil(3 * sigma)
    kernel = (2 * radius + 1,) * 2
    output = rendered.copy()
    for top in range(0, height, 128):
        bottom = min(height, top + 128)
        lo, hi = max(0, top - radius), min(height, bottom + radius)
        residual = original[lo:hi, :, :3].astype(np.float32) - rendered[lo:hi, :, :3]
        if original.shape[2] == 4:
            alpha = original[lo:hi, :, 3].astype(np.float32) / 255
            residual *= alpha[..., None]
            coverage = cv2.GaussianBlur(alpha, kernel, sigma, borderType=cv2.BORDER_REPLICATE)
        correction = cv2.GaussianBlur(residual, kernel, sigma, borderType=cv2.BORDER_REPLICATE)
        if original.shape[2] == 4:
            correction /= np.maximum(coverage[..., None], 1e-6)
        block = correction[top-lo:bottom-lo]
        output[top:bottom, :, :3] = np.rint(
            rendered[top:bottom, :, :3].astype(np.float32) + strength * block
        ).clip(0, 255).astype(np.uint8)
    if original.shape[2] == 4:
        output[..., 3] = original[..., 3]
    return output

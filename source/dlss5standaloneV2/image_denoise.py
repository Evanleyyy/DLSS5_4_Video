"""Optional spatial denoising for one RGBA frame; no temporal or disk cache."""
import cv2
import numpy as np


DEFAULTS = {'enabled': False, 'luma': 3.0, 'chroma': 3.0, 'weight': 1.0}


def normalize_settings(settings=None):
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError('降噪参数必须是一组设置')
    result = {key: settings.get(key, value) for key, value in DEFAULTS.items()}
    if result['enabled'] not in (False, True):
        raise ValueError('降噪开关必须为启用或关闭')
    result['enabled'] = bool(result['enabled'])
    for key, high in (('luma', 30), ('chroma', 30), ('weight', 1)):
        value = float(result[key])
        if not np.isfinite(value) or not 0 <= value <= high:
            raise ValueError('降噪参数超出范围：' + key)
        result[key] = value
    return result


def apply_rgba(rgba, settings):
    """Blend NLM with this stage's input, preserving alpha and the input array."""
    if (not settings['enabled'] or settings['weight'] == 0 or
            settings['luma'] == settings['chroma'] == 0):
        return rgba
    bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
    filtered = cv2.fastNlMeansDenoisingColored(
        bgr, None, settings['luma'], settings['chroma'], 7, 21)
    rgb = cv2.cvtColor(filtered, cv2.COLOR_BGR2RGB)
    output = rgba.copy()
    weight = settings['weight']
    if weight == 1:
        output[..., :3] = rgb
    else:
        output[..., :3] = np.rint(rgba[..., :3].astype(np.float32) * (1 - weight) +
                                  rgb.astype(np.float32) * weight).clip(0, 255).astype(np.uint8)
    return output

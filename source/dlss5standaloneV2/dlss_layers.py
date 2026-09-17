"""Two sequential DLSS passes with independent native sessions and a final blend."""
import json

import numpy as np
import dlss_engine
import image_denoise
import sr_settings
import task_control
import dlss_runtime
import runtime_session
import color_preservation

DEFAULTS = {'preset': 1, 'style': 0, 'intensity': 1.0, 'local_tone': 1.0,
            'local_struct': 1.0, 'skin_struct': 1.0, 'use_auto_mask': 1,
            'ui_correction': 0, 'guidance_mode': 0, 'depth_convention': 2,
            'motion_scale_x': 1.0, 'motion_scale_y': 1.0, 'runtime_version': 'auto'}


def normalize_settings(settings=None):
    source = settings or {}
    def layer(values):
        result = {key: values.get(key, value) for key, value in DEFAULTS.items()}
        result['runtime_version'] = dlss_runtime.normalize_version(result['runtime_version'])
        for key, high in [('preset', 3), ('style', 3), ('use_auto_mask', 1),
                          ('ui_correction', 1), ('guidance_mode', 3), ('depth_convention', 2)]:
            value = result[key]
            if value != int(value) or not (1 if key == 'preset' else 0) <= value <= high:
                raise ValueError('DLSS 参数超出范围：' + key)
            result[key] = int(value)
        for key, high in [('intensity', 1), ('local_tone', 5), ('local_struct', 5),
                          ('skin_struct', 5), ('motion_scale_x', 2), ('motion_scale_y', 2)]:
            value = float(result[key])
            if not np.isfinite(value) or not 0 <= value <= high:
                raise ValueError('DLSS 参数超出范围：' + key)
            result[key] = value
        return result
    result = layer(source)
    result['second_layer'] = layer(source['second_layer']) if source.get('second_layer') is not None else None
    weight = float(source.get('overall_weight', 1.0))
    if not np.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError('整体权重必须在 0% 到 100% 之间')
    result['overall_weight'] = weight
    result['color_preservation'] = color_preservation.normalize(source.get('color_preservation'))
    for key in ('input_denoise', 'output_denoise'):
        result[key] = image_denoise.normalize_settings(source.get(key))
    result['super_resolution'] = sr_settings.normalize(source.get('super_resolution'))
    return result


def image_settings(settings):
    result = normalize_settings(settings)
    result['guidance_mode'] = 0
    if result['second_layer'] is not None:
        result['second_layer']['guidance_mode'] = 0
    return result


def guidance_needs(settings):
    settings = normalize_settings(settings)
    layers = [settings] + ([settings['second_layer']] if settings['second_layer'] is not None else [])
    if settings['overall_weight'] == 0 or settings['super_resolution']['engine'] != 'dlss':
        return False, False
    return (any(layer['guidance_mode'] in (2, 3) for layer in layers),
            any(layer['guidance_mode'] in (1, 3) for layer in layers))


def settings_key(settings):
    normalized = normalize_settings(settings)
    # Disabled controls remain in presets, but cannot invalidate rendered pixels.
    for key in ('input_denoise', 'output_denoise'):
        denoise = normalized[key]
        if (not denoise['enabled'] or denoise['weight'] == 0 or
                denoise['luma'] == denoise['chroma'] == 0):
            normalized[key] = image_denoise.normalize_settings()
    if normalized['super_resolution']['engine'] == 'dlss':
        normalized['super_resolution'] = {'engine': 'dlss'}
    return json.dumps({'settings': normalized, 'runtimes': dlss_runtime.fingerprint(normalized)}, sort_keys=True)


def blend_result(original, result, weight):
    if weight == 0:
        return original.copy()
    if weight == 1:
        output = result.copy()
    else:
        output = np.rint(original.astype(np.float32) * (1 - weight) + result.astype(np.float32) * weight)
        output = np.clip(output, 0, 255).astype(np.uint8)
    output[..., 3] = original[..., 3]
    return output


class SecondPass:
    def __init__(self, width, height, settings):
        self.live = runtime_session.Live(width, height, settings)

    def process(self, rgba, flow, depth, reset, settings):
        self.live.update(settings)
        return self.live.process(rgba, flow, depth, reset)

    def close(self, force=False):
        self.live.close(force=force)


class LayeredLive:
    """Live frame interface with two passes, pre/post denoising and a final blend."""
    def __init__(self, width, height, settings=None):
        self.width, self.height = width, height
        self.settings = normalize_settings(settings)
        self.first = self.second = None
        self._reset = True
        self._closed = False

    def update(self, settings):
        if self._closed:
            raise RuntimeError('DLSS 会话已关闭')
        normalized = normalize_settings(settings)
        if normalized != self.settings:
            self._reset = True
            self.settings = normalized
        if self.second is not None and (normalized['second_layer'] is None or normalized['overall_weight'] == 0):
            self.second.close()
            self.second = None
        if self.first is not None and normalized['overall_weight'] == 0:
            self.first.close()
            self.first = None

    def process(self, rgba, motion, depth, reset=False):
        task_control.checkpoint()
        if self._closed:
            raise RuntimeError('DLSS 会话已关闭')
        if not isinstance(rgba, np.ndarray) or rgba.shape != (self.height, self.width, 4) or rgba.dtype != np.uint8:
            raise ValueError('DLSS 输入必须是尺寸匹配的 RGBA 图像')
        if self.settings['overall_weight'] == 0:
            self._reset = True
            return rgba.copy()
        first_settings = {key: self.settings[key] for key in DEFAULTS}
        try:
            prepared = image_denoise.apply_rgba(rgba, self.settings['input_denoise'])
            task_control.checkpoint()
            if self.first is None:
                self.first = runtime_session.Live(self.width, self.height, first_settings)
            else:
                self.first.update(first_settings)
            reset = bool(reset or self._reset)
            output = self.first.process(prepared, motion, depth, reset=reset)
            task_control.checkpoint()
            if self.settings['second_layer'] is not None:
                if self.second is None:
                    self.second = SecondPass(self.width, self.height, self.settings['second_layer'])
                output = self.second.process(output, motion, depth, reset, self.settings['second_layer'])
                task_control.checkpoint()
            output = image_denoise.apply_rgba(output, self.settings['output_denoise'])
            task_control.checkpoint()
            self._reset = False
            output = blend_result(rgba, output, self.settings['overall_weight'])
            return color_preservation.apply(rgba, output, self.settings['color_preservation']['strength'])
        except BaseException:
            self.close()
            raise

    def close(self):
        self._closed = True
        try:
            if self.first is not None:
                self.first.close()
                self.first = None
        finally:
            if self.second is not None:
                self.second.close()
                self.second = None

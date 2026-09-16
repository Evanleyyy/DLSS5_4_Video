"""Two sequential DLSS passes with independent native sessions and a final blend."""
import json
import multiprocessing
import os
import time

import numpy as np
import dlss_engine
import image_denoise
import sr_settings
import task_control

DEFAULTS = {'preset': 1, 'style': 0, 'intensity': 1.0, 'local_tone': 1.0,
            'local_struct': 1.0, 'skin_struct': 1.0, 'use_auto_mask': 1,
            'ui_correction': 0, 'guidance_mode': 0, 'depth_convention': 2,
            'motion_scale_x': 1.0, 'motion_scale_y': 1.0}


def normalize_settings(settings=None):
    source = settings or {}
    def layer(values):
        result = {key: values.get(key, value) for key, value in DEFAULTS.items()}
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
    return json.dumps(normalize_settings(settings), sort_keys=True)


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


def _second_worker(connection, width, height, settings, log_path):
    """The upstream DLL has one global session; isolate the second temporal history."""
    live = None
    try:
        dlss_engine.LOG_PATH = log_path
        live = dlss_engine.Live(width, height, settings)
        zeros_flow = np.zeros((height, width, 2), np.float32)
        zeros_depth = np.zeros((height, width), np.float32)
        connection.send(('ready', None))
        while True:
            command, payload = connection.recv()
            if command == 'close':
                break
            settings, rgba, flow, depth, reset = payload
            live.update(settings)
            output = live.process(rgba, zeros_flow if flow is None else flow,
                                  zeros_depth if depth is None else depth, reset=reset)
            connection.send(('result', output))
    except EOFError:
        pass
    except Exception as error:
        try:
            connection.send(('error', str(error)))
        except (EOFError, OSError):
            pass
    finally:
        try:
            if live is not None:
                live.close()
        finally:
            connection.close()


class SecondPass:
    def __init__(self, width, height, settings):
        context = multiprocessing.get_context('spawn')
        self.connection, child_connection = context.Pipe()
        log_dir = os.path.dirname(dlss_engine.LOG_PATH)
        log_path = os.path.join(log_dir, f'dlssnr_layer2_{os.getpid()}.log')
        self.process_handle = context.Process(target=_second_worker,
            args=(child_connection, width, height, settings, log_path), daemon=True)
        try:
            self.process_handle.start()
            child_connection.close()
            self._receive('ready')
        except BaseException:
            child_connection.close()
            self.close(force=True)
            raise

    def _receive(self, expected):
        deadline = time.monotonic() + 120
        while not self.connection.poll(.1):
            if not self.process_handle.is_alive():
                raise RuntimeError('第二层 DLSS 进程意外退出，请检查日志或降低处理分辨率')
            if time.monotonic() > deadline:
                self.close(force=True)
                raise RuntimeError('第二层 DLSS 响应超时，请重试或降低处理分辨率')
        try:
            status, result = self.connection.recv()
        except (EOFError, OSError) as error:
            raise RuntimeError('第二层 DLSS 连接已中断') from error
        if status != expected:
            raise RuntimeError('第二层 DLSS：' + str(result))
        return result

    def process(self, rgba, flow, depth, reset, settings):
        mode = settings['guidance_mode']
        self.connection.send(('process', (settings, rgba, flow if mode in (1, 3) else None,
                                         depth if mode in (2, 3) else None, reset)))
        return self._receive('result')

    def close(self, force=False):
        process = self.process_handle
        if process.pid is not None:
            if process.is_alive() and not force:
                try:
                    self.connection.send(('close', None))
                except (EOFError, OSError):
                    pass
                process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(10)
        self.connection.close()


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
                self.first = dlss_engine.Live(self.width, self.height, first_settings)
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
            return blend_result(rgba, output, self.settings['overall_weight'])
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

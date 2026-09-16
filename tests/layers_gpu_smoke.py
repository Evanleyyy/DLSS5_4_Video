"""Real GPU comparison against two explicitly sequential native sessions."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))


def main():
    import json
    import multiprocessing
    import numpy as np
    import dlss_engine
    import dlss_layers
    import pipeline
    directory = ROOT / 'logs/layers-gpu'
    directory.mkdir(parents=True, exist_ok=True)
    first = {'preset': 1, 'style': 0, 'guidance_mode': 0, 'local_tone': 1.3}
    second = {'preset': 2, 'style': 2, 'guidance_mode': 0, 'local_struct': 1.7}
    yy, xx = np.indices((240, 320))
    rgba = np.empty((240, 320, 4), dtype=np.uint8)
    rgba[..., 0] = (xx * 3 + yy) % 256
    rgba[..., 1] = (yy * 2 + xx) % 256
    rgba[..., 2] = (xx // 16 % 2) * 100 + 60
    rgba[..., 3] = 255
    flow = np.zeros((240, 320, 2), np.float32)
    depth = np.zeros((240, 320), np.float32)
    expected = rgba
    for setting in (first, second):
        live = dlss_engine.Live(320, 240, setting)
        try:
            expected = live.process(expected, flow, depth, reset=True)
        finally:
            live.close()
    chain = dlss_layers.LayeredLive(320, 240, {**first, 'second_layer': second})
    try:
        actual = chain.process(rgba, flow, depth, reset=True)
        error = np.abs(actual.astype(int) - expected.astype(int)).max()
        assert error <= 1, ('Two-pass mismatch', error)
        chain.update({**first, 'second_layer': second, 'overall_weight': .35})
        weighted = chain.process(rgba, flow, depth, reset=True)
        reference = dlss_layers.blend_result(rgba, expected, .35)
        assert np.abs(weighted.astype(int) - reference.astype(int)).max() <= 1
        chain.update({**first, 'second_layer': second, 'overall_weight': 0})
        np.testing.assert_array_equal(chain.process(rgba, flow, depth), rgba)
    finally:
        chain.close()
    assert not multiprocessing.active_children()
    pipeline.imwrite(str(directory / '双层结果.png'), actual[..., :3][..., ::-1])
    report = {'status': 'passed', 'sequential_reference_max_error': int(error),
              'overall_weight_35_percent': True, 'zero_weight_original': True,
              'worker_closed': True}
    (directory / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

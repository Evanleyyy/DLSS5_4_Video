"""Same resident PiSA weights must survive both parameter edits and tiled runs."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source/dlss5standaloneV2'))


def main():
    import numpy as np
    import psutil
    import model_sessions
    import sr_backend
    import sr_settings
    directory = ROOT / 'logs' / ('pisa-residency-' + str(time.time_ns()))
    os.environ['DLSS5_DATA_ROOT'] = str(directory)
    owner = model_sessions.ModelSessions()
    source = np.random.default_rng(42).integers(0, 256, (192, 256, 3), dtype=np.uint8)
    outputs, pids = [], []
    try:
        with model_sessions.bind(owner):
            for tile, pixel, semantic in ((256, 1., 1.), (320, .5, .8), (256, 1., 1.)):
                settings = {'super_resolution': sr_settings.normalize(dict(engine='pisa', tile=tile,
                            pisa_pixel=pixel, pisa_semantic=semantic))}
                outputs.append(sr_backend.process_image(source, settings))
                pids.append(owner.sr.process.pid)
        assert len(set(pids)) == 1, '同一模型改参数发生重新加载'
        assert np.any(outputs[0] != outputs[1]), '新参数没有生效'
        assert np.array_equal(outputs[0], outputs[2]), '恢复参数后结果不同，模型有残留状态'
    finally:
        owner.close()
    assert not any(psutil.pid_exists(pid) for pid in pids)
    result = dict(status='passed', one_model=True, parameter_change_effective=True,
                  restored_parameters_identical=True, no_remaining_workers=True)
    (directory / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

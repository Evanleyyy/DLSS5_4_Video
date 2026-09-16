from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'source/dlss5standaloneV2'), str(ROOT / 'packaging')]


def main():
    import json
    import tkinter as tk
    import gui
    from layers_verification import verify_layers
    directory = ROOT / 'logs/gui-layers'
    root = tk.Tk()
    try:
        app = gui.App(root)
        result = verify_layers(app, directory, ROOT / 'logs/exe-verification/独立程序测试.mp4', screenshots=True)
        (directory / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        root.destroy()


if __name__ == '__main__':
    main()

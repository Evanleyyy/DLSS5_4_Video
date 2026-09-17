"""Repair only this copied project's Python home after moving the folder."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    python_home = ROOT / 'runtime/python'
    config = ROOT / '.venv/pyvenv.cfg'
    if not (python_home / 'python.exe').is_file() or not config.is_file():
        raise SystemExit('缺少完整 Python 运行环境，请复制完整项目目录')
    lines = config.read_text(encoding='utf-8-sig').splitlines()
    lines = [line for line in lines if not line.startswith('home =')]
    config.write_text('home = ' + str(python_home) + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')
    print('已更新本项目运行环境：', ROOT)


if __name__ == '__main__':
    main()

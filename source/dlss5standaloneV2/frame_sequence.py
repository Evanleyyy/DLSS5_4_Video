"""Check a complete image sequence before publishing or encoding its frames."""
from pathlib import Path
from PIL import Image
import task_control


def validate_frames(directory, count, expected_size, verify=False):
    directory = Path(directory)
    width, height = expected_size
    for index in range(count):
        task_control.checkpoint()
        path = directory / f'{index:06d}.png'
        try:
            with Image.open(path) as image:
                size = image.size
                if verify:
                    image.verify()
        except (OSError, ValueError, SyntaxError) as error:
            raise ValueError(f'处理结果第 {index + 1} 帧缺失或损坏：{path.name}') from error
        if size != (width, height):
            raise ValueError(f'处理结果第 {index + 1} 帧（{path.name}）尺寸不一致：'
                             f'应为 {width}×{height}，实际 {size[0]}×{size[1]}')

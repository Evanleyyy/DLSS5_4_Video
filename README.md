# DLSS5 Standalone

面向 Windows 的本地图片与视频处理工具，基于 [purkatyy/DLSS5- 的 V2 发布包](https://github.com/purkatyy/DLSS5-/releases/tag/dlss5v2)进行稳定性修复和界面扩展。

当前版本 **0.3.6**：新增可自定义名称的参数预设，提供新增、保存、应用和删除按钮，支持改名与重启恢复。完整保存双层 DLSS、前后降噪、整体权重及超分设置，关闭的功能也保留参数。已有 0.3.0～0.3.5 安装版可直接使用累计更新包，保留导出缓存修复、生成暂停及空格播放功能。详见[参数预设操作说明](docs/参数预设操作说明.md)。

## 功能

- **分类界面**：素材、超分、参数、遮罩、导出、缓存、日志；支持窗口自适应、控件伸缩换行和分类滚动。
- **参数预设**：自定义名称、另存为新预设、保存修改、改名、应用及删除；保存整套参数并在重启时恢复，数据位于安装目录 `data/parameter_presets.json`。
- **暂停／继续生成**：支持图片、视频、深度、光流、扩散超分和导出；区分等待暂停与已暂停，继续保留进度，任务结束后恢复按钮状态。
- **图片预览**：滚轮缩放、拖动、原始大小、适应窗口及原图/结果对比。
- **局部遮罩**：画笔、橡皮、矩形选区、撤销/重做、反选和边缘羽化；默认只处理涂抹区域。
- **处理流程**：单图与批量图片 DLSS、视频 DLSS、Depth Anything V2 深度及 RAFT 光流，逐帧处理并缓存结果。
- **双层 DLSS**：第二层连续处理第一层结果，两层预设和完整参数独立；整体权重 0–100% 混合最终效果与原图，兼容局部遮罩和导出。详见[双层操作说明](docs/双层DLSS操作说明.md)。
- **前后独立降噪**：DLSS 前与全部 DLSS 层之后分别提供开关、亮度/色彩强度及 0–100% 权重，默认关闭；复用 OpenCV，无需额外模型。详见[降噪操作说明](docs/前后降噪操作说明.md)。
- **多通道导出**：输出类型可选 PNG 图片或 MP4 视频，勾选原图、DLSS、深度、光流可视化或局部遮罩后分别保存。
- **输出范围**：视频可提取当前帧或全部帧序列；单张图片可生成指定时长和帧率的静态视频。
- **安装包部署**：包含 Python、CUDA 运行库、三套超分模型、FFmpeg 和界面，支持选择安装位置、模型组件、快捷方式、升级与卸载。

## 仓库内容

仓库保存源码、测试、依赖版本、使用文档和打包脚本。模型权重、DLL、Python 运行环境、EXE、缓存和测试生成文件不进入 Git。

```text
source/dlss5standaloneV2/   应用及模型结构源码
packaging/                 独立 EXE 启动器、构建配置及打包后验证
tests/                     回归与界面测试
docs/                      图片编辑与多通道导出说明
launch.py                  本地启动入口
requirements-local.lock.txt
打包EXE.ps1
```

## 从源码启动

已验证环境：Windows 64 位、Python 3.12.10、PyTorch 2.8.0 + CUDA 12.8、Torchvision 0.23.0，以及 RTX 4090 Laptop GPU。需要兼容的 NVIDIA 显卡驱动。

### 1. 创建独立 Python 环境

在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements-local.lock.txt
```

### 2. 准备运行资源

从上游发布包及对应组件来源取得下列资源，放入指定位置：

| 资源 | 项目内路径 |
|---|---|
| 宿主 DLL | `source/dlss5standaloneV2/dlssnr_host.dll` |
| DLSS 运行库 | `source/dlss5standaloneV2/nvngx_dlssnr.dll` |
| Depth Anything V2 Large 权重 | `source/dlss5standaloneV2/models/checkpoints/depth_anything_v2_vitl.pth` |
| RAFT Large 权重 | `source/dlss5standaloneV2/torch_home/hub/checkpoints/raft_large_C_T_SKHT_V2-ff5fadd5.pth` |
| 支持 H.264/AAC 的 FFmpeg | `runtime/ffmpeg.exe` |

本仓库保留模型结构代码，不包含模型权重。原发布包的二进制校验信息及审查范围见[代码审查与部署说明](代码审查与部署说明.md)。

### 3. 启动

双击 `启动DLSS5.bat`，或运行：

```powershell
.\.venv\Scripts\python.exe launch.py
```

程序在本地处理素材；使用日志位于 `logs/`。

## 使用文档

- [界面、缩放、遮罩与羽化](docs/界面与遮罩操作说明.md)
- [图片、视频与多通道导出](docs/多通道导出说明.md)
- [前置、后置降噪与权重](docs/前后降噪操作说明.md)
- [本地超分、模型选择与安装](docs/本地超分操作说明.md)
- [暂停、继续生成与更新安装](docs/暂停生成操作说明.md)
- [空格播放／暂停快捷键](docs/空格播放操作说明.md)
- [安装包及旧版 EXE 构建与验证](packaging/打包说明.md)

局部手绘遮罩作用于当前单张图片；静态视频重复显示图片，不生成新的运动。单图没有帧间光流，对应导出选项会置灰。

## 测试

环境与 FFmpeg 就绪后运行回归测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe tests\gui_editor_smoke.py
```

真实 GPU 流程与导出验证：

```powershell
.\.venv\Scripts\python.exe tests\smoke_local.py
.\.venv\Scripts\python.exe tests\gui_smoke.py
.\.venv\Scripts\python.exe tests\gui_export_smoke.py
```

后两项 GPU 界面测试使用第一项测试生成的本地素材。回归测试还覆盖手动缓存清理、原始素材及导出保护、处理互斥。`tests/gui_cache_smoke.py` 验证缓存界面，`tests/verify_launcher.py` 验证真实启动器的多开和关闭清理流程。测试生成文件保留在本地并由 `.gitignore` 排除。

双层 GPU 数值验证使用 `tests/layers_gpu_smoke.py`；双层界面、遮罩导出、批量图片及视频验证使用 `tests/gui_layers_smoke.py`，后者使用独立 EXE 自检生成的短视频。前后降噪验证使用 `tests/gui_denoise_smoke.py`。可选超分引擎、透明度及 GUI 导出使用 `tests/gui_sr_smoke.py`；1080p、四倍批量图片、连续帧视频与音频使用 `tests/sr_delivery_checks.py`；0.3.0 完整安装升级使用 `tests/verify_installer.py`。暂停与继续使用 `tests/gui_pause_smoke.py`，安装版支持 `--verify-pause` 自检。当前回归测试共 62 项，另有预设与播放等真实界面测试。

## 打包

按照[打包说明](packaging/打包说明.md)准备依赖和 Inno Setup。`打包安装包.ps1 -UpdateOnly` 生成 `output/DLSS5_Update_0.3.6.exe`，用于已有 0.3.0～0.3.5 安装版；不带参数可以构建 0.3.6 完整包。本次提供 0.3.6 累计更新包，新电脑先使用现有 0.3.0 完整包及全部同名 `.bin` 数据分卷。预设测试为 `tests/test_parameter_presets.py`、`tests/gui_presets_smoke.py`，安装版使用 `--verify-presets`。最小焦点回归测试为 `tests/shortcut_focus_smoke.py`，Windows 原生按键回归为 `tests/native_playback_smoke.py`；完整播放快捷键测试为 `tests/gui_playback_smoke.py`，安装版使用 `--verify-playback`。

旧版 `打包EXE.ps1` 与 `output/DLSS5_Standalone.exe` 保留用于回退，不包含本版超分功能。

## 缓存管理

主界面“缓存”分类提供占用统计与勾选清理。安装版直接使用已安装运行库，模型不作为缓存；超分临时文件在安装目录的 `data/sr-cache`，可手动清理。原视频旁的深度、光流及处理结果帧可单独清理，原始素材及导出文件保留。旧单 EXE 的运行环境清理机制见[缓存管理说明](docs/缓存管理说明.md)。

## 来源与许可证

保留[上游 MIT 许可证](LICENSE)。随附的 Depth Anything V2 / DINOv2 结构代码保留各自版权信息及 [Apache 2.0 许可证](source/dlss5standaloneV2/models/depth_anything_v2/LICENSE)。详情见[第三方来源说明](THIRD_PARTY_NOTICES.md)。

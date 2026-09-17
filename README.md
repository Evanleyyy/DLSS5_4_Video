# DLSS5 Standalone

面向 Windows 的本地图片与视频处理工具，基于 [purkatyy/DLSS5- 的 V2 发布包](https://github.com/purkatyy/DLSS5-/releases/tag/dlss5v2)进行稳定性修复和界面扩展。

当前代码版本 **0.4.4**：取消确认按钮，修改参数后自动渲染；视频立即更新当前帧预览，导出时处理完整视频。继续复用同一模型，见 [0.4.4 更新说明](docs/0.4.4更新说明.md)。安装路径选择及同目录模型识别继续保留。0.4.1 已把 30 系分支的兼容运行库选择整合回原项目。30 系优先自动选择 SF-v2，40 系保留原版运行库；两层可分别选择并保存到参数预设。原项目名称、安装 AppId、默认目录、用户设置和其他超分功能继续沿用。详见 [0.4.1 更新说明](docs/0.4.1更新说明.md)。

**30 系仍为实验支持。** 目前实机验证设备为 RTX 4090 Laptop，尚未在 3060/3070/3080/3090 上验收。原版 0.3.7 的[固定源码快照](https://github.com/Evanleyyy/DLSS5_4_Video/tree/b1e2ec7fd64e5de274dd2ad14980598bd6df8c07)、独立 [30 系分支](https://github.com/Evanleyyy/DLSS5_4_Video/tree/codex/rtx30-offline)及完整历史继续保留。

## 模型版本选择

双击 `启动DLSS5.bat`，进入“参数”页，在第一层或第二层选择“DLSS 模型版本”：

| 选项 | 行为 |
|---|---|
| 自动选择 | 30 系选择 SF-v2，40 系使用原版 |
| 原版 310.8.0 | 保留原来的 40 系渲染路径 |
| 310.8.SF-v2 | 30 系优先使用的社区适配版，实验支持 |
| 310.8.SF | 旧版兼容库，可供对照和回退 |

点击“自检此层模型”可实际渲染三帧。旧预设未指定模型时自动使用“自动选择”，其余参数保持兼容。切换运行库会重建隔离渲染进程，并使对应帧缓存失效。安装与资源准备见 [模型版本选择与安装](docs/模型版本选择与安装.md)。

Git 源码不包含 DLL。已有本地资源就绪后可运行：

```powershell
.\.venv\Scripts\python.exe tools/prepare_dlss_runtimes.py
.\.venv\Scripts\python.exe tools/check_dlss_runtime.py --version auto --render
```

准备工具只获取两个固定的兼容库发布附件，不下载上游完整仓库；校验通过后推理可离线运行。其他模型继续采用本地优先、缺失时按需准备的流程。

## 即时渲染

图片导入、修改参数或应用预设后自动渲染；快速拖动滑块会合并短时间内的变化，处理中继续调参只保留最新设置。视频调参和定位只更新当前帧预览，导出时处理整段视频。批量图片选择文件夹后自动开始。详见 [0.4.4 操作说明](docs/0.4.4更新说明.md)。

已修复模型退出时的额外等待、相同视频重复确认后重算，以及关闭功能的参数使缓存失效的问题；同一图片的深度结果也可复用。各阶段耗时、启用条件和实测范围见 [处理性能排查说明](docs/处理性能排查说明.md)。

同一模型的普通参数变化不会重复加载模型。DLSS 原生预设编号或输入尺寸变化仍需重新初始化；模型切换、退出以及为新引导模型腾出显存时释放会话，具体见 [模型驻留说明](docs/0.4.3更新说明.md)。

## DLSS 原色彩保留

“参数”页新增 0～100% 原色彩保留滑块，尽量恢复原素材的局部颜色和大范围明暗，同时保留模型细纹理。单图在“遮罩”页可选择遮罩控制整个处理结果，或只控制颜色保留；两种模式均支持反向保护和羽化。调整颜色或选区后自动更新；仅改颜色或遮罩时可复用已有 DLSS 模型结果。图片导出使用最新已完成的预览结果。

原版、SF-v2、SF 和双层 DLSS 均可使用；视频、批量图片使用全画面强度。默认 0%，兼容旧预设。详见 [DLSS 原色彩保留说明](docs/DLSS原色彩保留说明.md)。0.4.1 安装包包含此功能；旧版安装包不包含。

## 下载与安装

下列链接是已发布的 **0.3.7** 安装包，不包含 0.4.1 新增的 30 系能力。0.4.4 安装包已在本地 `output` 生成，尚未作为 GitHub Release 附件发布；也可按 [打包说明](packaging/打包说明.md)自行构建。

[下载 0.3.7 安装包](https://github.com/Evanleyyy/DLSS5_4_Video/releases/tag/v0.3.7)

- **首次安装**：下载 `DLSS5_Setup_0.3.7.exe` 和全部 4 个同名 `.bin` 分卷，放在同一目录后运行安装器。完整包约 **6.54 GB**，包含程序和运行库，不含模型权重。
- **已有安装**：0.3.0～0.3.6 用户只需约 **35 MB** 的 `DLSS5_Update_0.3.7.exe`，选择原安装目录。
- **模型准备**：先校验并复用本地文件，缺失或损坏的文件从官方源下载。模型就绪后可断网推理。
- 下载对应 `_SHA256.txt` 校验文件完整性。GitHub 的 Source code 压缩包仅含源码。

PiSA 依赖的 SD 2.1 官方源在 2026-09-17 返回 401，已有本地文件可继续复用，缺失时需先解决上游访问或选择已有模型目录。后续不再分发模型，GitHub 上旧版含模型的离线安装包已由新版替代。

0.4.1 起的安装包自动识别旁边的 `models`、`sr-models`、`runtime/sr-models` 等模型目录，校验后复制到安装目录，已有相同文件跳过；缺失时提示，点击下载后才联网。详细布局见[安装包同目录模型识别说明](docs/安装包同目录模型识别说明.md)。模型路径、官方来源和部署步骤见[模型按需部署说明](docs/模型按需部署说明.md)。

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
- **安装包部署**：包含 Python、CUDA 运行库、FFmpeg 和界面；模型先复用本地文件，缺失时从官方源下载，支持选择安装位置、模型组件、快捷方式、升级与卸载。

## 仓库内容

仓库保存源码、测试、依赖版本、使用文档和打包脚本。模型权重、DLL、Python 运行环境、EXE、缓存和测试生成文件不进入 Git。

当前以本目录作为统一主工程。旧 30 系独立版的源码差异与安装包也已集中保存，目录用途见 [源码目录整合说明](docs/源码目录整合说明.md)。

```text
source/dlss5standaloneV2/   应用及模型结构源码
packaging/                 安装包构建配置、模型清单及打包后验证
tests/                     回归与界面测试
docs/                      图片编辑与多通道导出说明
launch.py                  本地启动入口
requirements-local.lock.txt
打包安装包.ps1
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
| 支持 H.264/AAC 的 FFmpeg | `runtime/ffmpeg.exe` |

本仓库保留模型结构代码，不包含模型权重。原发布包的二进制校验信息及审查范围见[代码审查与部署说明](代码审查与部署说明.md)。

模型可在首次使用时自动准备，也可执行 `.\.venv\Scripts\python.exe tools/download_sr_assets.py --engine guidance` 提前准备深度与光流。三套超分引擎另外需要 `runtime/python`、独立推理依赖与引擎源码，构建环境见[打包说明](packaging/打包说明.md)。

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
- [模型按需下载、复用与校验](docs/模型按需部署说明.md)
- [安装包构建与验证](packaging/打包说明.md)

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

双层 GPU 数值验证使用 `tests/layers_gpu_smoke.py`；双层界面、遮罩导出、批量图片及视频验证使用 `tests/gui_layers_smoke.py`，后者使用独立 EXE 自检生成的短视频。前后降噪验证使用 `tests/gui_denoise_smoke.py`。可选超分引擎、透明度及 GUI 导出使用 `tests/gui_sr_smoke.py`；1080p、四倍批量图片、连续帧视频与音频使用 `tests/sr_delivery_checks.py`。暂停与继续使用 `tests/gui_pause_smoke.py`，安装版支持 `--verify-pause` 自检。当前回归测试共 120 项，另有预设、播放和模型下载等真实界面测试。

## 打包

按照[打包说明](packaging/打包说明.md)准备依赖和 Inno Setup。`打包安装包.ps1 -UpdateOnly` 生成 `output/DLSS5_Update_0.4.4.exe`，用于已有原项目完整安装版；不带参数生成 0.4.4 完整安装包及运行库分卷。两种包均不内嵌模型，安装时会自动检测并复制安装包旁的本地模型。离线安装检测验证使用 `--verify-installer-models`。模型下载验证使用安装版 `--verify-model-assets`，参数预设使用 `--verify-presets`，播放快捷键使用 `--verify-playback`。

旧单文件 EXE 的启动器与缓存测试保留用于兼容性验证，新版本统一通过安装包交付。

## 缓存管理

主界面“缓存”分类提供占用统计与勾选清理。安装版直接使用已安装运行库，模型不作为缓存；超分临时文件在安装目录的 `data/sr-cache`，可手动清理。原视频旁的深度、光流及处理结果帧可单独清理，原始素材及导出文件保留。旧单 EXE 的运行环境清理机制见[缓存管理说明](docs/缓存管理说明.md)。

## 来源与许可证

保留[上游 MIT 许可证](LICENSE)。随附的 Depth Anything V2 / DINOv2 结构代码保留各自版权信息及 [Apache 2.0 许可证](source/dlss5standaloneV2/models/depth_anything_v2/LICENSE)。详情见[第三方来源说明](THIRD_PARTY_NOTICES.md)。

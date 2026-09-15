# DLSS5 Standalone

面向 Windows 的本地图片与视频处理工具，基于 [purkatyy/DLSS5- 的 V2 发布包](https://github.com/purkatyy/DLSS5-/releases/tag/dlss5v2)进行稳定性修复和界面扩展。

## 功能

- **分类界面**：素材、参数、遮罩、导出、缓存、日志；支持窗口自适应、控件伸缩换行和分类滚动。
- **图片预览**：滚轮缩放、拖动、原始大小、适应窗口及原图/结果对比。
- **局部遮罩**：画笔、橡皮、矩形选区、撤销/重做、反选和边缘羽化；默认只处理涂抹区域。
- **处理流程**：单图与批量图片 DLSS、视频 DLSS、Depth Anything V2 深度及 RAFT 光流，逐帧处理并缓存结果。
- **多通道导出**：输出类型可选 PNG 图片或 MP4 视频，勾选原图、DLSS、深度、光流可视化或局部遮罩后分别保存。
- **输出范围**：视频可提取当前帧或全部帧序列；单张图片可生成指定时长和帧率的静态视频。
- **独立打包**：将 Python、CUDA 运行库、模型、FFmpeg 和界面封装成单文件 EXE。

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
- [独立 EXE 构建与验证](packaging/打包说明.md)

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

## 打包

按照[打包说明](packaging/打包说明.md)准备 PyInstaller 和 7-Zip Extra，然后运行 `打包EXE.ps1`。构建结果为 `output/DLSS5_Standalone.exe`，约 3.37 GB，首次启动解压运行资源并复用后续缓存。

## 缓存管理

主界面“缓存”分类提供占用统计、勾选清理和取消待清理任务。普通退出保留缓存；只有手动点击清理后，正在使用的运行缓存才会在相关窗口全部关闭后删除。原视频旁的深度、光流、DLSS 中间帧可以单独清理，原始素材及正常导出文件保留。详见[缓存管理说明](docs/缓存管理说明.md)。

## 来源与许可证

保留[上游 MIT 许可证](LICENSE)。随附的 Depth Anything V2 / DINOv2 结构代码保留各自版权信息及 [Apache 2.0 许可证](source/dlss5standaloneV2/models/depth_anything_v2/LICENSE)。详情见[第三方来源说明](THIRD_PARTY_NOTICES.md)。

# DLSS5 30 系离线工作台

独立开发版本 **0.4.0**，基于原 DLSS5 Standalone 0.3.7，增加面向 RTX 30 系的实验性运行库适配与模型版本选择。

| 工程 | 入口 |
|---|---|
| 30 系离线开发版 | [本分支 `codex/rtx30-offline`](https://github.com/Evanleyyy/DLSS5_Standalone/tree/codex/rtx30-offline) |
| 原版 0.3.7 工程 | [保留的源码快照](https://github.com/Evanleyyy/DLSS5_Standalone/tree/b1e2ec7fd64e5de274dd2ad14980598bd6df8c07) |

本次同步源码、工具、测试和文档；不包含模型权重、专有 DLL、Python 环境或安装包。已准备资源的本地完整目录可直接启动，单独克隆 Git 源码后仍需准备运行环境。完整更新内容见 [0.4.0 更新说明](docs/0.4.0更新说明.md)。

## 启动与选择模型

双击 `启动30系离线版.bat`。进入“参数”页，在第一层或第二层的“DLSS 模型版本”中选择：

| 选项 | 行为 |
|---|---|
| 自动选择 | 30 系选择 SF-v2；40 系选择原项目运行库 |
| 原版 310.8.0 | 原项目随附文件，本地识别到 sm_89，适用于本机 40 系环境 |
| 310.8.SF-v2 | 社区适配版，包含 sm_75 / sm_86 / sm_89 / sm_120 内核；30 系优先试用 |
| 310.8.SF | 社区旧版，可供对照和回退 |

选好后点击“自检此层模型”，程序实际生成三帧并检查输出。两层可独立选择运行库；原有 Preset #1～#3 是另外的画面模型预设，继续保留。运行库版本会随命名参数预设保存，也会单独记住最后选择。

**30 系仍标为实验支持。** 已验证的是 RTX 4090 Laptop（16 GB、驱动 596.36）上的三个运行库，不代表已验证 3060/3070/3080/3090。包含对应架构内核只是必要条件，不保证所有驱动、分辨率与显存配置都能运行。自动模式在 20/50 系也会选 SF-v2，但这两代同样未实机验收。

## 本版改动

- 三个运行库分目录保存，不覆盖原项目的文件。
- 按实际 CUDA 计算能力选择版本，读取运行库的 CUDA 内核记录，并核对 SHA-256。
- 每层使用独立渲染进程；切换版本结束旧进程，重新创建会话，不混用已加载的 DLL。
- 进程超时、初始化失败和崩溃显示日志位置；Windows 作业对象负责随主进程退出清理子进程。
- 参数预设保留模型版本；帧缓存记录运行库哈希，防止更换模型后误用旧输出。
- 深度与光流完成后，进入 DLSS 前释放对应 PyTorch 模型，减少显存并存占用。
- 保留图片、批量图片、视频、双层处理、遮罩、降噪、暂停与音轨导出功能。

低显存设备建议从 320×240 自检和单层短视频开始，再逐步提高分辨率。离线渲染允许慢速处理，但不能消除单帧显存上限。

## 文件与离线使用

```text
source/dlss5standaloneV2/nvngx_dlssnr.dll   原项目运行库
runtime/dlssnr/310.8.SF-v2/                30 系优先兼容库
runtime/dlssnr/310.8.SF/                   旧版兼容库
runtime/dlssnr/manifest.json              文件哈希、架构与来源
data/dlss-runtime.json                    最后选择的版本
data/parameter_presets.json               命名参数预设
logs/                                    原生日志与验收记录
```

本地副本已准备兼容 DLL、Python、FFmpeg 及已有模型，DLSS 推理不需要联网。复制到另一台电脑时应携带整个开发目录；虚拟环境包含本机路径，需要执行 `重定位运行环境.bat` 后再启动。也可以按既有打包流程制作安装版。

从纯源码重建时，先准备 Windows x64 / Python 3.12 环境及 `requirements-local.lock.txt` 对应依赖，并从已有原版工程复用 `dlssnr_host.dll`、原版 `nvngx_dlssnr.dll` 和 `runtime/ffmpeg.exe`。需要其他超分引擎时还需准备对应推理依赖，详见 [模型按需部署说明](docs/模型按需部署说明.md) 和 [打包说明](packaging/打包说明.md)。请在独立目录操作，保留原版工程。

在基础资源就绪后，可按下面的命令准备或检查兼容运行库。下载器仅获取 SF / SF-v2 两个指定发布附件，校验压缩包和 DLL 的固定 SHA-256，不下载上游完整仓库：

```powershell
.\.venv\Scripts\python.exe tools/prepare_dlss_runtimes.py
.\.venv\Scripts\python.exe tools/prepare_dlss_runtimes.py --check-only
```

`runtime/dlssnr/manifest.json` 由准备脚本在本地生成，记录文件哈希、架构和来源，不纳入 Git。

## 检查与命令行

```powershell
.\.venv\Scripts\python.exe tools/check_dlss_runtime.py --version auto --render
.\.venv\Scripts\python.exe tools/check_dlss_runtime.py --all
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe tests/runtime_delivery_smoke.py
.\.venv\Scripts\python.exe source/dlss5standaloneV2/pipeline.py "视频.mp4" --dlss --runtime 310.8.SF-v2 --preset 1 --export dlss
```

`--all --render` 会实际测试全部版本；30 系上原版 sm_89 运行库会被明确拒绝，属于预期行为。一般用户直接运行 `运行模型自检.bat` 即可。

## 验证与来源

86 项回归测试通过；本机三个运行库分别完成三帧自检、12 帧视频处理与带音轨导出。已验证同一会话切换版本会创建新进程、两个不同版本串联、深度与光流引导、界面模型选择和重启恢复。公开验证摘要见 [开发与验证说明](docs/30系版本开发与验证.md)；原始运行记录保存在本地 `logs/runtime-delivery.json`，不纳入 Git。

原生宿主仍使用原项目 `dlssnr_host.dll`。本版没有重写 NVIDIA 模型，也没有把闭源运行库转为开源。核心 DLL 的许可独立于本项目源码；本地开发使用不代表已确认可以公开再分发。来源见 `THIRD_PARTY_NOTICES.md`。

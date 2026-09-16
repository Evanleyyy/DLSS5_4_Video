# 第三方来源说明

## DLSS5 离线工具

- 来源：[purkatyy/DLSS5-](https://github.com/purkatyy/DLSS5-)，V2 发布附件。
- 上游许可证：[MIT](https://github.com/purkatyy/DLSS5-/blob/main/LICENSE)，原始版权声明保存在根目录 `LICENSE`。
- 本项目修改了界面布局、图片编辑、原生接口校验、任务线程管理、逐帧处理、缓存核验、导出及独立打包流程。

## Depth Anything V2 / DINOv2 结构代码

- 代码目录：`source/dlss5standaloneV2/models/depth_anything_v2/`。
- 来源：[Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2)；部分文件保留 Meta Platforms 的 DINOv2 版权声明。
- [Apache 2.0 许可证](https://github.com/DepthAnything/Depth-Anything-V2/blob/main/LICENSE)保存在该代码目录的 `LICENSE`。

## 运行依赖与二进制资源

PyTorch、Torchvision、FFmpeg、7-Zip、NVIDIA 运行库及模型权重分别遵循其组件许可证。本 Git 仓库不分发这些安装包、权重或二进制文件。

原始 DLL 的签名检查、已完成的修复和本机验证范围见根目录 `代码审查与部署说明.md`。
## 本地扩散超分组件

- PiSA-SR：<https://github.com/csslc/PiSA-SR>，上游 README 声明 Apache-2.0；权重来自作者提供的 Google Drive。代码副本与 README 随安装运行库保留。
- PiSA 基础模型 Stable Diffusion 2.1：0.3.0 的历史资产使用 `Manojb/stable-diffusion-2-1-base` 镜像，模型卡声明 OpenRAIL++。0.3.7 起自动下载只访问 PiSA 作者文档指定的 `stabilityai/stable-diffusion-2-1-base` 官方仓库；已有本地资产可继续校验复用，历史镜像不作为下载回退源。
- SeedVR2：<https://github.com/ByteDance-Seed/SeedVR>；本地消费级推理实现 <https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler>，Apache-2.0。3B FP8 与 VAE 权重来自该实现所指定的 `numz/SeedVR2_comfyUI`。
- VOSR 2.0：<https://github.com/cswry/VOSR>，Apache-2.0（上游注明的第三方例外除外）。模型、Qwen VAE 与 DINOv2 来自作者的 `CSWRY/VOSR` 权重仓库。DINOv2 许可文件随模型保留；VOSR 的第三方源码保留原版权及许可声明。
- PyTorch、Transformers、Diffusers、PEFT 及相关 Python 组件的元数据与许可随运行库保留。推理依赖版本列于 `packaging/sr-packages.lock.txt`、`packaging/pisa-packages.lock.txt`。
- 代码提交号、模型文件长度与 SHA-256 列于 `packaging/sr-assets-manifest.json`。安装器由 Inno Setup 构建。

以上模型及其外部资产遵循各自上游许可；本项目许可证不替代这些组件的许可。

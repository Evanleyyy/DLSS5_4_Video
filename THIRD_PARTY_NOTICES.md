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

# 更新记录

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的基本格式。

## [Unreleased]

### Changed

- 初始化 `main` 分支并规范化 GitHub 项目结构。
- 使用 `pyproject.toml` 管理项目元数据、依赖、入口和 pytest 配置。
- 拆分运行、开发和构建依赖，补充 wheel 构建检查。
- 重写 README，修复本机绝对路径和过时说明。
- 将优化后的使用文档整理到 `docs/`，历史文件移入忽略目录。

### Added

- GitHub Actions Windows/Python 3.11-3.12 持续集成。
- Git、编辑器、贡献、安全、行为准则、Issue 和 PR 规范文件。
- `python -m app` 入口和项目元数据一致性测试。

## [0.1.0] - 2026-03-17

### Added

- 图片和 PDF 输入。
- 题目检测、安全裁剪与动态 A4 排版。
- GUI、CLI、调试导出、模板和手动校正。
- 天津大学水印、Windows 可执行文件和安装包构建配置。

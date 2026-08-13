# TJU Homework Making

面向题目截图、扫描件、图片和 PDF 的自动整理工具。项目使用传统视觉算法检测题块，进行安全裁剪，并按统一页眉、网格和书写留白生成 A4 PDF。

[使用文档（PDF）](docs/TJU_Homework_Making_使用文档.pdf) · [贡献指南](CONTRIBUTING.md) · [更新记录](CHANGELOG.md)

![CI](https://github.com/OWNER/REPOSITORY/actions/workflows/ci.yml/badge.svg)

## 功能

- 支持 PNG、JPEG、BMP、TIFF、WebP 和 PDF 输入
- 支持多文件排序、拖拽、粘贴和 PDF 素材框选
- 题号、连通域、投影分段和网格约束等传统视觉检测
- 安全扩框、保守去白边与特殊题图边缘保护
- 动态每页题数、行模式和 PDF/图片版式模板
- 身份页眉、天津大学低透明度水印和 A4 PDF 输出
- GUI 手动校正、打回重生成和实时页面预览
- CLI、YAML/JSON 配置与完整 debug 中间产物
- PyInstaller 免安装版本和 Inno Setup 安装包构建

## 环境要求

- Windows 11（主要支持平台）
- Python 3.11 或 3.12

创建运行环境：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

国内网络可配置清华 PyPI 镜像：

```powershell
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

开发环境使用：

```powershell
python -m pip install -r requirements-dev.txt
```

## 快速开始

### GUI

源码运行：

```powershell
python -m app.gui
```

或运行统一启动脚本：

```powershell
.\launch_gui.bat
```

`launch_gui.bat` 会优先启动 `dist/pdf_exam_formatter_gui/` 中的打包版本；未找到时再使用本机 Python。

GUI 基本流程：

1. 添加、拖入或粘贴图片/PDF。
2. 选择输出目录和文件名。
3. 设置模式、DPI、每页题数、模板和页眉信息。
4. 开始处理并检查预览。
5. 必要时手动调整裁剪框，再导出最终 PDF。

### CLI

```powershell
python -m app.main `
  --input .\input1.png .\input2.pdf `
  --output .\outputs\result.pdf `
  --dpi 220 `
  --per-page 4 `
  --watermark true
```

安装项目后也可以使用：

```powershell
pdf-exam-formatter --input .\input.png --output .\outputs\result.pdf
```

常用参数：

| 参数 | 用途 |
|---|---|
| `--input` | 一个或多个图片/PDF 输入 |
| `--output` | 输出 PDF 路径 |
| `--mode` | `normal` 或 `fast` |
| `--dpi` | 图片和 PDF 光栅化 DPI |
| `--per-page` | 每页题数 |
| `--strict-single-question` | 严格单题模式 |
| `--layout-template` | PDF/图片版式模板 |
| `--template-lock-per-page` | 是否由模板锁定页面容量 |
| `--watermark` | 是否绘制天津大学水印 |
| `--debug` | 是否导出中间结果 |
| `--config` | 全局 YAML/JSON 配置 |
| `--header-config` | 单独页眉配置 |
| `--student-id`、`--class-name`、`--student-name` | 身份页眉字段 |
| `--print-config` | 打印最终配置并退出 |
| `--version` | 显示版本 |

完整参数：

```powershell
python -m app.main --help
```

## 配置

配置覆盖顺序为：

```text
内置默认值 -> --config -> --header-config -> CLI/GUI 当前输入
```

示例：

```yaml
io:
  image_dpi: 240
  pdf_dpi: 240

layout:
  problems_per_page: 4
  template_lock_per_page: true

watermark:
  enabled: true

debug:
  enabled: true
```

查看合并后的最终配置：

```powershell
python -m app.main `
  --input .\input.png `
  --output .\outputs\result.pdf `
  --config .\config.yaml `
  --print-config
```

## 处理流程

```text
读取与光栅化
  -> 预处理
  -> 候选题块检测
  -> 合并、去重和阅读顺序整理
  -> 安全裁剪
  -> 分页与版式计算
  -> 页眉和水印
  -> ReportLab PDF
```

检测不到题块时会退回整页题块，避免整次任务直接失败。检测结果不理想时，建议按下面顺序查看 debug 产物：

1. `detection_candidates/`
2. `detection_refined/`
3. `crop_contact_sheet.png`
4. `render_preview/`

## 项目结构

```text
TJU-Homework-Making/
├─ .github/workflows/       GitHub Actions
├─ .github/ISSUE_TEMPLATE/  Issue 与 PR 协作模板
├─ app/                     核心 Python 包、GUI 与 CLI
├─ assets/                  水印、字体与示例素材
├─ docs/                    使用文档
├─ tests/                   单元与回归测试
├─ build_exe.bat            PyInstaller 构建脚本
├─ build_setup.bat          Inno Setup 构建脚本
├─ pdf_exam_formatter_gui.spec
├─ pdf_exam_formatter_setup.iss
├─ pyproject.toml           项目元数据和 pytest 配置
├─ requirements.txt         运行环境安装入口
└─ requirements-dev.txt     开发和测试环境安装入口
```

以下目录是运行或构建产物，不进入 Git：

- `.cache/`
- `build/`
- `dist/`
- `dist_installer/`
- `debug/`
- `outputs/`
- `output/`
- `tmp/`

## 测试

```powershell
python -m compileall -q app
python -m pytest
python -m app.main --version
python -m app --version
python -m pip wheel --no-deps --wheel-dir tmp\wheel-check .
```

GitHub Actions 会在 Windows 上使用 Python 3.11 和 3.12 执行相同检查。

`test_reference_pdf_regression.py` 需要本机参考 PDF；未提供时会自动跳过。

## Windows 打包

构建免安装 GUI：

```powershell
.\build_exe.bat
```

输出位置：

```text
dist/pdf_exam_formatter_gui/pdf_exam_formatter_gui.exe
```

构建安装包：

```powershell
.\build_setup.bat
```

输出位置：

```text
dist_installer/pdf_exam_formatter_setup.exe
```

构建脚本可能安装 PyInstaller 或 Inno Setup，请在运行前确认当前 Python 环境和网络来源。

## 贡献与安全

- 开发流程参见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 涉及作业、学号或姓名的数据安全注意事项参见 [SECURITY.md](SECURITY.md)。
- 社区协作约定参见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 水印素材来源参见 [assets/branding/README.md](assets/branding/README.md)。

## 许可证

代码以 [MIT License](LICENSE) 发布。天津大学名称与标识不因本项目许可证而获得额外授权。

# 贡献指南

感谢参与 TJU Homework Making。

## 开发环境

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

## 修改原则

- 保持 Python 3.11+ 兼容。
- 用户界面文案可使用中文，代码标识符使用英文。
- 不提交 `dist/`、`build/`、`debug/`、`outputs/`、`.cache/` 等生成目录。
- 修改检测、裁剪或布局逻辑时，应同步增加或更新测试。
- 不在代码和文档中写入本机绝对路径。

## 提交前检查

```powershell
python -m compileall -q app
python -m pytest
python -m app.main --version
python -m app --version
python -m pip wheel --no-deps --wheel-dir tmp\wheel-check .
```

提交说明应解释修改原因。一个提交尽量只处理一个独立问题。

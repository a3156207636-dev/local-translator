# 参与贡献 / Contributing

欢迎 Issue、PR 和讨论。这个项目目前由作者个人维护，以下是一些约定。

## 开发环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

入口是 `main.py`。常用调试命令见 `README.md` 末尾的「诊断工具」与命令行示例。

## 编码约定

- Python 侧命令行输出一律用 **ASCII 键值**（方便管道 / 重定向，避免中文编码错乱）。
- 给用户看的 `.bat` / `.txt` 用 **GBK** 编码写盘（匹配简体中文控制台代码页 936）。
- 不提交 `config.json` / `license.json`（含本机配置与授权缓存，已在 `.gitignore`）。

## 许可证

贡献即表示你同意以 **AGPLv3** 发布你的修改（见 `LICENSE`）。
如果你希望以**闭源 / 商业**方式使用，请先参阅 `COMMERCIAL.md` 购买商业授权。

## 提 PR 前

1. 跑一遍自带自检：`python selftest_instant.py`、`python e2e_test.py`
2. 确保没有把个人配置 / 密钥带进来（`git status` 里不应出现 `config.json`、`license.json`）
3. 用中文或英文描述清楚改动动机

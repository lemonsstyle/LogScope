# LogScope

[English](README.en.md) | 简体中文

LogScope 是一个本地运行的只读数据库日志查询工具。它通过浏览器界面连接 MySQL、PostgreSQL 或 SQL Server，支持表单筛选和只读 `SELECT` 查询。

## 安全与隐私

- 默认只监听 `127.0.0.1`，不会对局域网开放。
- 数据库密码只保存在当前浏览器页面会话中，不写入本地配置文件。
- 本地连接配置保存在用户目录下的 `.heidisql-lite/connections.json`，只包含连接名、主机、端口、用户名、数据库等元数据。
- 不建议在不可信网络中使用 `--allow-remote`，否则其他人可能通过你的电脑发起数据库连接。

## 直接运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
pip install -r requirements.txt
python3 app.py --host 127.0.0.1 --port 8765
```

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
python app.py --host 127.0.0.1 --port 8765
```

启动后打开：

```text
http://127.0.0.1:8765
```

## 使用方式

1. 在“连接配置”中填写名称、数据库类型、主机、端口、用户名、密码和默认数据库。
2. 点击“测试连接”或“保存连接”。
3. 在“选表”中选择当前连接，点击“读取库表”。
4. 选择数据库/Schema 和表后执行查询。

说明：

- MySQL 的“数据库/Schema”列表对应 database。
- PostgreSQL 和 SQL Server 的“默认数据库”是连接目标 database，“数据库/Schema”列表对应 schema。
- 高级 SQL 只允许单条只读 `SELECT` 查询。

## Windows 打包

请在 Windows 电脑上打包 Windows 可执行文件：

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
pip install pyinstaller
python build_windows.py
```

打包成功后输出目录：

```text
dist\LogScope
```

把整个 `dist\LogScope` 文件夹复制到目标 Windows 电脑，然后运行：

```text
Start-LogScope-Console.bat
```

浏览器会自动打开 `http://127.0.0.1:8765`。这个控制台窗口关闭后服务也会停止；也可以运行 `Stop-LogScope.bat` 停止服务。

## 常用参数

```bash
python3 app.py --host 127.0.0.1 --port 8765 --data-dir ~/.heidisql-lite --query-timeout-seconds 300
```

仅在可信网络中需要开放给其他机器访问时使用：

```bash
python3 app.py --host 0.0.0.0 --port 8765 --allow-remote
```

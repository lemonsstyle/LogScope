# LogScope

English | [简体中文](README.md)

LogScope is a local, read-only database log query tool. It connects to MySQL, PostgreSQL, or SQL Server through a browser UI and supports both form-based filtering and read-only `SELECT` queries.

## Security and Privacy

- By default, LogScope only listens on `127.0.0.1` and is not exposed to your local network.
- Database passwords are kept only in the current browser page session and are never written to local configuration files.
- Local connection profiles are stored under `.heidisql-lite/connections.json` in the user's home directory. They contain only metadata such as connection name, host, port, username, and database.
- Do not use `--allow-remote` on untrusted networks. Other users may be able to make database connection attempts through your computer.

## Run from Source

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
pip install -r requirements.txt
python3 app.py --host 127.0.0.1 --port 8765
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
python app.py --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

## Usage

1. In "Connection Settings", enter the name, database type, host, port, username, password, and default database.
2. Click "Test Connection" or "Save Connection".
3. In "Select Table", choose the current connection and click "Load Databases/Tables".
4. Select a database/schema and table, then run a query.

Notes:

- For MySQL, the "Database/Schema" list represents databases.
- For PostgreSQL and SQL Server, "Default Database" is the target database for the connection, while the "Database/Schema" list represents schemas.
- Advanced SQL only allows a single read-only `SELECT` query.

## Package for Windows

Build the Windows executable on a Windows computer:

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
pip install pyinstaller
python build_windows.py
```

The output directory is:

```text
dist\LogScope
```

Copy the entire `dist\LogScope` folder to the target Windows computer, then run:

```text
Run-LogScope.bat
```

The browser opens `http://127.0.0.1:8765` automatically. Close the console window when you are done to stop the service.

## Common Options

```bash
python3 app.py --host 127.0.0.1 --port 8765 --data-dir ~/.heidisql-lite --query-timeout-seconds 300
```

Only use this on trusted networks if you need access from other machines:

```bash
python3 app.py --host 0.0.0.0 --port 8765 --allow-remote
```

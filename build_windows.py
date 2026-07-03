"""
Windows打包脚本 - 使用PyInstaller
请在Windows上运行此脚本来生成Windows可执行文件
"""
import subprocess
import sys
from pathlib import Path

def main():
    print("=" * 60)
    print("LogScope Windows 打包工具")
    print("=" * 60)

    # 检查PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("\n[错误] 未安装 PyInstaller")
        print("请运行: pip install pyinstaller")
        sys.exit(1)

    required_drivers = (
        ("PyMySQL", "pymysql"),
        ("psycopg", "psycopg"),
        ("pymssql", "pymssql"),
    )
    for display_name, module_name in required_drivers:
        try:
            __import__(module_name)
            print(f"\n✓ {display_name} 已安装")
        except ImportError:
            print(f"\n[错误] 未安装 {display_name}")
            print("请运行: pip install -r requirements.txt")
            sys.exit(1)

    # 确认项目文件存在
    project_root = Path(__file__).parent
    app_file = project_root / "app.py"
    static_dir = project_root / "static"

    if not app_file.exists():
        print(f"\n[错误] 找不到 app.py")
        sys.exit(1)

    if not static_dir.exists():
        print(f"\n[错误] 找不到 static 目录")
        sys.exit(1)

    print("\n✓ 项目文件检查通过")
    print(f"  - app.py: {app_file}")
    print(f"  - static/: {static_dir}")

    # PyInstaller命令 - 关键：添加 --hidden-import
    separator = ';' if sys.platform == 'win32' else ':'
    cmd = [
        "pyinstaller",
        "--name=LogScope",
        "--noconfirm",
        "--clean",
        f"--add-data={static_dir}{separator}{static_dir.name}",
        "--hidden-import=pymysql",  # 关键：强制包含PyMySQL
        "--hidden-import=pymysql.cursors",
        "--hidden-import=pymysql.connections",
        "--hidden-import=psycopg",
        "--hidden-import=psycopg_binary",
        "--hidden-import=pymssql",
        "--hidden-import=_mssql",
        "--collect-binaries=psycopg_binary",
        "--collect-binaries=pymssql",
        "--console",
        str(app_file)
    ]

    print("\n开始打包...")
    print(f"命令: {' '.join(cmd)}")
    print()

    try:
        subprocess.run(cmd, check=True)
        dist_dir = project_root / "dist" / "LogScope"
        print("\n" + "=" * 60)
        print("✓ 打包成功!")
        print("=" * 60)
        print(f"\n输出目录: {dist_dir}")
        print("\n下一步:")
        print("1. 测试运行 LogScope.exe")
        print("2. 将整个 LogScope 文件夹复制到Windows电脑")
        print("3. 用户双击 LogScope.exe 启动，浏览器会自动打开")
        print("4. 使用完成后关闭 LogScope.exe 的控制台窗口")
    except subprocess.CalledProcessError as e:
        print(f"\n[错误] 打包失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

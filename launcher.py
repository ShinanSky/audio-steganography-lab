"""Shared entry point for Windows and macOS launchers."""
import sys
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import messagebox


def main():
    try:
        import numpy
        if len(sys.argv) > 1 and sys.argv[1] == "reader":
            from reader import main as start
        else:
            from generator import main as start
        sys.argv = [sys.argv[0]]
        start()
    except Exception:
        report = traceback.format_exc()
        Path(__file__).with_name("startup_error.log").write_text(report, encoding="utf-8")
        root = tk.Tk()
        root.withdraw()
        installer = "安装依赖.command" if sys.platform == "darwin" else "安装依赖.cmd"
        messagebox.showerror("启动失败", f"请运行 {installer}。\n详情：startup_error.log")
        root.destroy()
        raise SystemExit(1)


if __name__ == "__main__":
    main()

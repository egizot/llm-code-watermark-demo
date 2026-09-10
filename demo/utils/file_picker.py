from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog

    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def pick_path(kind: str) -> str:
    if not TKINTER_AVAILABLE:
        return ""

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    try:
        if kind == "file":
            path = filedialog.askopenfilename(
                title="Select a Python file",
                filetypes=[("Python Files", "*.py")],
                initialdir=str(PROJECT_ROOT),
            )
        else:
            path = filedialog.askdirectory(
                title="Select a folder containing Python files",
                initialdir=str(PROJECT_ROOT),
            )
    finally:
        root.destroy()

    return path
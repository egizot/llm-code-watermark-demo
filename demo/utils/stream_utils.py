import contextlib
import difflib
import io
import queue
import threading
from pathlib import Path


class _StreamToQueue(io.TextIOBase):

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s):
        if s and s.strip():
            self.q.put(s)
        return len(s)

    def flush(self):
        pass


def run_with_live_log(func, *args, placeholder=None, max_lines=300, **kwargs):
    q: queue.Queue = queue.Queue()
    result = {}

    def target():
        stream = _StreamToQueue(q)
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                result["value"] = func(*args, **kwargs)
        except Exception as e: 
            result["error"] = e
        finally:
            q.put(None) 

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    lines = []
    while True:
        item = q.get()
        if item is None:
            break
        lines.append(item.rstrip("\n"))
        lines = lines[-max_lines:]
        if placeholder is not None:
            placeholder.code("\n".join(lines) or " ", language="text")

    thread.join()

    if "error" in result:
        raise result["error"]
    return result.get("value")


def list_py_files(root: Path):
    root = Path(root)
    if not root.exists():
        return []
    if root.is_file():
        return [(root.name, root)]
    return [(str(p.relative_to(root)), p) for p in sorted(root.rglob("*.py"))]


def diff_pairs(original_root: Path, modified_root: Path):
    original_root = Path(original_root)
    modified_root = Path(modified_root)

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace") if path and path.exists() else ""

    def make_entry(rel_path: str, orig_text: str, mod_text: str) -> dict:
        diff = "\n".join(
            difflib.unified_diff(
                orig_text.splitlines(),
                mod_text.splitlines(),
                fromfile=f"original/{rel_path}",
                tofile=f"modified/{rel_path}",
                lineterm="",
            )
        )
        return {
            "rel_path": rel_path,
            "original": orig_text,
            "modified": mod_text,
            "diff": diff,
            "changed": orig_text != mod_text,
        }

    if original_root.is_file() or (not original_root.is_dir() and modified_root.is_file()):
        orig_text = read(original_root)
        mod_text = read(modified_root)
        return [make_entry(original_root.name, orig_text, mod_text)]

    orig_files = dict(list_py_files(original_root))
    mod_files = dict(list_py_files(modified_root))

    results = []
    for rel_path in sorted(set(orig_files) | set(mod_files)):
        orig_text = read(orig_files.get(rel_path))
        mod_text = read(mod_files.get(rel_path))
        results.append(make_entry(rel_path, orig_text, mod_text))

    return results
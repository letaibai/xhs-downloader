"""小红书博主图片下载工具 - 图形界面版.

双击运行, 粘贴链接点下载即可, 无需命令行.
"""

import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, scrolledtext

import xhs_dl


class LogQueue(queue.Queue):
    """把 print 重定向到 GUI 日志区."""

    def write(self, s: str) -> None:
        if s.strip():
            self.put(s)

    def flush(self) -> None:
        pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("小红书图片下载器")
        self.geometry("680x460")
        self.minsize(560, 380)
        self.running = False
        self._build_ui()

    def _build_ui(self) -> None:
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="博主主页链接 / 单篇帖子链接 / 博主ID:").pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(4, 8))
        self.url_var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.url_var)
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", lambda e: self.start())
        self.btn = ttk.Button(row, text="开始下载", command=self.start)
        self.btn.pack(side="left", padx=(8, 0))

        ttk.Label(frm, text="日志:").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(frm, height=15, state="disabled",
                                             font=("Microsoft YaHei UI", 9))
        self.log.pack(fill="both", expand=True)

        self.status = ttk.Label(self, text="就绪", anchor="w",
                                padding=(12, 2))
        self.status.pack(fill="x")

    def log_write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip("\n") + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def start(self) -> None:
        if self.running:
            return
        target = self.url_var.get().strip()
        if not target:
            self.log_write("请先粘贴链接.")
            return
        self.running = True
        self.btn.configure(state="disabled", text="下载中...")
        self.set_status("正在下载, 请耐心等待...")
        self.log_write(f"目标: {target}")
        # print 重定向到 GUI
        logq = LogQueue()
        self._orig_stdout = sys.stdout
        sys.stdout = logq
        t = threading.Thread(target=self._worker, args=(target, logq), daemon=True)
        t.start()
        self._poll(logq)

    def _poll(self, logq) -> None:
        drained = False
        while True:
            try:
                line = logq.get_nowait()
            except queue.Empty:
                drained = True
                break
            self.log_write(line)
        if self.running:
            self.after(200, lambda: self._poll(logq))

    def _worker(self, target: str, logq) -> None:
        try:
            xhs_dl.run(target)
        except SystemExit:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            sys.stdout = self._orig_stdout
            logq.put("\n[完成] 可在 downloads/ 目录查看文件. "
                     "窗口可直接粘贴下一个链接继续下载.")
            self.after(0, self._done)

    def _done(self) -> None:
        self.running = False
        self.btn.configure(state="normal", text="开始下载")
        self.set_status("就绪")


def main() -> None:
    xhs_dl.DOWNLOAD_DIR.mkdir(exist_ok=True)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

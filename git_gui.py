"""图形化 Git：点击按钮自动执行常用 Git 命令，下方实时显示原版命令行输出。

运行方式: python git_gui.py
"""

import os
import shlex
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

DEFAULT_DIR = os.path.dirname(os.path.abspath(__file__))


def run_git(repo, args, timeout=60):
    actual = ["git", "-c", "core.quotepath=false"] + args
    display = shlex.join(["git"] + args)
    env = dict(os.environ, GIT_PAGER="cat", PAGER="cat")
    try:
        proc = subprocess.run(
            actual, cwd=repo, capture_output=True,
            encoding="utf-8", errors="replace", env=env, timeout=timeout,
        )
    except FileNotFoundError:
        return display, "未找到 git 命令，请先安装 Git。", -1
    except subprocess.TimeoutExpired:
        return display, "命令执行超时。", -1
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return display, text, proc.returncode


class GitGUI:
    def __init__(self, root):
        self.root = root
        root.title("图形化 Git")
        root.geometry("940x720")
        root.minsize(780, 560)
        self._build_top()
        self._build_buttons()
        self._build_files()
        self._build_output()
        self.refresh_status()

    def _build_top(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(top, text="仓库路径:").pack(side="left")
        self.path_var = tk.StringVar(value=DEFAULT_DIR)
        ttk.Entry(top, textvariable=self.path_var).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="浏览…", command=self.choose_dir).pack(side="left")
        ttk.Button(top, text="刷新", command=self.refresh_status).pack(
            side="left", padx=(4, 0))
        self.state_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.state_var,
                  foreground="#606060").pack(anchor="w", padx=10, pady=(2, 0))

    def _build_buttons(self):
        box = ttk.LabelFrame(self.root, text="点击按钮执行 Git 命令")
        box.pack(fill="x", padx=8, pady=4)
        rows = [
            [("查看状态", self.run_status),
             ("暂存全部", lambda: self.run(["add", "."])),
             ("暂存选中", self.add_selected),
             ("取消暂存", lambda: self.run(["reset"])),
             ("提交", self.commit),
             ("修改上次提交", self.amend)],
            [("查看日志", lambda: self.run(
                ["log", "--oneline", "--graph", "--all", "-20"])),
             ("查看分支", lambda: self.run(["branch", "-a"])),
             ("新建并切换分支", self.create_branch),
             ("切换分支", self.switch_branch),
             ("合并分支", self.merge_branch)],
            [("拉取", lambda: self.run(["pull"], slow=True)),
             ("推送", lambda: self.run(["push"], slow=True)),
             ("查看远程", lambda: self.run(["remote", "-v"])),
             ("初始化仓库", lambda: self.run(["init"], require_repo=False)),
             ("克隆仓库", self.clone)],
            [("暂存改动(stash)", lambda: self.run(["stash", "push"])),
             ("弹出暂存", lambda: self.run(["stash", "pop"])),
             ("撤销上次提交", self.reset_soft),
             ("丢弃改动", self.discard_changes),
             ("清空输出", self.clear_output)],
        ]
        for row in rows:
            frame = ttk.Frame(box)
            frame.pack(fill="x", padx=6, pady=3)
            for text, cmd in row:
                ttk.Button(frame, text=text, width=16, command=cmd).pack(
                    side="left", padx=3)

    def _build_files(self):
        box = ttk.LabelFrame(
            self.root, text="变更文件（选中后可“暂存选中”，双击查看改动）")
        box.pack(fill="x", padx=8, pady=4)
        self.branch_label = ttk.Label(box, text="当前分支: -")
        self.branch_label.pack(anchor="w", padx=6, pady=(4, 0))
        frame = ttk.Frame(box)
        frame.pack(fill="x", padx=6, pady=(2, 6))
        self.file_list = tk.Listbox(frame, height=7, font=("Consolas", 10))
        scroll = ttk.Scrollbar(frame, command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.file_list.pack(side="left", fill="x", expand=True)
        self.file_list.bind("<Double-Button-1>", self.show_diff)

    def _build_output(self):
        box = ttk.LabelFrame(self.root, text="Git 命令行输出（原版）")
        box.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.output = tk.Text(
            box, height=10, state="disabled", wrap="none", relief="flat",
            bg="#1e1e1e", fg="#dcdcdc", font=("Consolas", 10))
        scroll = ttk.Scrollbar(box, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self.output.tag_configure("cmd", foreground="#7ec8ff")
        self.output.tag_configure("ok", foreground="#6ee06e")
        self.output.tag_configure("fail", foreground="#ff7070")
        self.append_output(
            "点击上方按钮执行命令，此处显示真实的 git 命令与输出。\n\n")

    def append_output(self, msg, tag=None):
        self.output.configure(state="normal")
        if tag:
            self.output.insert("end", msg, tag)
        else:
            self.output.insert("end", msg)
        self.output.see("end")
        self.output.configure(state="disabled")

    def clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def is_repo(self, repo):
        _, _, code = run_git(repo, ["rev-parse", "--is-inside-work-tree"])
        return code == 0

    def current_branch(self, repo):
        _, out, code = run_git(repo, ["branch", "--show-current"])
        return out.strip() if code == 0 else ""

    def _local_branches(self, repo):
        _, out, code = run_git(repo, ["branch", "--format=%(refname:short)"])
        if code != 0:
            return []
        return [b.strip() for b in out.splitlines() if b.strip()]

    def refresh_status(self):
        repo = self.path_var.get().strip()
        branch, files = "（不是 Git 仓库）", []
        if repo and os.path.isdir(repo):
            _, out, code = run_git(repo, ["status", "-z", "--porcelain"])
            if code == 0:
                b = self.current_branch(repo)
                branch = b if b else "（空仓库，还没有提交）"
                files = [f"{e[:2]}  {e[3:]}"
                         for e in out.split("\x00") if len(e) > 3]
        self.branch_label.configure(
            text=f"当前分支: {branch}    变更文件: {len(files)} 个")
        self.file_list.delete(0, "end")
        for f in files:
            self.file_list.insert("end", f)

    def run(self, args, slow=False, confirm=None, repo=None,
            require_repo=True, on_success=None):
        repo = repo or self.path_var.get().strip()
        if not repo or not os.path.isdir(repo):
            messagebox.showwarning("提示", "请先在上方选择一个有效的目录。")
            return
        if require_repo and not self.is_repo(repo):
            messagebox.showwarning(
                "提示", f"“{repo}”不是 Git 仓库。\n请先点击“初始化仓库”或“克隆仓库”。")
            return
        if confirm and not messagebox.askyesno("确认操作", confirm):
            return
        threading.Thread(
            target=self._worker,
            args=(repo, args, slow, on_success), daemon=True).start()

    def _worker(self, repo, args, slow, on_success):
        try:
            self.root.after(
                0, lambda: self.state_var.set(f"执行中: git {args[0]} …"))
        except tk.TclError:
            return
        display, text, code = run_git(repo, args, timeout=600 if slow else 60)
        ok = code == 0
        stamp = time.strftime("%H:%M:%S")

        def done():
            try:
                self.append_output(f"[{stamp}] $ {display}\n", "cmd")
                self.append_output((text or "(无输出)") + "\n")
                self.append_output(
                    f"—— 结果: {'成功' if ok else '失败'} ——\n\n",
                    "ok" if ok else "fail")
                self.state_var.set("就绪")
                self.refresh_status()
                if on_success and ok:
                    on_success()
            except tk.TclError:
                pass

        try:
            self.root.after(0, done)
        except tk.TclError:
            pass

    def run_status(self):
        self.run(["status"])

    def choose_dir(self):
        d = filedialog.askdirectory(
            initialdir=self.path_var.get() or DEFAULT_DIR)
        if d:
            self.path_var.set(d)
            self.refresh_status()

    def _file_name(self, index):
        name = self.file_list.get(index)[4:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        return name

    def add_selected(self):
        sel = self.file_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在“变更文件”列表中选中文件。")
            return
        self.run(["add", "--", self._file_name(sel[0])])

    def show_diff(self, _event=None):
        sel = self.file_list.curselection()
        if not sel:
            return
        self.run(["diff", "--", self._file_name(sel[0])])

    def commit(self):
        msg = simpledialog.askstring("提交", "请输入提交信息:", parent=self.root)
        if msg is None:
            return
        if not msg.strip():
            messagebox.showwarning("提示", "提交信息不能为空。")
            return
        self.run(["commit", "-m", msg.strip()])

    def amend(self):
        if not messagebox.askyesno("修改上次提交", "把当前改动并入上一次提交？"):
            return
        msg = simpledialog.askstring(
            "修改上次提交", "输入新的提交信息（留空则保持原信息）:", parent=self.root)
        if msg is None:
            return
        args = ["commit", "--amend"]
        if msg.strip():
            args += ["-m", msg.strip()]
        else:
            args.append("--no-edit")
        self.run(args)

    def create_branch(self):
        name = simpledialog.askstring("新建分支", "请输入分支名:", parent=self.root)
        if not name or not name.strip():
            return
        self.run(["switch", "-c", name.strip()])

    def switch_branch(self):
        repo = self.path_var.get().strip()
        if not repo or not os.path.isdir(repo) or not self.is_repo(repo):
            messagebox.showwarning("提示", "请先选择一个有效的 Git 仓库。")
            return
        cur = self.current_branch(repo)
        choices = [b for b in self._local_branches(repo) if b != cur]
        if not choices:
            messagebox.showinfo("切换分支", "没有其他分支可切换。")
            return
        name = self._choose("选择要切换到的分支", choices)
        if name:
            self.run(["switch", name])

    def merge_branch(self):
        repo = self.path_var.get().strip()
        if not repo or not os.path.isdir(repo) or not self.is_repo(repo):
            messagebox.showwarning("提示", "请先选择一个有效的 Git 仓库。")
            return
        cur = self.current_branch(repo)
        choices = [b for b in self._local_branches(repo) if b != cur]
        if not choices:
            messagebox.showinfo("合并分支", "没有其他分支可合并。")
            return
        name = self._choose(f"把哪个分支合并到 {cur or '当前分支'}", choices)
        if name:
            self.run(["merge", name])

    def clone(self):
        url = simpledialog.askstring("克隆仓库", "请输入仓库地址:", parent=self.root)
        if not url or not url.strip():
            return
        parent = filedialog.askdirectory(
            title="选择克隆到哪个目录",
            initialdir=self.path_var.get() or DEFAULT_DIR)
        if not parent:
            return
        name = url.strip().rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]

        def on_success():
            self.path_var.set(os.path.join(parent, name))

        self.run(["clone", url.strip()], slow=True, repo=parent,
                 require_repo=False, on_success=on_success)

    def reset_soft(self):
        self.run(
            ["reset", "--soft", "HEAD~1"],
            confirm="撤销上一次提交，但保留所有改动在暂存区？\n命令: git reset --soft HEAD~1")

    def discard_changes(self):
        self.run(
            ["restore", "."],
            confirm="确定丢弃全部未提交改动吗？此操作不可恢复！\n命令: git restore .")

    def _choose(self, title, items):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text=title + ":").pack(padx=12, pady=(12, 4))
        var = tk.StringVar(value=items[0])
        box = ttk.Combobox(win, textvariable=var, values=items,
                           state="readonly", width=34)
        box.pack(padx=12, pady=2)
        result = []

        def ok(_event=None):
            result.append(var.get())
            win.destroy()

        def cancel():
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=(8, 12))
        ttk.Button(btns, text="确定", command=ok).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=cancel).pack(side="left")
        box.bind("<Return>", ok)
        box.focus_set()
        win.wait_window()
        return result[0] if result else None


def main():
    root = tk.Tk()
    GitGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
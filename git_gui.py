"""图形化 Git：多标签页，每个标签独立管理一个 Git 项目。

运行方式: python git_gui.py
"""

import json
import os
import queue
import shlex
import subprocess
import webbrowser
import zipfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

from conflict_agent import ConflictAgent

DEFAULT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(os.path.expanduser("~"), ".git_gui_state.json")


def run_git(repo, args, timeout=60):
    display = shlex.join(["git"] + args)
    if not repo:
        return display, "未指定仓库目录。", -1
    actual = ["git", "-c", "core.quotepath=false"] + args
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
    except OSError as e:
        return display, f"无法执行命令: {e}", -1
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return display, text, proc.returncode


class RepoPanel:
    def __init__(self, parent, root, repo_path, cmd_history=None):
        self.root = root
        self.path_var = tk.StringVar(value=repo_path)
        self._result_q = queue.Queue()
        self._refresh_q = queue.Queue()
        self._cmd_history = list(cmd_history) if cmd_history else []
        self._cmd_hist_idx = len(self._cmd_history)
        self._conflict_agent = ConflictAgent()
        self._build(parent)
        self.refresh_status()

        self.root.after(100, self._poll_queue)
        self.root.after(2000, self._auto_refresh)

    def _build(self, parent):
        self._build_top(parent)
        self._build_buttons(parent)
        self._build_graph(parent)
        self._build_files(parent)
        self._build_output(parent)

    def _build_top(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(top, text="仓库路径:").pack(side="left")
        path_entry = ttk.Entry(top, textvariable=self.path_var)
        path_entry.pack(side="left", fill="x", expand=True, padx=6)
        path_entry.bind("<Return>", lambda e: self.refresh_status())
        btn_new = ttk.Button(top, text="新建仓库", command=self.new_repo)
        btn_new.pack(side="left")
        self._add_tooltip(btn_new, "git init + git add",
                          "选择目录并勾选文件，在该目录初始化 Git 仓库并加入暂存")
        btn_open = ttk.Button(top, text="打开已有仓库", command=self.open_repo)
        btn_open.pack(side="left", padx=4)
        self._add_tooltip(btn_open, "cd <repo> + git status",
                          "选择一个已有的 Git 仓库目录并打开")
        btn_refresh = ttk.Button(top, text="刷新", command=self.refresh_status)
        btn_refresh.pack(side="left", padx=(4, 0))
        self._add_tooltip(btn_refresh, "git status",
                          "重新读取当前仓库的状态、分支和提交历史")
        self.state_var = tk.StringVar(value="就绪")
        ttk.Label(parent, textvariable=self.state_var,
                  foreground="#606060").pack(anchor="w", padx=10, pady=(2, 0))

    def _build_buttons(self, parent):
        box = ttk.LabelFrame(parent, text="点击按钮执行 Git 命令")
        box.pack(fill="x", padx=8, pady=4)
        rows = [
            [("查看状态", self.run_status,
              "git status", "显示工作区状态：变更、暂存、未跟踪文件"),
             ("暂存全部", lambda: self.run(["add", "."]),
              "git add .", "将所有改动加入暂存区"),
             ("暂存选中", self.add_selected,
              "git add <file>", "将选中的文件加入暂存区"),
             ("取消暂存", lambda: self.run(["reset"]),
              "git reset", "将暂存区文件移回工作区"),
             ("提交", self.commit,
              "git commit -m <msg>", "提交暂存区改动到仓库"),
             ("修改上次提交", self.amend,
              "git commit --amend", "修改上一次提交的内容或信息")],
            [("查看日志", lambda: self.run(
                ["log", "--oneline", "--graph", "--all", "-20"]),
              "git log --oneline --graph --all -20", "显示最近20条提交历史"),
             ("查看分支", lambda: self.run(["branch", "-a"]),
              "git branch -a", "列出所有本地和远程分支"),
             ("新建并切换分支", self.create_branch,
              "git switch -c <name>", "创建新分支并切换到该分支"),
             ("切换分支", self.switch_branch,
              "git switch <name>", "切换到指定分支"),
             ("合并分支", self.merge_branch,
              "git merge <branch>", "将指定分支合并到当前分支"),
             ("中止合并", self.abort_merge,
              "git merge --abort", "放弃当前合并，恢复到合并前状态")],
            [("拉取", lambda: self.run(["pull"], slow=True),
              "git pull", "从远程仓库拉取并合并改动"),
             ("推送", lambda: self.run(["push"], slow=True),
              "git push", "将本地提交推送到远程仓库"),
             ("查看远程", lambda: self.run(["remote", "-v"]),
              "git remote -v", "显示远程仓库地址"),
             ("初始化仓库", lambda: self.run(["init"], require_repo=False),
              "git init", "在当前目录初始化 Git 仓库"),
             ("克隆仓库", self.clone,
              "git clone <url>", "从远程地址克隆仓库")],
            [("暂存改动(stash)", lambda: self.run(["stash", "push"]),
              "git stash push", "临时保存工作区改动"),
             ("弹出暂存", lambda: self.run(["stash", "pop"]),
              "git stash pop", "恢复最近一次 stash 的改动"),
             ("撤销上次提交", self.reset_soft,
              "git reset --soft HEAD~1", "撤销上次提交，保留改动在暂存区"),
             ("丢弃改动", self.discard_changes,
              "git restore .", "丢弃所有未提交的改动"),
             ("清空输出", self.clear_output,
               "", "清空命令行输出区")],
            [("连接GitHub并推送", self.connect_github,
               "git remote add + git push -u",
               "输入或创建 GitHub 仓库地址，连接后把本地文件推送上去")],
        ]
        for row in rows:
            frame = ttk.Frame(box)
            frame.pack(fill="x", padx=6, pady=3)
            for text, cmd, git_cmd, desc in row:
                btn = ttk.Button(frame, text=text, width=16, command=cmd)
                btn.pack(side="left", padx=3)
                self._add_tooltip(btn, git_cmd, desc)

    def _add_tooltip(self, widget, git_cmd, desc):
        tip = git_cmd + "\n" + desc if git_cmd else desc

        def on_enter(event):
            x, y = event.x_root + 14, event.y_root + 10
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(
                tw, text=tip, justify="left", background="#ffffe0",
                relief="solid", borderwidth=1,
                font=("Microsoft YaHei UI", 9), padx=6, pady=4).pack()
            widget._tooltip = tw

        def on_leave(_event):
            tw = getattr(widget, "_tooltip", None)
            if tw:
                tw.destroy()
                widget._tooltip = None

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def _build_files(self, parent):
        box = ttk.LabelFrame(
            parent, text='变更文件（选中后可"暂存选中"，双击查看改动）')
        box.pack(fill="x", padx=8, pady=4)
        self.branch_label = ttk.Label(box, text="当前分支: -")
        self.branch_label.pack(anchor="w", padx=6, pady=(4, 0))
        frame = ttk.Frame(box)
        frame.pack(fill="x", padx=6, pady=(2, 6))
        self.file_list = tk.Listbox(frame, height=5, font=("Consolas", 10))
        scroll = ttk.Scrollbar(frame, command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.file_list.pack(side="left", fill="x", expand=True)
        self.file_list.bind("<Double-Button-1>", self.show_diff)

    def _build_output(self, parent):
        box = ttk.LabelFrame(parent, text="Git 命令行输出")
        box.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.output = tk.Text(
            box, height=8, state="disabled", wrap="none", relief="flat",
            bg="#1e1e1e", fg="#dcdcdc", font=("Consolas", 10))
        scroll = ttk.Scrollbar(box, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        cmd_bar = ttk.Frame(box)
        cmd_bar.pack(side="bottom", fill="x")
        ttk.Label(cmd_bar, text="git>", font=("Consolas", 10)).pack(
            side="left", padx=(4, 2))
        self.cmd_var = tk.StringVar()
        self.cmd_entry = ttk.Entry(cmd_bar, textvariable=self.cmd_var)
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=2)
        self.cmd_entry.bind("<Return>", self._run_command)
        self.cmd_entry.bind("<Up>", self._cmd_history_prev)
        self.cmd_entry.bind("<Down>", self._cmd_history_next)
        ttk.Button(cmd_bar, text="执行", command=self._run_command).pack(
            side="left", padx=2)
        scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self.output.tag_configure("cmd", foreground="#7ec8ff")
        self.output.tag_configure("ok", foreground="#6ee06e")
        self.output.tag_configure("fail", foreground="#ff7070")
        self.append_output(
            "点击上方按钮执行命令，或在下方输入 git 命令后回车执行，"
            "图形化视图会自动同步。\n\n")

    def _run_command(self, _event=None):
        raw = self.cmd_var.get().strip()
        if not raw:
            return
        self._cmd_history.append(raw)
        self._cmd_hist_idx = len(self._cmd_history)
        self.cmd_var.set("")
        try:
            args = shlex.split(raw)
        except ValueError as e:
            messagebox.showwarning("命令解析失败", str(e))
            return
        self.run(args, require_repo=False)

    def _cmd_history_prev(self, _event=None):
        if not self._cmd_history:
            return
        if self._cmd_hist_idx > 0:
            self._cmd_hist_idx -= 1
            self.cmd_var.set(self._cmd_history[self._cmd_hist_idx])

    def _cmd_history_next(self, _event=None):
        if not self._cmd_history:
            return
        if self._cmd_hist_idx < len(self._cmd_history) - 1:
            self._cmd_hist_idx += 1
            self.cmd_var.set(self._cmd_history[self._cmd_hist_idx])
        else:
            self._cmd_hist_idx = len(self._cmd_history)
            self.cmd_var.set("")

    def _auto_refresh(self):
        try:
            self._refresh_async()
        except tk.TclError:
            return
        self.root.after(2000, self._auto_refresh)

    def _refresh_async(self):
        repo = self.path_var.get().strip()

        def worker():
            try:
                data = self._gather_refresh(repo)
                self._refresh_q.put(data)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _gather_refresh(self, repo):
        data = {"branch": "（不是 Git 仓库）", "files": [],
                "commits": [], "lanes": {}, "rows": {}}
        if not repo or not os.path.isdir(repo):
            return data
        if not self.is_repo(repo):
            return data
        _, out, code = run_git(repo, ["status", "-z", "--porcelain"])
        if code == 0:
            _, bout, bcode = run_git(repo, ["branch", "--show-current"])
            b = bout.strip() if bcode == 0 else ""
            if b:
                data["branch"] = b
            else:
                _, _, lc = run_git(repo, ["rev-parse", "--short", "HEAD"])
                data["branch"] = "(detached HEAD)" if lc == 0 else "（空仓库，还没有提交）"
            data["files"] = [f"{e[:2]}  {e[3:]}"
                             for e in out.split("\x00") if len(e) > 3]
        _, lout, lcode = run_git(repo, [
            "log", "--all", "--topo-order", "--max-count=200",
            "--pretty=format:%H%x00%h%x00%s%x00%P%x00%d"])
        if lcode == 0 and lout.strip():
            commits = []
            for line in lout.split("\n"):
                parts = line.split("\x00")
                if len(parts) >= 5:
                    commits.append({
                        "hash": parts[0], "short": parts[1],
                        "subject": parts[2],
                        "parents": parts[3].split() if parts[3] else [],
                        "refs": parts[4].strip(" ()"),
                    })
            waiting = {}
            next_lane = 0
            lanes = {}
            for c in commits:
                q = waiting.get(c["hash"])
                if q:
                    lane = q.pop(0)
                    if not q:
                        del waiting[c["hash"]]
                else:
                    lane = next_lane
                    next_lane += 1
                lanes[c["hash"]] = lane
                for i, p in enumerate(c["parents"]):
                    if i == 0:
                        waiting.setdefault(p, []).append(lane)
                    else:
                        waiting.setdefault(p, []).append(next_lane)
                        next_lane += 1
            data["commits"] = commits
            data["lanes"] = lanes
            data["rows"] = {c["hash"]: i for i, c in enumerate(commits)}
        return data

    def _apply_refresh(self, data):
        focus = self.root.focus_get()
        self.branch_label.configure(
            text=f"当前分支: {data['branch']}    变更文件: {len(data['files'])} 个")
        self.file_list.delete(0, "end")
        for f in data["files"]:
            self.file_list.insert("end", f)
        self._graph_commits = data["commits"]
        self._graph_lanes = data["lanes"]
        self._graph_rows = data["rows"]
        if self._selected and self._selected["hash"] not in data["rows"]:
            self._selected = None
        self._draw_graph()
        if focus:
            try:
                focus.focus_set()
            except tk.TclError:
                pass

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
                if b:
                    branch = b
                else:
                    _, _, lc = run_git(repo, ["rev-parse", "--short", "HEAD"])
                    branch = "(detached HEAD)" if lc == 0 else "（空仓库，还没有提交）"
                files = [f"{e[:2]}  {e[3:]}"
                         for e in out.split("\x00") if len(e) > 3]
        self.branch_label.configure(
            text=f"当前分支: {branch}    变更文件: {len(files)} 个")
        self.file_list.delete(0, "end")
        for f in files:
            self.file_list.insert("end", f)
        self.refresh_graph()

    def _build_graph(self, parent):
        self._graph_commits = []
        self._graph_lanes = {}
        self._graph_rows = {}
        self._graph_nodes = []
        self._selected = None
        self._graph_font = tkfont.Font(family="Microsoft YaHei UI", size=9)
        box = ttk.LabelFrame(
            parent,
            text="版本历史图（单击弹出操作菜单，右键：详情 / 回滚 / 检出；橙=HEAD 蓝=分支）")
        box.pack(fill="x", padx=8, pady=4)
        frame = ttk.Frame(box)
        frame.pack(fill="x", padx=6, pady=6)
        self.graph = tk.Canvas(frame, height=300, bg="#ffffff",
                               highlightthickness=0)
        scroll = ttk.Scrollbar(frame, orient="vertical",
                               command=self.graph.yview)
        self.graph.configure(yscrollcommand=scroll.set)
        self.graph.bind("<Button-1>", self._on_graph_click)
        self.graph.bind("<Button-3>", self._on_graph_menu)
        self.graph.bind("<Configure>", lambda e: self._draw_graph())
        self.graph.bind("<MouseWheel>", lambda e: self.graph.yview_scroll(int(-e.delta / 120), "units"))
        scroll.pack(side="right", fill="y")
        self.graph.pack(side="left", fill="both", expand=True)

    def refresh_graph(self):
        self._graph_commits = []
        self._graph_lanes = {}
        self._graph_rows = {}
        self._selected = None
        repo = self.path_var.get().strip()
        if repo and os.path.isdir(repo):
            _, out, code = run_git(repo, [
                "log", "--all", "--topo-order", "--max-count=200",
                "--pretty=format:%H%x00%h%x00%s%x00%P%x00%d"])
            if code == 0 and out.strip():
                commits = []
                for line in out.split("\n"):
                    parts = line.split("\x00")
                    if len(parts) >= 5:
                        commits.append({
                            "hash": parts[0], "short": parts[1],
                            "subject": parts[2],
                            "parents": parts[3].split() if parts[3] else [],
                            "refs": parts[4].strip(" ()"),
                        })
                waiting = {}
                next_lane = 0
                for c in commits:
                    q = waiting.get(c["hash"])
                    if q:
                        lane = q.pop(0)
                        if not q:
                            del waiting[c["hash"]]
                    else:
                        lane = next_lane
                        next_lane += 1
                    self._graph_lanes[c["hash"]] = lane
                    for i, p in enumerate(c["parents"]):
                        if i == 0:
                            waiting.setdefault(p, []).append(lane)
                        else:
                            waiting.setdefault(p, []).append(next_lane)
                            next_lane += 1

                for i, c in enumerate(commits):
                    self._graph_rows[c["hash"]] = i
                self._graph_commits = commits
        self._draw_graph()

    def _draw_graph(self):
        canvas = self.graph
        canvas.delete("all")
        self._graph_nodes = []
        commits = self._graph_commits
        if not commits:
            canvas.create_text(16, 16, anchor="w", fill="#999999",
                               text="暂无提交（空仓库或尚未初始化）")
            canvas.configure(scrollregion=(0, 0, 100, 40))
            return
        col_w, row_h, r = 30, 34, 7
        mx, my = 24, 24
        f = self._graph_font
        lanes, rows = self._graph_lanes, self._graph_rows
        for c in commits:
            for p in c["parents"]:
                if p not in rows:
                    continue
                x1 = mx + lanes[c["hash"]] * col_w
                y1 = my + rows[c["hash"]] * row_h
                x2 = mx + lanes[p] * col_w
                y2 = my + rows[p] * row_h
                canvas.create_line(
                    x1, y1, x1, y1 + row_h / 2, x2, y2 - row_h / 2, x2, y2,
                    smooth=True, fill="#b8c0cc", width=2)
        sel = self._selected["hash"] if self._selected else None
        max_x = 0
        for c in commits:
            h = c["hash"]
            x = mx + lanes[h] * col_w
            y = my + rows[h] * row_h
            color = "#ff9500" if c["refs"].startswith("HEAD") else "#4a90d9"
            if h == sel:
                canvas.create_oval(x - 11, y - 11, x + 11, y + 11,
                                   outline="#e65000", width=2)
            canvas.create_oval(x - r, y - r, x + r, y + r,
                               fill=color, outline="#ffffff", width=1)
            tx = x + r + 10
            canvas.create_text(tx, y, anchor="w", fill="#909090",
                               text=c["short"], font=f)
            tx += f.measure(c["short"]) + 10
            canvas.create_text(tx, y, anchor="w", fill="#222222",
                               text=c["subject"], font=f)
            tx += f.measure(c["subject"]) + 14
            for ref in c["refs"].split(", "):
                ref = ref.strip()
                if not ref:
                    continue
                if ref.startswith("HEAD"):
                    label, color2 = ref, "#e65000"
                elif ref.startswith("tag:"):
                    label, color2 = ref[4:].strip(), "#8e7cc3"
                elif "/" in ref:
                    label, color2 = ref, "#8a8a8a"
                else:
                    label, color2 = ref, "#2e7d32"
                w = f.measure(label) + 12
                canvas.create_rectangle(tx, y - 10, tx + w, y + 10,
                                        outline=color2, width=1)
                canvas.create_text(tx + 6, y, anchor="w", fill=color2,
                                   text=label, font=f)
                tx += w + 6
            max_x = max(max_x, tx)
            self._graph_nodes.append(
                {"hash": h, "short": c["short"], "x": x, "y": y})
        total_h = my * 2 + len(commits) * row_h
        width = max(max_x + 20, canvas.winfo_width())
        canvas.configure(scrollregion=(0, 0, width, total_h))

    def _hit_node(self, cx, cy):
        best, best_d = None, 15 * 15
        for n in self._graph_nodes:
            d = (n["x"] - cx) ** 2 + (n["y"] - cy) ** 2
            if d <= best_d:
                best, best_d = n, d
        return best

    def _on_graph_click(self, event):
        node = self._hit_node(self.graph.canvasx(event.x),
                              self.graph.canvasy(event.y))
        if not node:
            return
        self._selected = node
        self._draw_graph()
        s = node["short"]
        menu = tk.Menu(self.graph, tearoff=0)
        menu.add_command(label=f"查看此提交详情（{s}）",
                         command=lambda: self._select_commit(node))
        menu.add_separator()
        menu.add_command(label=f"⤺ 回滚到此版本，丢弃之后的提交（{s}）",
                         command=lambda: self._reset_to(node, True))
        menu.add_command(label=f"⤺ 回滚到此版本，保留改动在暂存区（{s}）",
                         command=lambda: self._reset_to(node, False))
        menu.add_command(label=f"检出此版本，进入游离 HEAD（{s}）",
                         command=lambda: self._checkout_commit(node))
        menu.add_separator()
        menu.add_command(label="复制完整哈希",
                         command=lambda: self._copy_hash(node))
        menu.tk_popup(event.x_root, event.y_root)
        menu.destroy()

    def _on_graph_menu(self, event):
        node = self._hit_node(self.graph.canvasx(event.x),
                              self.graph.canvasy(event.y))
        if not node:
            return
        self._selected = node
        self._draw_graph()
        s = node["short"]
        menu = tk.Menu(self.graph, tearoff=0)
        menu.add_command(label=f"查看此提交详情（{s}）",
                         command=lambda: self._select_commit(node))
        menu.add_command(label=f"回滚到此版本，丢弃之后的提交（{s}）",
                         command=lambda: self._reset_to(node, True))
        menu.add_command(label=f"回滚到此版本，保留改动在暂存区（{s}）",
                         command=lambda: self._reset_to(node, False))
        menu.add_command(label=f"检出此版本，进入游离 HEAD（{s}）",
                         command=lambda: self._checkout_commit(node))
        menu.add_separator()
        menu.add_command(label="复制完整哈希",
                         command=lambda: self._copy_hash(node))
        menu.tk_popup(event.x_root, event.y_root)
        menu.destroy()

    def _select_commit(self, node):
        self._selected = node
        self._draw_graph()
        self.run(["show", "--stat", "--format=medium", node["short"]])

    def _reset_to(self, node, hard):
        s = node["short"]
        if hard:
            confirm = (f"确定硬回滚到 {s} 吗？\n\n"
                       "此提交之后的所有提交与未提交改动都会被丢弃，"
                       "操作不易恢复！\n\n命令: git reset --hard " + s)
            self.run(["reset", "--hard", s], confirm=confirm)
        else:
            confirm = (f"回滚到 {s}，改动保留在暂存区？\n\n"
                       "命令: git reset --soft " + s)
            self.run(["reset", "--soft", s], confirm=confirm)

    def _checkout_commit(self, node):
        s = node["short"]
        confirm = (f"检出 {s}？HEAD 将进入游离状态（不属于任何分支）。\n\n"
                   "命令: git switch --detach " + s)
        self.run(["switch", "--detach", s], confirm=confirm)

    def _copy_hash(self, node):
        self.root.clipboard_clear()
        self.root.clipboard_append(node["hash"])
        self.state_var.set(f"已复制完整哈希: {node['hash']}")

    def run(self, args, slow=False, confirm=None, repo=None,
            require_repo=True, on_success=None):
        repo = repo or self.path_var.get().strip()
        if not repo or not os.path.isdir(repo):
            messagebox.showwarning("提示", "请先在上方选择一个有效的目录。")
            return
        if require_repo and not self.is_repo(repo):
            messagebox.showwarning(
                "提示", f'“{repo}”不是 Git 仓库。\n请先点击“初始化仓库”或“克隆仓库”。')
            return
        if confirm and not messagebox.askyesno("确认操作", confirm):
            return
        self.state_var.set(f"执行中: git {args[0]} …")
        threading.Thread(
            target=self._worker,
            args=(repo, args, slow, on_success), daemon=True).start()

    def _worker(self, repo, args, slow, on_success):
        try:
            display, text, code = run_git(repo, args, timeout=600 if slow else 60)
        except Exception as e:
            display = shlex.join(["git"] + args)
            self._result_q.put((display, str(e), -1, args, on_success, repo))
            return
        self._result_q.put((display, text, code, args, on_success, repo))

    def _poll_queue(self):
        focus = self.root.focus_get()
        try:
            while True:
                (display, text, code, args, on_success,
                 repo) = self._result_q.get_nowait()
                ok = code == 0
                stamp = time.strftime("%H:%M:%S")
                self.append_output(f"[{stamp}] $ {display}\n", "cmd")
                self.append_output((text or "(无输出)") + "\n")
                self.append_output(
                    f"—— 结果: {'成功' if ok else '失败'} ——\n\n",
                    "ok" if ok else "fail")
                self.state_var.set("就绪")
                self._refresh_async()
                if args and args[0] == "merge" and "--abort" not in args and not ok:
                    self._check_merge_conflict(repo)
                if on_success and ok:
                    on_success()
        except queue.Empty:
            pass
        try:
            while True:
                self._apply_refresh(self._refresh_q.get_nowait())
        except queue.Empty:
            pass
        if focus:
            try:
                focus.focus_set()
            except tk.TclError:
                pass
        self.root.after(100, self._poll_queue)

    def run_status(self):
        self.run(["status"])

    def new_repo(self):
        d = filedialog.askdirectory(
            title="选择新建仓库的目录（将在此目录初始化 Git 仓库）",
            initialdir=self.path_var.get() or DEFAULT_DIR)
        if not d:
            return
        if self.is_repo(d):
            if messagebox.askyesno(
                    "目录已是仓库",
                    f"「{d}」已经是 Git 仓库。\n是否直接打开它？"):
                self.path_var.set(d)
                self.refresh_status()
            return
        files = self._select_files_dialog(d)
        if files is None:
            return
        self.path_var.set(d)

        def on_init_success():
            if files:
                self.run(
                    ["add", "--"] + files,
                    on_success=lambda: self.append_output(
                        f"仓库已在 {d} 初始化，已暂存 {len(files)} 个文件。\n"
                        "请点击「提交」完成初始提交。\n\n", "ok"))
            else:
                self.append_output(
                    f"仓库已在 {d} 初始化（空仓库）。\n"
                    "可暂存文件后点击「提交」。\n\n", "ok")

        self.run(["init"], require_repo=False, on_success=on_init_success)

    def open_repo(self):
        d = filedialog.askdirectory(
            title="选择已有的 Git 仓库目录",
            initialdir=self.path_var.get() or DEFAULT_DIR)
        if not d:
            return
        if not self.is_repo(d):
            if messagebox.askyesno(
                    "不是 Git 仓库",
                    f"「{d}」不是 Git 仓库。\n是否在此目录初始化新仓库？"):
                self.path_var.set(d)
                self.run(["init"], require_repo=False,
                         on_success=lambda: self.append_output(
                             f"已在 {d} 初始化新仓库。\n\n", "ok"))
            return
        self.path_var.set(d)
        self.refresh_status()
        self.append_output(f"已打开仓库: {d}\n\n", "ok")

    def _select_files_dialog(self, directory):
        win = tk.Toplevel(self.root)
        win.title("选择要加入仓库的文件")
        win.transient(self.root)
        win.grab_set()
        win.geometry("520x420")
        win.resizable(True, True)
        ttk.Label(win, text=f"目录: {directory}", foreground="#606060").pack(
            padx=8, pady=(8, 2), anchor="w")
        ttk.Label(win, text="勾选要加入 Git 仓库的文件（默认全选）:").pack(
            padx=8, pady=(0, 4), anchor="w")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=4)
        listbox = tk.Listbox(frame, selectmode="multiple",
                             font=("Consolas", 10), height=14)
        sb = ttk.Scrollbar(frame, command=listbox.yview)
        listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        all_files = []
        try:
            for item in sorted(os.listdir(directory)):
                full = os.path.join(directory, item)
                if os.path.isfile(full) and not item.startswith("."):
                    all_files.append(item)
                    listbox.insert("end", item)
        except OSError as e:
            messagebox.showerror("读取目录失败", str(e), parent=win)
            win.destroy()
            return None
        for i in range(len(all_files)):
            listbox.select_set(i)
        if not all_files:
            ttk.Label(win, text="（目录为空，将创建空仓库）",
                      foreground="#999999").pack(padx=8, pady=4)
        result = []

        def ok():
            sel = listbox.curselection()
            result.append([all_files[i] for i in sel])
            win.destroy()

        def cancel():
            result.append(None)
            win.destroy()

        def select_all():
            listbox.select_set(0, "end")

        def deselect_all():
            listbox.select_clear(0, "end")

        btns = ttk.Frame(win)
        btns.pack(pady=(4, 8))
        ttk.Button(btns, text="全选", command=select_all).pack(
            side="left", padx=4)
        ttk.Button(btns, text="全不选", command=deselect_all).pack(
            side="left", padx=4)
        ttk.Button(btns, text="确定", command=ok).pack(
            side="left", padx=4)
        ttk.Button(btns, text="取消", command=cancel).pack(
            side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.wait_window()
        return result[0] if result else None

    def _file_name(self, index):
        name = self.file_list.get(index)[4:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        return name

    def add_selected(self):
        sel = self.file_list.curselection()
        if not sel:
            messagebox.showinfo("提示", '请先在"变更文件"列表中选中文件。')
            return
        self.run(["add", "--", self._file_name(sel[0])])

    def show_diff(self, _event=None):
        sel = self.file_list.curselection()
        if not sel:
            return
        self.run(["diff", "HEAD", "--", self._file_name(sel[0])])

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
        if self._merge_in_progress(repo):
            messagebox.showwarning(
                "提示", "当前已有未完成的合并。\n请解决冲突后提交，或点击「中止合并」。")
            return
        cur = self.current_branch(repo)
        choices = [b for b in self._local_branches(repo) if b != cur]
        if not choices:
            messagebox.showinfo("合并分支", "没有其他分支可合并。")
            return
        _, out, code = run_git(repo, ["status", "--porcelain"])
        if code == 0 and out.strip():
            if not messagebox.askyesno(
                    "提示",
                    "工作区有未提交的改动，合并可能失败或产生混乱。\n"
                    "建议先「提交」或「暂存改动(stash)」。\n仍要继续合并吗？"):
                return
        picked = self._merge_dialog(cur, choices)
        if not picked:
            return
        branch, mode = picked
        args = ["merge"]
        if mode == "no-ff":
            args.append("--no-ff")
        elif mode == "squash":
            args.append("--squash")
        args.append(branch)
        self.run(args, slow=True)

    def _merge_dialog(self, cur, choices):
        win = tk.Toplevel(self.root)
        win.title("合并分支")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text=f"把哪个分支合并到「{cur or '当前分支'}」:").pack(
            padx=12, pady=(12, 4))
        var = tk.StringVar(value=choices[0])
        ttk.Combobox(win, textvariable=var, values=choices,
                     state="readonly", width=34).pack(padx=12, pady=2)
        mode_var = tk.StringVar(value="normal")
        box = ttk.Frame(win)
        box.pack(padx=12, pady=(6, 2))
        for text, val in [("普通合并（能快进则快进）", "normal"),
                          ("强制生成合并提交（--no-ff）", "no-ff"),
                          ("压缩为一次提交（--squash）", "squash")]:
            ttk.Radiobutton(box, text=text, value=val,
                            variable=mode_var).pack(anchor="w")
        ttk.Label(win, text="遇到冲突时：修改冲突文件 → 暂存全部 → 提交；\n或点击「中止合并」放弃本次合并。",
                  foreground="#666666", justify="left").pack(
            padx=12, pady=(4, 0))
        result = []

        def ok(_event=None):
            result.append((var.get(), mode_var.get()))
            win.destroy()

        def cancel():
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=(6, 12))
        ttk.Button(btns, text="开始合并", command=ok).pack(
            side="left", padx=6)
        ttk.Button(btns, text="取消", command=cancel).pack(side="left")
        win.bind("<Return>", ok)
        win.wait_window()
        return result[0] if result else None

    def _merge_in_progress(self, repo):
        _, _, code = run_git(repo, ["rev-parse", "-q", "--verify",
                                    "MERGE_HEAD"])
        return code == 0

    def _check_merge_conflict(self, repo):
        _, out, code = run_git(repo, ["status", "--porcelain"])
        conflicts = []
        if code == 0:
            conflicts = [line[3:] for line in out.splitlines()
                         if line[:2] in ("UU", "AA", "DD",
                                         "AU", "UA", "DU", "UD")]
        if conflicts:
            self.state_var.set(
                f"⚠ 合并冲突: {len(conflicts)} 个文件待解决")
            self.append_output(
                "⚠ 检测到合并冲突！冲突文件:\n  "
                + "\n  ".join(conflicts) + "\n", "fail")
            if messagebox.askyesno(
                    "合并冲突",
                    f"检测到 {len(conflicts)} 个文件的合并冲突。\n"
                    "是否尝试自动修改？"):
                file_results = self._auto_resolve_conflicts(repo, conflicts)
                if file_results:
                    self._show_conflict_diff(repo, file_results)
                else:
                    self.append_output("没有可自动修改的冲突文件。\n\n", "fail")
            else:
                self.append_output(
                    "请手动解决: 修改冲突文件 → 「暂存全部」→「提交」完成合并；"
                    "或点击「中止合并」放弃。\n\n", "fail")
        elif self._merge_in_progress(repo):
            self.state_var.set("合并进行中(无冲突)，请提交完成合并")
        else:
            self.state_var.set("就绪")

    def _auto_resolve_conflicts(self, repo, conflicts):
        file_results = []
        for rel in conflicts:
            path = os.path.join(repo, rel)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except OSError as e:
                self.append_output(f"无法读取 {rel}: {e}\n\n", "fail")
                continue
            resolved, file_reports = self._conflict_agent.resolve(original, rel)

            file_results.append((rel, original, resolved, file_reports))
            self.append_output(f"── 冲突文件: {rel} ──\n", "fail")
            self.append_output("【原始内容】\n")
            self.append_output(original + "\n")
            self.append_output("【修改后内容】\n", "ok")
            self.append_output(resolved + "\n\n")
            if file_reports:
                self.append_output("【解决报告】\n", "ok")
                for rpt in file_reports:
                    self.append_output(
                        f"  冲突 {rpt['line_range']} ({rpt['type']}): "
                        f"{rpt['strategy']} - {rpt['reason']} "
                        f"[置信度: {rpt['confidence']:.2f}]\n")
                self.append_output("\n")
        return file_results

    def _show_conflict_diff(self, repo, file_results):
        win = tk.Toplevel(self.root)
        win.title("冲突修改对比 - 请检查")
        win.transient(self.root)
        win.grab_set()
        win.geometry("960x760")
        names = [r[0] for r in file_results]
        display_values = [f"{i}: {n}" for i, n in enumerate(names)]
        sel = tk.StringVar(value=display_values[0] if display_values else "")
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(bar, text="文件:").pack(side="left", padx=(0, 4))
        box = ttk.Combobox(bar, textvariable=sel, values=display_values,
                           state="readonly", width=54)
        box.pack(side="left")
        content = ttk.Frame(win)
        content.pack(fill="both", expand=True, padx=8, pady=4)
        left_frame = ttk.Frame(content)
        right_frame = ttk.Frame(content)
        left_frame.pack(side="left", fill="both", expand=True)
        right_frame.pack(side="left", fill="both", expand=True)
        ttk.Label(left_frame, text="冲突原始内容").pack(anchor="w")
        ttk.Label(right_frame, text="修改后内容").pack(anchor="w")
        lsb = ttk.Scrollbar(left_frame)
        rsb = ttk.Scrollbar(right_frame)
        left = tk.Text(left_frame, wrap="none", yscrollcommand=lsb.set)
        right = tk.Text(right_frame, wrap="none", yscrollcommand=rsb.set)
        lsb.config(command=left.yview)
        rsb.config(command=right.yview)
        lsb.pack(side="right", fill="y")
        left.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")
        right.pack(side="left", fill="both", expand=True)
        data = [(r[1], r[2], r[3] if len(r) > 3 else []) for r in file_results]

        report_frame = ttk.Frame(win)
        report_frame.pack(fill="both", padx=8, pady=(2, 4))
        ttk.Label(report_frame, text="── 冲突解决报告 ──").pack(anchor="w")
        rsb2 = ttk.Scrollbar(report_frame)
        report_text = tk.Text(report_frame, height=8, wrap="word", yscrollcommand=rsb2.set)
        rsb2.config(command=report_text.yview)
        rsb2.pack(side="right", fill="y")
        report_text.pack(side="left", fill="both", expand=True)

        def show(_event=None):
            val = sel.get()
            try:
                idx = int(val.split(":", 1)[0])
            except (ValueError, IndexError):
                idx = 0
            if idx < 0 or idx >= len(data):
                idx = 0
            orig, new, reports = data[idx]
            left.delete("1.0", "end")
            right.delete("1.0", "end")
            left.insert("1.0", orig)
            right.insert("1.0", new)
            report_text.delete("1.0", "end")
            if not reports:
                report_text.insert("1.0", "（无解决报告）")
                return
            lines = [f"文件: {names[idx]}"]
            for j, rpt in enumerate(reports, 1):
                lines.append(
                    f"  冲突{j} (行{rpt['line_range']}): "
                    f"{rpt['strategy']} - {rpt['reason']} "
                    f"[置信度: {rpt['confidence']:.2f}]")
            report_text.insert("1.0", "\n".join(lines))

        box.bind("<<ComboboxSelected>>", show)
        show()
        btns = ttk.Frame(win)
        btns.pack(pady=(4, 12))
        result = []

        def accept():
            result.append("accept")
            win.destroy()

        def discard():
            result.append("discard")
            win.destroy()

        ttk.Button(btns, text="接受修改", command=accept).pack(side="left", padx=6)
        ttk.Button(btns, text="放弃修改", command=discard).pack(side="left", padx=6)
        win.protocol("WM_DELETE_WINDOW", discard)
        win.wait_window()
        if result and result[0] == "accept":
            for rel, _orig, new, _rpts in file_results:
                path = os.path.join(repo, rel)
                try:
                    with open(path, "w", encoding="utf-8", errors="replace") as f:
                        f.write(new)
                except OSError as e:
                    self.append_output(f"无法写入 {rel}: {e}\n", "fail")
                    continue
            self.run(["add"] + names, repo=repo,
                     on_success=lambda: self.append_output("已暂存所有自动解决冲突的文件，请点击「提交」完成合并。\n\n", "ok"))
        elif result and result[0] == "discard":
            self.append_output("已保留原始冲突标记，请手动解决后「暂存全部」→「提交」。\n\n", "fail")

    def abort_merge(self):
        repo = self.path_var.get().strip()
        if not repo or not os.path.isdir(repo) or not self.is_repo(repo):
            messagebox.showwarning("提示", "请先选择一个有效的 Git 仓库。")
            return
        if not self._merge_in_progress(repo):
            messagebox.showinfo("中止合并", "当前没有进行中的合并。")
            return
        self.run(
            ["merge", "--abort"],
            confirm="确定中止当前合并？工作区将恢复到合并前状态。\n命令: git merge --abort")

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

    def connect_github(self):
        repo = self.path_var.get().strip()
        if not repo or not os.path.isdir(repo) or not self.is_repo(repo):
            messagebox.showwarning("提示", "请先选择一个有效的 Git 仓库。")
            return
        _, _, code = run_git(repo, ["rev-parse", "HEAD"])
        if code != 0:
            messagebox.showwarning(
                "提示", "当前仓库还没有任何提交。\n"
                "请先「暂存全部」并「提交」，再连接 GitHub 推送。")
            return
        result = self._github_dialog(repo)
        if not result:
            return
        url, remote_name, branch = result
        _, out, _ = run_git(repo, ["remote"])
        existing = [r.strip() for r in out.splitlines() if r.strip()]
        if remote_name in existing:
            _, cur_url, _ = run_git(repo, ["remote", "get-url", remote_name])
            if not messagebox.askyesno(
                    "远程已存在",
                    f"远程「{remote_name}」已存在:\n  {cur_url.strip()}\n\n"
                    f"是否替换为新地址？\n  {url}"):
                return
            self.run(
                ["remote", "set-url", remote_name, url],
                on_success=lambda: self._push_remote(remote_name, branch))
        else:
            self.run(
                ["remote", "add", remote_name, url],
                on_success=lambda: self._push_remote(remote_name, branch))

    def _push_remote(self, remote_name, branch):
        self.append_output(
            f"正在推送 {branch} 到 {remote_name}，首次推送可能需要输入 GitHub 账号密码或令牌……\n",
            "cmd")
        self.run(["push", "-u", remote_name, branch], slow=True)

    def _github_dialog(self, repo):
        win = tk.Toplevel(self.root)
        win.title("连接 GitHub 并推送")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text="将本地仓库推送到 GitHub 远程仓库",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(
            padx=12, pady=(12, 4))
        info = ttk.LabelFrame(win, text="还没有 GitHub 仓库？按以下步骤创建")
        info.pack(fill="x", padx=12, pady=4)
        ttk.Label(
            info, justify="left",
            text="1. 点击下方按钮打开 GitHub 创建页面（需先登录 GitHub）\n"
                 "2. 填写仓库名称，不要勾选「添加 README / .gitignore / 许可证」\n"
                 "3. 点击「Create repository」创建空仓库\n"
                 "4. 复制页面给出的 HTTPS 地址，填入下方「仓库 URL」").pack(
            padx=8, pady=4, anchor="w")
        ttk.Button(
            info, text="打开 GitHub 新建仓库页面",
            command=lambda: webbrowser.open("https://github.com/new")).pack(
            padx=8, pady=(0, 6))
        url_frame = ttk.Frame(win)
        url_frame.pack(fill="x", padx=12, pady=4)
        ttk.Label(url_frame, text="仓库 URL:").pack(side="left", padx=(0, 4))
        url_var = tk.StringVar()
        ttk.Entry(url_frame, textvariable=url_var, width=52).pack(
            side="left", fill="x", expand=True)
        ttk.Button(
            url_frame, text="粘贴", width=4,
            command=lambda: url_var.set(
                self.root.clipboard_get() or "")).pack(side="left", padx=4)
        opt_frame = ttk.Frame(win)
        opt_frame.pack(fill="x", padx=12, pady=4)
        ttk.Label(opt_frame, text="远程名称:").pack(side="left", padx=(0, 4))
        remote_var = tk.StringVar(value="origin")
        ttk.Entry(opt_frame, textvariable=remote_var, width=10).pack(side="left")
        ttk.Label(opt_frame, text="    推送分支:").pack(side="left", padx=(8, 4))
        cur = self.current_branch(repo)
        branch_var = tk.StringVar(value=cur or "main")
        branches = self._local_branches(repo)
        ttk.Combobox(opt_frame, textvariable=branch_var, values=branches,
                     width=18).pack(side="left")
        _, out, _ = run_git(repo, ["remote", "-v"])
        if out.strip():
            ttk.Label(
                win, text=f"当前已有远程:\n{out}", foreground="#666666",
                justify="left").pack(padx=12, pady=4, anchor="w")
        result = []

        def ok():
            url = url_var.get().strip()
            if not url:
                messagebox.showwarning("提示", "请输入仓库 URL。", parent=win)
                return
            if not url.startswith(("http://", "https://", "git@")):
                messagebox.showwarning(
                    "提示", "URL 格式不正确。\n应为 https://github.com/用户名/仓库名.git",
                    parent=win)
                return
            result.append((url, remote_var.get().strip() or "origin",
                           branch_var.get().strip() or "main"))
            win.destroy()

        def cancel():
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=(8, 12))
        ttk.Button(btns, text="连接并推送", command=ok).pack(
            side="left", padx=6)
        ttk.Button(btns, text="取消", command=cancel).pack(side="left")
        win.wait_window()
        return result[0] if result else None

    def reset_soft(self):
        self.run(
            ["reset", "--soft", "HEAD~1"],
            confirm="撤销上一次提交，但保留所有改动在暂存区？\n命令: git reset --soft HEAD~1")

    def discard_changes(self):
        self.run(
            ["reset", "--hard", "HEAD"],
            confirm="确定丢弃全部未提交改动（含暂存区）吗？此操作不可恢复！\n命令: git reset --hard HEAD")

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


class GitGUI:
    def __init__(self, root):
        self.root = root
        root.title("图形化 Git")
        root.geometry("960x920")
        root.minsize(800, 700)
        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(top, text="+ 新建项目标签",
                   command=self._new_tab).pack(side="left")
        ttk.Button(top, text="× 关闭当前标签",
                   command=self._close_tab).pack(side="left", padx=4)
        ttk.Button(top, text="导出压缩包",
                   command=self._export_zip).pack(side="left", padx=4)
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self._panels = {}
        tabs, active = self._load_state()
        if not tabs:
            tabs = [{"path": DEFAULT_DIR, "cmd_history": []}]
            active = 0
        for t in tabs:
            self._new_tab(t["path"], t.get("cmd_history", []), _save=False)
        if active < len(tabs):
            self.nb.select(active)
        self._save_state()
        current = self.nb.select()
        if current:
            panel = self._panels.get(str(current))
            if panel:
                panel.cmd_entry.focus_set()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_state(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            tabs = data.get("tabs", [])
            if not isinstance(tabs, list):
                return [], 0
            tabs = [t for t in tabs if isinstance(t, dict) and t.get("path")]
            return tabs, data.get("active_tab", 0)
        except Exception:
            return [], 0

    def _save_state(self):
        tabs = []
        for frame_id, panel in self._panels.items():
            tabs.append({
                "path": panel.path_var.get().strip(),
                "cmd_history": panel._cmd_history[-50:],
            })
        try:
            active = self.nb.index(self.nb.select())
        except Exception:
            active = 0
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"tabs": tabs, "active_tab": active}, f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_state()
        self.root.destroy()

    def _new_tab(self, path=None, cmd_history=None, _save=True):
        if path is None:
            path = filedialog.askdirectory(initialdir=DEFAULT_DIR)
            if not path:
                return
        frame = ttk.Frame(self.nb)
        panel = RepoPanel(frame, self.root, path, cmd_history)
        name = os.path.basename(path) or path
        self.nb.add(frame, text=name)
        self.nb.select(frame)
        self._panels[str(frame)] = panel
        if _save:
            self._save_state()

    def _close_tab(self):
        current = self.nb.select()
        if not current:
            return
        if self.nb.index("end") <= 1:
            messagebox.showinfo("提示", "至少保留一个标签页。")
            return
        self.nb.forget(current)
        self._panels.pop(str(current), None)
        self._save_state()

    def _export_zip(self):
        save_path = filedialog.asksaveasfilename(
            title="导出项目压缩包",
            defaultextension=".zip",
            initialfile="git_gui_project.zip",
            filetypes=[("ZIP 压缩包", "*.zip")])
        if not save_path:
            return
        include_files = ["git_gui.py", "conflict_agent.py", ".gitignore"]
        exclude_exts = {".pyc", ".log"}
        exclude_dirs = {"__pycache__", ".git"}
        try:
            with zipfile.ZipFile(save_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname in include_files:
                    fpath = os.path.join(DEFAULT_DIR, fname)
                    if os.path.isfile(fpath):
                        zf.write(fpath, fname)
                for item in os.listdir(DEFAULT_DIR):
                    if item in include_files or item in exclude_dirs:
                        continue
                    fpath = os.path.join(DEFAULT_DIR, item)
                    if os.path.isfile(fpath):
                        ext = os.path.splitext(item)[1].lower()
                        if ext in exclude_exts:
                            continue
                        zf.write(fpath, item)
                    elif os.path.isdir(fpath) and item not in exclude_dirs:
                        for root_dir, dirs, files in os.walk(fpath):
                            dirs[:] = [d for d in dirs
                                       if d not in exclude_dirs]
                            for fn in files:
                                ext = os.path.splitext(fn)[1].lower()
                                if ext in exclude_exts:
                                    continue
                                full = os.path.join(root_dir, fn)
                                arc = os.path.relpath(full, DEFAULT_DIR)
                                zf.write(full, arc)
            messagebox.showinfo(
                "导出成功",
                f"项目已导出到:\n{save_path}\n\n"
                f"包含: {', '.join(include_files)} 及其他项目文件\n"
                f"排除: .git/, __pycache__/, *.pyc, *.log")
        except Exception as exc:
            messagebox.showerror("导出失败", f"无法创建压缩包: {exc}")


def main():
    root = tk.Tk()
    GitGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
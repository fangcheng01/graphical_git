"""图形化 Git：点击按钮自动执行常用 Git 命令，下方实时显示原版命令行输出。

运行方式: python git_gui.py
"""

import os
import queue
import shlex
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

DEFAULT_DIR = os.path.dirname(os.path.abspath(__file__))

GIT_COMMANDS = [
    ("git status", "查看工作区状态"),
    ("git status -s", "简短状态"),
    ("git add .", "暂存全部改动"),
    ("git add <文件>", "暂存指定文件"),
    ("git commit -m \"提交信息\"", "提交"),
    ("git commit --amend", "修改上次提交"),
    ("git log --oneline --graph --all", "图形化查看日志"),
    ("git log -5", "查看最近5条提交"),
    ("git diff", "查看未暂存改动"),
    ("git diff --cached", "查看已暂存改动"),
    ("git show <提交>", "查看某次提交详情"),
    ("git branch -a", "查看所有分支"),
    ("git branch <名字>", "创建分支"),
    ("git switch <分支>", "切换分支"),
    ("git switch -c <分支>", "新建并切换分支"),
    ("git merge <分支>", "合并分支"),
    ("git merge --no-ff <分支>", "合并并生成合并提交"),
    ("git merge --abort", "中止合并"),
    ("git rebase <分支>", "变基"),
    ("git reset --soft HEAD~1", "撤销上次提交(保留改动)"),
    ("git reset --hard <提交>", "硬回滚(危险)"),
    ("git revert <提交>", "生成反向提交抵消"),
    ("git stash push", "暂存工作区"),
    ("git stash pop", "弹出暂存"),
    ("git stash list", "查看暂存列表"),
    ("git remote -v", "查看远程仓库"),
    ("git remote add origin <地址>", "添加远程仓库"),
    ("git push", "推送到远程"),
    ("git push -u origin <分支>", "推送并关联远程分支"),
    ("git pull", "拉取远程更新"),
    ("git fetch", "获取远程更新"),
    ("git tag", "查看标签"),
    ("git tag <名字>", "创建标签"),
    ("git checkout -- <文件>", "丢弃文件改动"),
    ("git clean -fd", "删除未跟踪文件(危险)"),
]

IGNORE_KEYS = ("Up", "Down", "Return", "Tab", "Escape", "Left", "Right",
               "Home", "End", "Shift_L", "Shift_R", "Control_L",
               "Control_R", "Alt_L", "Alt_R")


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
        root.geometry("960x920")
        root.minsize(800, 700)
        self._result_q = queue.Queue()
        self._build_top()
        self._build_buttons()
        self._build_graph()
        self._build_files()
        self._build_output()
        self.refresh_status()
        self.root.after(100, self._poll_queue)

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
             ("合并分支", self.merge_branch),
             ("中止合并", self.abort_merge)],
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
        self.file_list = tk.Listbox(frame, height=5, font=("Consolas", 10))
        scroll = ttk.Scrollbar(frame, command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.file_list.pack(side="left", fill="x", expand=True)
        self.file_list.bind("<Double-Button-1>", self.show_diff)

    def _build_output(self):
        box = ttk.LabelFrame(
            self.root,
            text="Git 命令行（可直接输入命令，输入时自动提示；↑↓ 历史，Tab 补全）")
        box.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.output = tk.Text(
            box, height=8, state="disabled", wrap="none", relief="flat",
            bg="#1e1e1e", fg="#dcdcdc", font=("Consolas", 10))
        scroll = ttk.Scrollbar(box, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        entry_row = ttk.Frame(box)
        entry_row.pack(side="bottom", fill="x", padx=4, pady=(4, 6))
        ttk.Label(entry_row, text="$", foreground="#6ee06e").pack(side="left")
        self.cmd_entry = ttk.Entry(entry_row, font=("Consolas", 10))
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(entry_row, text="执行", command=self.exec_input).pack(
            side="left")
        scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self.output.tag_configure("cmd", foreground="#7ec8ff")
        self.output.tag_configure("ok", foreground="#6ee06e")
        self.output.tag_configure("fail", foreground="#ff7070")
        self.suggest_box = tk.Listbox(
            box, height=6, font=("Consolas", 9), bg="#fffbe6", fg="#333333",
            relief="flat", highlightthickness=1,
            highlightbackground="#d0d0d0")
        self.suggest_box.bind("<ButtonRelease-1>", self._on_suggest_click)
        self._history = []
        self._hist_idx = None
        self._sug_cmds = []
        self._sug_idx = 0
        self.cmd_entry.bind("<Return>", self._on_return)
        self.cmd_entry.bind("<Tab>", self._on_tab)
        self.cmd_entry.bind("<Up>", self._on_up)
        self.cmd_entry.bind("<Down>", self._on_down)
        self.cmd_entry.bind("<Escape>", lambda e: self._hide_suggest())
        self.cmd_entry.bind("<KeyRelease>", self._update_suggest)
        self.append_output(
            "点击上方按钮执行命令，或在 $ 输入框直接输入 git 命令后回车，"
            "此处显示真实的命令与输出。\n\n")

    def _update_suggest(self, event):
        if event.keysym in IGNORE_KEYS:
            return
        q = self.cmd_entry.get().strip()
        if q.lower().startswith("git"):
            q = q[3:].lstrip()
        if not q:
            self._hide_suggest()
            return
        ql = q.lower()
        starts, contains = [], []
        for cmd, desc in GIT_COMMANDS:
            body = cmd[4:]
            if body.startswith(ql):
                starts.append((cmd, desc))
            elif ql in body:
                contains.append((cmd, desc))
        picked = (starts + contains)[:8]
        if not picked:
            self._hide_suggest()
            return
        self._sug_cmds = [c for c, _ in picked]
        self._sug_idx = 0
        self.suggest_box.delete(0, "end")
        for cmd, desc in picked:
            self.suggest_box.insert("end", f"{cmd}    {desc}")
        self.suggest_box.selection_clear(0, "end")
        self.suggest_box.selection_set(0)
        self.suggest_box.see(0)
        if not self.suggest_box.winfo_ismapped():
            self.suggest_box.pack(side="bottom", fill="x", padx=6, pady=(0, 2))

    def _hide_suggest(self):
        if self.suggest_box.winfo_ismapped():
            self.suggest_box.pack_forget()
        self._sug_cmds = []
        self._sug_idx = 0

    def _pick_suggest(self):
        if not self._sug_cmds:
            return
        idx = min(max(self._sug_idx, 0), len(self._sug_cmds) - 1)
        cmd = self._sug_cmds[idx]
        self.cmd_entry.delete(0, "end")
        self.cmd_entry.insert(0, cmd)
        self.cmd_entry.icursor("end")
        self._hide_suggest()
        self.cmd_entry.focus_set()

    def _on_suggest_click(self, event):
        idx = self.suggest_box.nearest(event.y)
        if 0 <= idx < len(self._sug_cmds):
            self._sug_idx = idx
            self._pick_suggest()

    def _on_return(self, _event=None):
        if self.suggest_box.winfo_ismapped() and self._sug_cmds:
            self._pick_suggest()
            return "break"
        self.exec_input()
        return "break"

    def _on_tab(self, _event=None):
        if self.suggest_box.winfo_ismapped() and self._sug_cmds:
            self._pick_suggest()
        return "break"

    def _on_up(self, _event=None):
        if self.suggest_box.winfo_ismapped() and self._sug_cmds:
            self._sug_idx = max(0, self._sug_idx - 1)
            self.suggest_box.selection_clear(0, "end")
            self.suggest_box.selection_set(self._sug_idx)
            self.suggest_box.see(self._sug_idx)
            return "break"
        self._hist_prev()
        return "break"

    def _on_down(self, _event=None):
        if self.suggest_box.winfo_ismapped() and self._sug_cmds:
            self._sug_idx = min(len(self._sug_cmds) - 1, self._sug_idx + 1)
            self.suggest_box.selection_clear(0, "end")
            self.suggest_box.selection_set(self._sug_idx)
            self.suggest_box.see(self._sug_idx)
            return "break"
        self._hist_next()
        return "break"

    def _hist_prev(self):
        if not self._history:
            return
        if self._hist_idx is None:
            self._hist_idx = len(self._history)
        self._hist_idx = max(0, self._hist_idx - 1)
        self.cmd_entry.delete(0, "end")
        self.cmd_entry.insert(0, self._history[self._hist_idx])
        self.cmd_entry.icursor("end")

    def _hist_next(self):
        if self._hist_idx is None:
            return
        self._hist_idx += 1
        self.cmd_entry.delete(0, "end")
        if self._hist_idx >= len(self._history):
            self._hist_idx = None
        else:
            self.cmd_entry.insert(0, self._history[self._hist_idx])
        self.cmd_entry.icursor("end")

    def exec_input(self):
        raw = self.cmd_entry.get().strip()
        if not raw:
            return
        self._hide_suggest()
        self.cmd_entry.delete(0, "end")
        self._history.append(raw)
        self._hist_idx = None
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()
        if parts and parts[0].lower() == "git":
            parts = parts[1:]
        if not parts:
            return
        joined = " ".join(parts)
        confirm = None
        for kw in ("reset --hard", "push --force", "push -f", "clean",
                   "filter-branch"):
            if kw in joined:
                confirm = (f"命令包含危险操作「{kw}」，确定执行？\n\n"
                           f"命令: git {joined}")
                break
        slow = parts[0] in ("push", "pull", "fetch", "clone")
        self.run(parts, slow=slow, confirm=confirm)

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
        self.refresh_graph()

    def _build_graph(self):
        self._graph_commits = []
        self._graph_lanes = {}
        self._graph_rows = {}
        self._graph_nodes = []
        self._selected = None
        self._graph_font = tkfont.Font(family="Microsoft YaHei UI", size=9)
        box = ttk.LabelFrame(
            self.root,
            text="版本历史图（单击选中提交，右键：详情 / 回滚 / 检出；橙=HEAD 绿=分支）")
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
        if node:
            self._select_commit(node)

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
                "提示", f"“{repo}”不是 Git 仓库。\n请先点击“初始化仓库”或“克隆仓库”。")
            return
        if confirm and not messagebox.askyesno("确认操作", confirm):
            return
        self.state_var.set(f"执行中: git {args[0]} …")
        threading.Thread(
            target=self._worker,
            args=(repo, args, slow, on_success), daemon=True).start()

    def _worker(self, repo, args, slow, on_success):
        display, text, code = run_git(repo, args, timeout=600 if slow else 60)
        self._result_q.put((display, text, code, args, on_success, repo))

    def _poll_queue(self):
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
                self.refresh_status()
                if args and args[0] == "merge" and not ok:
                    self._check_merge_conflict(repo)
                if on_success and ok:
                    on_success()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

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
                + "\n  ".join(conflicts)
                + "\n解决后: 「暂存全部」→「提交」完成合并；"
                  "或点击「中止合并」放弃。\n\n", "fail")
        elif self._merge_in_progress(repo):
            self.state_var.set("合并进行中(无冲突)，请提交完成合并")
        else:
            self.state_var.set("就绪")

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
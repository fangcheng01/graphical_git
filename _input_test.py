import os
import subprocess
import tempfile
import time
import tkinter as tk

import git_gui

tmp = tempfile.mkdtemp()

def git(*args):
    subprocess.run(["git"] + list(args), cwd=tmp, check=True,
                   capture_output=True)

git("init", "-q", "-b", "main")
git("config", "user.email", "t@t.com")
git("config", "user.name", "t")
with open(os.path.join(tmp, "a.txt"), "w") as f:
    f.write("1")
git("add", ".")
git("commit", "-qm", "c1")

root = tk.Tk()
app = git_gui.GitGUI(root)
app.path_var.set(tmp)
root.update()

app.cmd_entry.insert(0, "st")
app._update_suggest(type("E", (), {"keysym": "s"})())
assert app._sug_cmds, "no suggestions"
print("SUGGEST:", app._sug_cmds[:3])
assert any(c.startswith("git status") for c in app._sug_cmds), app._sug_cmds
app._pick_suggest()
assert app.cmd_entry.get() == "git status", app.cmd_entry.get()

app.cmd_entry.delete(0, "end")
app.cmd_entry.insert(0, "git log --oneline -3")
app.exec_input()
assert app.cmd_entry.get() == "", "entry should clear"

app.cmd_entry.insert(0, "git status -s")
app.exec_input()
for _ in range(60):
    root.update()
    time.sleep(0.1)
out = app.output.get("1.0", "end")
assert "$ git log --oneline -3" in out, out[:600]
assert "$ git status -s" in out, out[:600]

app._hist_prev()
assert app.cmd_entry.get() == "git status -s", app.cmd_entry.get()
app._hist_prev()
assert app.cmd_entry.get() == "git log --oneline -3", app.cmd_entry.get()
app._hist_next()
assert app.cmd_entry.get() == "git status -s", app.cmd_entry.get()

root.destroy()
print("CMD INPUT SMOKE OK")
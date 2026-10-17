"""合并冲突解决智能体 - 语义级冲突分析和智能解决"""

import json
import os
import re


class ConflictAgent:
    def __init__(self):
        self.strategies = [
            self._strategy_identical,
            self._strategy_empty_side,
            self._strategy_imports,
            self._strategy_config,
            self._strategy_superset,
            self._strategy_semantic,
            self._strategy_indent_aware,
            self._strategy_confidence,
            self._strategy_bracket_check,
            self._strategy_general,
        ]

    def resolve(self, original_text, filename=""):
        lines = original_text.splitlines(keepends=True)
        result = []
        reports = []
        i = 0
        n = len(lines)
        while i < n:
            if lines[i].startswith("<<<<<<<"):
                start_line = i + 1
                marker = lines[i]
                i += 1
                head = []
                while i < n and not lines[i].startswith("======="):
                    head.append(lines[i])
                    i += 1
                if i >= n:
                    result.append(marker)
                    result.extend(head)
                    break
                i += 1
                branch = []
                while i < n and not lines[i].startswith(">>>>>>>"):
                    branch.append(lines[i])
                    i += 1
                if i >= n:
                    result.append(marker)
                    result.extend(head)
                    result.append("=======\n")
                    result.extend(branch)
                    break
                end_line = i + 1
                end_marker = lines[i]
                i += 1
                merged, strategy_name, reason, confidence = self.analyze_conflict(
                    head, branch, filename)
                ctype = self._detect_conflict_type(head, branch, filename)
                if merged is None:
                    result.append(marker)
                    result.extend(self._fix_newlines(head))
                    result.append("=======\n")
                    result.extend(self._fix_newlines(branch))
                    result.append(end_marker)
                    reports.append({
                        "file": filename,
                        "line_range": f"{start_line}-{end_line}",
                        "type": ctype,
                        "strategy": "none",
                        "reason": "无法自动解决，保留原始冲突标记，请手动处理",
                        "confidence": 0.0,
                    })
                else:
                    result.extend(merged)
                    reports.append({
                        "file": filename,
                        "line_range": f"{start_line}-{end_line}",
                        "type": ctype,
                        "strategy": strategy_name,
                        "reason": reason,
                        "confidence": confidence,
                    })
            else:
                result.append(lines[i])
                i += 1
        return "".join(result), reports

    def analyze_conflict(self, head_lines, branch_lines, filename=""):
        for strategy in self.strategies:
            res = strategy(head_lines, branch_lines, filename)
            if res is not None:
                merged, reason, confidence = res
                return merged, strategy.__name__, reason, confidence
        return None, "none", "无可用策略", 0.0

    def _detect_conflict_type(self, head, branch, filename):
        head_empty = all(not ln.strip() for ln in head)
        branch_empty = all(not ln.strip() for ln in branch)
        if head_empty and branch_empty:
            return "delete_delete"
        if head_empty or branch_empty:
            return "delete_modify"
        return "content"

    def _detect_file_type(self, filename, text):
        lower = filename.lower()
        if lower.endswith(".py"):
            return "python"
        if lower.endswith((".js", ".ts", ".jsx", ".tsx")):
            return "javascript"
        if lower.endswith(".java"):
            return "java"
        if lower.endswith(".json"):
            return "json"
        if lower.endswith((".yaml", ".yml")):
            return "yaml"
        if lower.endswith(".md"):
            return "markdown"
        if lower.endswith((".ini", ".cfg", ".conf", ".properties")):
            return "ini"
        s = text.strip()
        if s and s[0] in "{[":
            try:
                json.loads(s)
                return "json"
            except Exception:
                pass
        for ln in text.splitlines():
            ss = ln.strip()
            if ss and ":" in ln and not ln.startswith((" ", "\t", "#")):
                return "yaml"
        return "code"

    def _is_import_line(self, line):
        s = line.strip()
        if not s:
            return False
        return s.startswith(("import ", "from ", "#include"))

    def _compress_blanks(self, lst):
        out = []
        prev_blank = False
        for ln in lst:
            cur_blank = not ln.strip()
            if cur_blank and prev_blank:
                continue
            out.append(ln)
            prev_blank = cur_blank
        return out

    def _fix_newlines(self, lst):
        out = []
        for ln in lst:
            if ln and not ln.endswith("\n"):
                out.append(ln + "\n")
            else:
                out.append(ln)
        return out

    def _detect_indent(self, lines):
        counts = {"    ": 0, "  ": 0, "\t": 0}
        for ln in lines:
            if ln.startswith("    "):
                counts["    "] += 1
            elif ln.startswith("  "):
                counts["  "] += 1
            elif ln.startswith("\t"):
                counts["\t"] += 1
        best = max(counts, key=counts.get)
        if counts[best] == 0:
            return None
        return best

    def _convert_indent(self, line, target):
        stripped = line.lstrip()
        if not stripped:
            return line
        leading = line[:len(line) - len(stripped)]
        if "\t" in leading and target != "\t":
            return leading.replace("\t", target) + stripped
        if " " in leading and target == "\t":
            return leading.replace("    ", "\t").replace("  ", "\t") + stripped
        return line

    def _brackets_balanced(self, lines):
        text = "".join(lines)
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack = []
        in_str = None
        i = 0
        while i < len(text):
            c = text[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == in_str:
                    in_str = None
                i += 1
                continue
            if c in ('"', "'"):
                in_str = c
            elif c in pairs:
                stack.append(c)
            elif c in pairs.values():
                if not stack:
                    return False
                top = stack.pop()
                if pairs[top] != c:
                    return False
            i += 1
        return len(stack) == 0

    def _is_json_like(self, text):
        s = text.strip()
        return bool(s) and s[0] in "{["

    def _deep_merge_dict(self, a, b):
        result = dict(a)
        for k, v in b.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = self._deep_merge_dict(result[k], v)
            else:
                result[k] = v
        return result

    def _merge_json(self, head_text, branch_text):
        try:
            head_obj = json.loads(head_text)
            branch_obj = json.loads(branch_text)
        except Exception:
            return None
        if not isinstance(head_obj, dict) or not isinstance(branch_obj, dict):
            return None
        merged = self._deep_merge_dict(head_obj, branch_obj)
        try:
            out = json.dumps(merged, indent=2, ensure_ascii=False)
        except Exception:
            return None
        return [ln + "\n" for ln in out.splitlines()]

    def _is_yaml_like(self, text):
        for ln in text.splitlines():
            if ln.startswith((" ", "\t", "#")):
                continue
            s = ln.strip()
            if not s:
                continue
            if ":" in ln:
                return True
        return False

    def _is_ini_like(self, text):
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("[") and s.endswith("]"):
                return True
            if "=" in s and not s.startswith("#"):
                return True
        return False

    def _merge_kv_block(self, head, branch, sep):
        def top_keys(lines):
            keys = set()
            for ln in lines:
                if ln.startswith((" ", "\t", "#")):
                    continue
                s = ln.strip()
                if not s:
                    continue
                if sep in ln:
                    keys.add(ln.split(sep, 1)[0].strip())
                elif s.startswith("[") and s.endswith("]"):
                    keys.add(s)
            return keys
        head_keys = top_keys(head)
        branch_keys = top_keys(branch)
        if not head_keys and not branch_keys:
            return None
        merged = list(head)
        for ln in branch:
            if ln.startswith((" ", "\t", "#")):
                continue
            s = ln.strip()
            if not s:
                continue
            if sep in ln:
                k = ln.split(sep, 1)[0].strip()
                if k not in head_keys:
                    merged.append(ln)
            elif s.startswith("[") and s.endswith("]"):
                if s not in head_keys:
                    merged.append(ln)
        merged = self._compress_blanks(merged)
        merged = self._fix_newlines(merged)
        if not self._brackets_balanced(merged):
            return None
        return merged

    def _try_config_merge(self, head, branch):
        head_text = "".join(head)
        branch_text = "".join(branch)
        if self._is_json_like(head_text) and self._is_json_like(branch_text):
            r = self._merge_json(head_text, branch_text)
            if r is not None:
                return r
        if self._is_yaml_like(head_text) and self._is_yaml_like(branch_text):
            r = self._merge_kv_block(head, branch, ":")
            if r is not None:
                return r
        if self._is_ini_like(head_text) and self._is_ini_like(branch_text):
            r = self._merge_kv_block(head, branch, "=")
            if r is not None:
                return r
        return None

    def _strategy_identical(self, head, branch, filename):
        if head == branch:
            return list(head), "双方内容完全相同，直接保留", 1.0
        return None

    def _strategy_empty_side(self, head, branch, filename):
        head_empty = all(not ln.strip() for ln in head)
        branch_empty = all(not ln.strip() for ln in branch)
        if head_empty and branch_empty:
            return [], "双方都为空，删除该段", 1.0
        if head_empty:
            return self._fix_newlines(branch), "HEAD 方为空，采用 branch 新增内容", 0.9
        if branch_empty:
            return self._fix_newlines(head), "branch 方为空，保留 HEAD 内容", 0.9
        return None

    def _strategy_imports(self, head, branch, filename):
        head_has_imp = any(self._is_import_line(ln) for ln in head)
        branch_has_imp = any(self._is_import_line(ln) for ln in branch)
        if not (head_has_imp or branch_has_imp):
            return None
        head_imp = [ln for ln in head if self._is_import_line(ln)]
        head_non = [ln for ln in head if not self._is_import_line(ln)]
        branch_imp = [ln for ln in branch if self._is_import_line(ln)]
        branch_non = [ln for ln in branch if not self._is_import_line(ln)]
        merged_imp = list(head_imp)
        for bi in branch_imp:
            if bi not in merged_imp:
                merged_imp.append(bi)
        merged_non = list(head_non)
        for bn in branch_non:
            if bn not in merged_non:
                merged_non.append(bn)
        result = merged_imp + merged_non
        result = self._compress_blanks(result)
        result = self._fix_newlines(result)
        if not self._brackets_balanced(result):
            return None
        return result, "双方都包含 import 语句，合并保留所有 import 并去重", 0.95

    def _strategy_config(self, head, branch, filename):
        ftype = self._detect_file_type(filename, "".join(head))
        if ftype in ("python", "javascript", "java", "markdown", "code"):
            return None
        r = self._try_config_merge(head, branch)
        if r is None:
            return None
        if ftype == "json":
            reason = "JSON 深度合并：双方独有 key 保留，同名 key 取 branch"
        elif ftype == "yaml":
            reason = "YAML 按 key 合并：保留 HEAD，追加 branch 独有 key"
        elif ftype == "ini":
            reason = "INI/配置 按 key 合并：保留 HEAD，追加 branch 独有键"
        else:
            reason = "配置文件合并"
        return r, reason, 0.9

    def _strategy_superset(self, head, branch, filename):
        head_set = set(head)
        branch_set = set(branch)
        if not head_set and not branch_set:
            return None
        if head_set <= branch_set and head_set != branch_set:
            return self._fix_newlines(branch), "HEAD 是 branch 的子集，采用 branch（包含更多内容）", 0.85
        if branch_set <= head_set and branch_set != head_set:
            return self._fix_newlines(head), "branch 是 HEAD 的子集，采用 HEAD（包含更多内容）", 0.85
        return None

    def _strategy_semantic(self, head, branch, filename):
        ftype = self._detect_file_type(filename, "".join(head))
        if ftype == "python":
            r = self._semantic_python(head, branch)
            if r is not None:
                return r
        elif ftype == "json":
            r = self._merge_json("".join(head), "".join(branch))
            if r is not None:
                return r, "JSON 语义合并：深度合并字典，保留双方独有 key", 0.9
        elif ftype == "yaml":
            r = self._merge_kv_block(head, branch, ":")
            if r is not None:
                return r, "YAML 语义合并：按缩进和 key 合并", 0.85
        elif ftype == "markdown":
            return self._semantic_markdown(head, branch)
        return self._semantic_code_block(head, branch)

    def _semantic_python(self, head, branch):
        def split_blocks(lines):
            blocks = []
            cur = []
            for ln in lines:
                s = ln.lstrip()
                if s.startswith(("def ", "class ", "@")) and not ln.startswith((" ", "\t")):
                    if cur:
                        blocks.append(cur)
                    cur = [ln]
                else:
                    cur.append(ln)
            if cur:
                blocks.append(cur)
            return blocks
        head_blocks = split_blocks(head)
        branch_blocks = split_blocks(branch)
        if not head_blocks and not branch_blocks:
            return None
        merged = []
        seen = set()
        for blk in head_blocks + branch_blocks:
            key = tuple(blk)
            if key not in seen:
                seen.add(key)
                merged.extend(blk)
        merged = self._compress_blanks(merged)
        merged = self._fix_newlines(merged)
        if not self._brackets_balanced(merged):
            return None
        return merged, "Python 语义合并：按函数/类定义分块，完整保留双方定义并去重", 0.85

    def _semantic_markdown(self, head, branch):
        def to_paragraphs(lines):
            paras = []
            cur = []
            for ln in lines:
                if not ln.strip():
                    if cur:
                        paras.append("".join(cur))
                        cur = []
                else:
                    cur.append(ln)
            if cur:
                paras.append("".join(cur))
            return paras
        head_paras = to_paragraphs(head)
        branch_paras = to_paragraphs(branch)
        merged = list(head_paras)
        for bp in branch_paras:
            if bp not in merged:
                merged.append(bp)
        out = []
        for p in merged:
            out.append(p)
            if not p.endswith("\n"):
                out.append("\n")
            out.append("\n")
        if out:
            out.pop()
        out = self._fix_newlines(out)
        return out, "Markdown 语义合并：按段落合并，保留双方独有段落", 0.8

    def _semantic_code_block(self, head, branch):
        def to_blocks(lines):
            blocks = []
            cur = []
            for ln in lines:
                if not ln.strip():
                    if cur:
                        blocks.append(cur)
                        cur = []
                else:
                    cur.append(ln)
            if cur:
                blocks.append(cur)
            return blocks
        head_blocks = to_blocks(head)
        branch_blocks = to_blocks(branch)
        if not head_blocks and not branch_blocks:
            return None
        merged = []
        seen = set()
        for blk in head_blocks + branch_blocks:
            key = tuple(blk)
            if key not in seen:
                seen.add(key)
                merged.extend(blk)
        out = self._fix_newlines(merged)
        if not self._brackets_balanced(out):
            return None
        return out, "通用代码块语义合并：按空行分段，保留双方独有代码段", 0.75

    def _strategy_indent_aware(self, head, branch, filename):
        head_target = self._detect_indent(head)
        branch_target = self._detect_indent(branch)
        if not head_target or not branch_target:
            return None
        if head_target == branch_target:
            return None
        converted = [self._convert_indent(ln, head_target) for ln in branch]
        merged = list(head)
        for c in converted:
            if c not in merged:
                merged.append(c)
        merged = self._compress_blanks(merged)
        merged = self._fix_newlines(merged)
        if not self._brackets_balanced(merged):
            return None
        return merged, "缩进感知合并：将 branch 缩进统一为 HEAD 风格后追加独有行", 0.7

    def _strategy_confidence(self, head, branch, filename):
        head_set = set(head)
        branch_set = set(branch)
        if not head_set or not branch_set:
            return None
        overlap = len(head_set & branch_set) / len(head_set | branch_set)
        if overlap < 0.5:
            return None
        merged = list(head)
        for b in branch:
            if b not in merged:
                merged.append(b)
        merged = self._compress_blanks(merged)
        merged = self._fix_newlines(merged)
        if not self._brackets_balanced(merged):
            return None
        conf = round(0.5 + overlap * 0.3, 2)
        return merged, f"置信度评估合并：双方重叠率 {overlap:.0%}，合并保留双方内容", conf

    def _strategy_bracket_check(self, head, branch, filename):
        head_ok = self._brackets_balanced(head)
        branch_ok = self._brackets_balanced(branch)
        if head_ok and branch_ok:
            return None
        if head_ok and not branch_ok:
            return self._fix_newlines(head), "括号检查：HEAD 括号平衡而 branch 不平衡，保留 HEAD", 0.6
        if branch_ok and not head_ok:
            return self._fix_newlines(branch), "括号检查：branch 括号平衡而 HEAD 不平衡，采用 branch", 0.6
        return None

    def _strategy_general(self, head, branch, filename):
        head_set = set(head)
        branch_set = set(branch)
        if not head_set and not branch_set:
            return [], "通用合并：双方均为空", 1.0
        overlap = len(head_set & branch_set) / max(len(head_set | branch_set), 1)
        if overlap < 0.2:
            return None
        target_indent = self._detect_indent(head)
        merged = list(head)
        for b in branch:
            if b not in merged:
                if target_indent:
                    merged.append(self._convert_indent(b, target_indent))
                else:
                    merged.append(b)
        merged = self._compress_blanks(merged)
        merged = self._fix_newlines(merged)
        if not self._brackets_balanced(merged):
            return None
        conf = round(0.5 + overlap * 0.2, 2)
        return merged, f"通用合并：双方重叠率 {overlap:.0%}，保留 HEAD 并追加 branch 独有行", conf
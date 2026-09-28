#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""framework-rules 编号引用校验器（投资框架全家共享的操作门）

投资框架五个 skill 的 SKILL.md / references 大量以「framework-rules #N」引用全局规则条目。
编号是纯人肉维护的——引用指向已删除/改号的条目时不会报错，只会静默做错事。
本脚本把「引用有效性」变成机器门禁：扫描全部 md，对照 framework-rules.md 实存条目号，
悬空引用一律报错（事前避免 > 事后审查；与 verify-format.py 同一原则）。

用法：
    python3 check-rule-refs.py                 # 校验投资框架全家（5 个 skill 目录）
    python3 check-rule-refs.py <md 文件> [...]  # 只校验指定文件
退出码：0=全部引用有效 / 1=存在悬空引用 / 2=用法错误。

维护约定：framework-rules.md 删除或改号条目后，必须跑本脚本清引用；新条目号自动被识别。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FAMILY = [
    os.path.dirname(HERE),                       # investment-framework（本体；工作区 .agents/skills 是指向本体的软链）
    os.path.expanduser('~/.skills-manager/skills/investment-refine'),
    os.path.expanduser('~/.skills-manager/skills/investment-coarse-processor'),
    os.path.expanduser('~/.skills-manager/skills/investment-review'),
    os.path.expanduser('~/.skills-manager/skills/xueqiu-spyder'),
    os.path.expanduser('~/.skills-manager/skills/prediction-console'),
]
RULES_FILE = os.path.join(os.path.dirname(HERE), 'references', 'framework-rules.md')
REF_PAT = re.compile(r'framework-rules(?:\.md)?\s*[#＃]\s*(\d+)')


def load_rule_numbers():
    """framework-rules.md 的条目号 = 行首「N. 」（顶层编号列表）。"""
    with open(RULES_FILE, encoding='utf-8') as f:
        text = f.read()
    return {int(m.group(1)) for m in re.finditer(r'^(\d+)\.\s', text, re.M)}


def target_files(args):
    if args:
        return [p for p in args]
    files = []
    for d in FAMILY:
        for root, dirs, names in os.walk(d):
            dirs[:] = [x for x in dirs if x not in ('scripts', 'assets', '__pycache__')]
            for n in names:
                if n.endswith('.md') and not (os.path.abspath(os.path.join(root, n))
                                              == os.path.abspath(RULES_FILE)):
                    files.append(os.path.join(root, n))
    return sorted(files)


def main():
    args = sys.argv[1:]
    if not os.path.exists(RULES_FILE):
        print(f'✗ 找不到规则文件: {RULES_FILE}', file=sys.stderr)
        return 2
    rules = load_rule_numbers()
    if not rules:
        print('✗ framework-rules.md 未解析出任何条目号（编号格式应为行首「N. 」）', file=sys.stderr)
        return 2

    bad = total = 0
    for path in target_files(args):
        if not os.path.exists(path):
            print(f'✗ 文件不存在: {path}', file=sys.stderr)
            bad += 1
            continue
        with open(path, encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                for m in REF_PAT.finditer(line):
                    total += 1
                    num = int(m.group(1))
                    if num not in rules:
                        bad += 1
                        rel = os.path.relpath(path, os.path.dirname(FAMILY[0]))
                        print(f'✗ 悬空引用 {rel}:{i}  framework-rules #{num}（实存条目：'
                              f'{min(rules)}~{max(rules)}）')
    print(f'\n共校验 {total} 处 framework-rules 引用：有效 {total - bad}，悬空 {bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

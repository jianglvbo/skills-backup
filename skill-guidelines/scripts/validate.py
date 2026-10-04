#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 结构机械校验器（skill-guidelines 的执行层）

把「6 段结构 / 主文件行数 / name+description 规范 / 引用完整性 / 公共化红线 / 目录卫生」这些**能机器判定的项**
从『靠模型自检』变成『跑脚本看输出』——这正是本 skill 的「执行优于模拟」原则。

用法：
    python3 validate.py                     # 校验 ~/.agents/skills 下全部 skill
    python3 validate.py <skill 目录> [...]   # 只校验指定 skill
    python3 validate.py --json              # 输出 JSON（供其它工具消费）
    python3 validate.py --strict            # 把「提示」也算失败（默认提示不计入退出码）

判定口径：以 SKILL.md 为准；第三方安装的 skill（见 ~/.agents/.skill-lock.json）只报信息、不计失败。
退出码：0=无失败项 / 1=有失败项 / 2=用法错误。
"""
import argparse
import json
import os
import re
import sys

SECTIONS = {
    'Default stance': r'^#+\s*Default\s+[Ss]tance',
    'Workflow': r'^#+\s*Workflow',
    'Output format': r'^#+\s*Output\s+[Ff]ormat',
    'Relative files': r'^#+\s*Relative\s+[Ff]iles',
    'Source hierarchy': r'^#+\s*Source\s+[Hh]ierarchy',
    '自检': r'^#+\s*(自检|[Ss]elf-?[Cc]heck)',
}
PRIVATE_PAT = re.compile(r'~/\.(workbuddy|qwenworkcn|qoderworkcn|claude|codex)[\w/.\-]*')
RELPATH_PAT = re.compile(r'`((?:references|scripts|assets|templates|lib)/[\w\u4e00-\u9fff/.\-（）()]+)`')
NOISE_DIRS = {'.DS_Store', '__pycache__', 'node_modules', '.git', '.chrome-debug-profile', '.venv', 'venv'}
BIG_BYTES = 20 * 1024 * 1024      # 单文件 >20MB 视为不该放 skill 目录
MAX_LINES = 200


def _read(p):
    with open(p, encoding='utf-8', errors='replace') as f:
        return f.read()


def third_party_names():
    """第三方安装的 skill 名单（其写法不套本仓库模板，不判失败）。"""
    try:
        lock = json.loads(_read(os.path.expanduser('~/.agents/.skill-lock.json')))
        return set((lock.get('skills') or {}).keys())
    except Exception:
        return set()


def check_skill(skill_dir, third_party=False):
    name = os.path.basename(os.path.abspath(skill_dir))
    fails, notes = [], []
    sk = os.path.join(skill_dir, 'SKILL.md')
    if not os.path.exists(sk):
        return {'skill': name, 'fails': ['缺 SKILL.md'], 'notes': [], 'lines': 0, 'size_mb': 0}
    text = _read(sk)
    lines = text.split('\n')

    fm = re.match(r'^---\n(.*?)\n---', text, re.S)
    if not fm:
        fails.append('缺 YAML frontmatter')
        desc = ''
    else:
        body = fm.group(1)
        desc = (re.search(r'^description:\s*(.*?)(?=^\w+:|\Z)', body, re.S | re.M) or [None, ''])[1]
        nm = re.search(r'^name:\s*["\']?([^\s"\']+)', body, re.M)
        if not nm:
            fails.append('frontmatter 缺 name')
        else:
            nm_val = nm.group(1)
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', nm_val):
                fails.append(f'name 不合规范（小写字母/数字/连字符，无首尾或连续连字符）: {nm_val}')
            if len(nm_val) > 64:
                fails.append(f'name 超长（>64 字符）')
            if nm_val != name:
                fails.append(f'name 与目录名不一致: name={nm_val} 目录={name}')
        if not desc:
            fails.append('frontmatter 缺 description（AI 选择 skill 的唯一依据）')
        else:
            if len(desc) > 1024:
                fails.append(f'description 超长（{len(desc)} > 1024 字符）')
            # 指针要写明「到达条件」，而不是含某个魔术词。
            # 旧检查要求字面出现「触发词」，它奖励的正是它想防的事：
            # 往指针里塞同义词清单＝一个 branch 写好几遍。
            # 「有没有写明条件」措辞空间太大，机械判会误报，故只作提示不作门禁。
            if not re.search(r'时使用|时触发|时使|when|use\s+when|只用于|用于|触发词', desc, re.I):
                notes.append('description 可能未写明到达条件（人工确认何时该取用它）')
            triggers = re.findall(r'[「“]([^」”]{1,20})[」”]', desc)
            if len(triggers) >= 4:
                notes.append(f'description 列了 {len(triggers)} 个触发词，疑似同义重复（一个 branch 写多遍）')
            if not any(k in desc for k in ('排除', 'Exclusions', '区别于', '不用')):
                notes.append('description 无「排除条件」（易与相邻 skill 抢路由）')

    for sec, pat in SECTIONS.items():
        if not re.search(pat, text, re.M):
            fails.append(f'缺「{sec}」段')

    m = re.search(r'^#+\s*Default\s+[Ss]tance(.*?)(?=^#\s|\Z)', text, re.S | re.M)
    if m:
        stance = m.group(1)
        if '核心原则' not in stance and 'Core Principles' not in stance:
            notes.append('Default stance 无「核心原则」小节')
        if not any(k in stance for k in ('边界', '禁止行为', 'Boundaries', 'Prohibitions')):
            notes.append('Default stance 无「边界」小节（旧名「禁止行为」）')
        # 靠禁令转向会把被禁行为拉进上下文，让它更容易出现。
        neg = len(re.findall(r'绝不|不要|禁止|不得|勿\b', stance))
        if neg >= 4:
            notes.append(f'Default stance 有 {neg} 处否定式表述，逐条试改为正向目标')

    m = re.search(r'^#+\s*(自检|[Ss]elf-?[Cc]heck)(.*?)(?=^#\s|\Z)', text, re.S | re.M)
    if m:
        n = len(re.findall(r'^\s*[-*]\s*\[', m.group(2), re.M))
        if n < 3:
            fails.append(f'自检仅 {n} 条（要求 ≥3）')

    if len(lines) > MAX_LINES:
        fails.append(f'主文件 {len(lines)} 行 > {MAX_LINES}（细节应下沉 references/）')

    m = re.search(r'^#+\s*Relative\s+[Ff]iles(.*?)(?=^#\s|\Z)', text, re.S | re.M)
    if m and '执行' not in m.group(1) and 'execute' not in m.group(1).lower():
        notes.append('Relative files 未标注「读 / 执行」')

    for rel in sorted(set(RELPATH_PAT.findall(text))):
        if not os.path.exists(os.path.join(skill_dir, rel)):
            # 跨 skill 引用（形如 post-fetch `references/x.md`）由正文文字说明，这里只提示
            notes.append(f'引用的文件不存在（若为跨 skill 引用需写明归属）: {rel}')

    # 只在「非规则说明行」上判私有路径——描述红线本身的文字（含 不得/禁止/红线/示例）不算违规
    priv = set()
    for ln in lines:
        if any(k in ln for k in ('不得', '禁止', '红线', '示例', '反例', 'exempt')):
            continue
        priv.update(mm.group(0) for mm in PRIVATE_PAT.finditer(ln))
    if priv:
        fails.append('含 agent 私有路径（Ai 仓库公共化红线）: ' + ', '.join(sorted(priv)[:3]))

    noise, big, total = [], [], 0
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        for d in list(dirnames):
            if d in NOISE_DIRS:
                noise.append(os.path.relpath(os.path.join(dirpath, d), skill_dir))
                dirnames.remove(d)
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            total += size
            if fn == '.DS_Store':
                noise.append(os.path.relpath(fp, skill_dir))
            if size > BIG_BYTES:
                big.append(f'{os.path.relpath(fp, skill_dir)} ({size // 1048576}MB)')
    if noise:
        fails.append('目录内含噪音/私有产物: ' + ', '.join(noise[:4]) + ('…' if len(noise) > 4 else ''))
    if big:
        fails.append('含大文件（不该进 skill 目录/仓库）: ' + ', '.join(big[:3]))

    if third_party:
        notes = ['（第三方安装 skill：模板项不判失败）'] + notes
        fails = []
    return {'skill': name, 'fails': fails, 'notes': notes, 'lines': len(lines),
            'size_mb': round(total / 1048576, 1)}


def main():
    ap = argparse.ArgumentParser(description='Skill 结构机械校验（skill-guidelines 执行层）')
    ap.add_argument('skills', nargs='*', help='skill 目录；缺省校验 ~/.agents/skills 下全部')
    ap.add_argument('--root', default=os.path.expanduser('~/.agents/skills'))
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--strict', action='store_true', help='提示项也计入失败')
    a = ap.parse_args()

    targets = a.skills or [os.path.join(a.root, d) for d in sorted(os.listdir(a.root))
                           if os.path.isdir(os.path.join(a.root, d))]
    if not targets:
        print('没有可校验的 skill 目录', file=sys.stderr)
        return 2
    third = third_party_names()
    results = [check_skill(t, os.path.basename(os.path.abspath(t)) in third) for t in targets]

    if a.json:
        json.dump(results, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        nf = nt = 0
        for r in results:
            nf += len(r['fails'])
            nt += len(r['notes'])
            flag = '❌' if r['fails'] else ('⚠️' if r['notes'] else '✅')
            print(f"{flag} {r['skill']:<26} {r['lines']:>4} 行  {r['size_mb']:>6}MB")
            for x in r['fails']:
                print('     ✗', x)
            for x in r['notes']:
                print('     · ', x)
        print(f'\n共 {len(results)} 个 skill：失败项 {nf} 处，提示 {nt} 处')
    bad = sum(1 for r in results if r['fails'] or (a.strict and r['notes']))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

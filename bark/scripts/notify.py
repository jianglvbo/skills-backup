#!/usr/bin/env python3
"""Bark 推送到用户 iPhone（api.day.app，device key 已写死）。

用法: notify.py [选项] [标题] 正文|-
  标题缺省为 "ZCode"；正文传 "-" 时从 stdin 读（适合多行/长文本）。

选项:
  --level LEVEL    active(默认,亮屏)/timeSensitive(专注模式可弹)/passive(静默进列表)/critical(静音也响,需App授权)
  --group GROUP    消息分组，同组在通知中心归拢、可整组静音
  --id ID          通知唯一标识，相同 id 替换旧通知不堆叠（进度/状态类推送）
  --delete ID      删除指定 id 的通知（需系统设置里给 Bark 开「后台App刷新」）
  --url URL        点按通知跳转的链接（http 走 Universal Link）
  --sound NAME     铃声名，内置 32 种如 minuet/alarm/paymentsuccess/gotosleep/silence
  --icon URL       自定义通知图标 URL（iOS 15+，自动缓存）
  --image URL      展开通知显示的大图 URL
  --badge N        App 角标数字，0 清除角标
  --markdown       正文按 Markdown 发送（加粗/列表/标题/引用/代码块等，图片降级为链接）
  --copy TEXT      点按通知时复制的内容（缺省复制正文）
  --call           铃声循环播放 30 秒（强提醒）
  --volume N       critical 级别的音量 0-10（默认 5，仅 critical 生效）
  --ttl SECONDS    历史记录有效期，到期自动删（验证码/临时告警类）
  --archive / --no-archive   是否存入历史记录（缺省跟 App 内设置）

退出码: 0 = 推送成功（API code 200）；1 = 推送失败（打印返回体或异常）；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

DEVICE_KEY = "DYrdRmSCeBxCS4Puvg2n67"  # 用户 2026-09-26 确认非敏感，可写死
SERVER = "https://api.day.app/push"

OPTIONAL_KEYS = ("level", "group", "id", "url", "sound", "icon", "image", "copy")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="发 Bark 推送到用户 iPhone（各选项详见文件头 docstring）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("words", nargs="*", help="[标题] 正文；有 2 个时第 1 个是标题，正文恒为最后 1 个；传 - 从 stdin 读")
    p.add_argument("--level", choices=["active", "timeSensitive", "passive", "critical"])
    p.add_argument("--group")
    p.add_argument("--id", help="相同 id 替换旧通知")
    p.add_argument("--delete", metavar="ID", help="删除指定 id 的通知")
    p.add_argument("--url")
    p.add_argument("--sound")
    p.add_argument("--icon")
    p.add_argument("--image")
    p.add_argument("--badge", type=int)
    p.add_argument("--markdown", action="store_true", help="正文按 Markdown 发送")
    p.add_argument("--copy")
    p.add_argument("--call", action="store_true", help="循环响铃 30 秒")
    p.add_argument("--volume", type=int, choices=range(0, 11), metavar="0-10")
    p.add_argument("--ttl", type=int, help="历史记录有效期（秒）")
    p.add_argument("--archive", action=argparse.BooleanOptionalAction, default=None)
    return p


def split_words(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """位置参数解析：[标题] 正文——正文恒为最后一个，前面至多一个是标题。"""
    words = args.words
    if len(words) > 2:
        build_parser().error("位置参数至多两个：[标题] 正文")
    title = words[0] if len(words) == 2 else None
    body = words[-1] if words else None
    return title, body


def build_payload(args: argparse.Namespace, title: str | None, body: str) -> dict:
    payload = {"device_key": DEVICE_KEY}
    if args.delete:
        payload.update({"id": args.delete, "delete": "1"})
        return payload
    payload["markdown" if args.markdown else "body"] = body
    if title:
        payload["title"] = title
    for key in OPTIONAL_KEYS:
        if getattr(args, key):
            payload[key] = getattr(args, key)
    if args.badge is not None:
        payload["badge"] = args.badge
    if args.call:
        payload["call"] = "1"
    if args.volume is not None:
        payload["volume"] = args.volume
    if args.ttl is not None:
        payload["ttl"] = args.ttl
    if args.archive is not None:
        payload["isArchive"] = "1" if args.archive else "0"
    return payload


def main() -> int:
    args = build_parser().parse_args()
    title, body_arg = split_words(args)
    if args.delete:
        if body_arg:
            build_parser().error("--delete 模式不接受标题/正文")
        payload = build_payload(args, title, "")
    else:
        if body_arg is None:
            build_parser().error("缺少正文（或用 --delete ID 删除通知）")
        body = sys.stdin.read() if body_arg == "-" else body_arg
        if not body.strip():
            print("正文为空", file=sys.stderr)
            return 2
        payload = build_payload(args, title, body)

    req = urllib.request.Request(
        SERVER,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15).read().decode()
    except Exception as exc:
        print(f"推送失败: {exc}", file=sys.stderr)
        return 1
    print(resp)
    return 0 if '"code":200' in resp else 1


if __name__ == "__main__":
    sys.exit(main())

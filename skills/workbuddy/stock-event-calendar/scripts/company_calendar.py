#!/usr/bin/env python3
"""读取巨潮资讯网「交易信息 → 公司日历」，输出其中排定的事件。

用法:
  python3 company_calendar.py --symbol 长鑫科技
  python3 company_calendar.py --symbol 688825

只做一件事：把公司日历里的条目打出来。查不到就是查不到，不推测、不补全。
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CNINFO = "https://www.cninfo.com.cn"
API = "https://api.cninfo.com.cn"


def fetch(url, data=None):
    """返回响应文本；失败返回 None（由调用方区分「查不到」与「接口坏了」）。"""
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": UA,
            "Referer": f"{CNINFO}/",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! 请求失败 {url} -> {exc}", file=sys.stderr)
        return None


def resolve(keyword):
    """名称或代码 -> 候选标的列表（code / name / category）。"""
    body = urllib.parse.urlencode({"keyWord": keyword, "maxNum": 10}).encode()
    raw = fetch(f"{CNINFO}/new/information/topSearch/query", body)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return [
        {"code": i.get("code"), "name": i.get("zwjc"), "category": i.get("category") or ""}
        for i in data
    ]


def pick(cands):
    """从候选中选出唯一标的，A 股优先。

    A+H 同名时只处理 A 股，H 股与债券类一律忽略——这是本技能的既定口径。
    注意：判定依据是 category（"A股"/"港股"/"资产支持证券"…），
    不能用 type 字段，000002 这类深市 A 股的 type 也是 shj，不可靠。

    返回 (selected, ignored, ambiguous)：
      selected  —— 选中的标的，None 表示无法确定
      ignored   —— 因非 A 股而被忽略的候选
      ambiguous —— 仍有多只 A 股无法区分时，返回这些候选
    """
    a_shares = [c for c in cands if c["category"] == "A股"]
    if len(a_shares) == 1:
        ignored = [c for c in cands if c is not a_shares[0]]
        return a_shares[0], ignored, None
    if len(a_shares) > 1:
        return None, [], a_shares
    # 没有 A 股：仅在候选唯一时按原样处理（如用户直接给了港股代码）
    if len(cands) == 1:
        return cands[0], [], None
    return None, [], cands


def calendar(code):
    """公司日历。返回 (msg, events)；接口不通返回 (None, None)。"""
    raw = fetch(f"{API}/service/tradingTips?secCode={code}")
    if not raw:
        return None, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    return data.get("msg"), data.get("hint") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="股票名称或 6 位代码")
    args = ap.parse_args()

    cands = resolve(args.symbol.strip())
    if cands is None:
        print("未能完成检索（接口异常），请稍后重试。")
        return 1
    if not cands:
        print(f"未检索到「{args.symbol}」，请确认名称或代码。")
        return 1

    sec, ignored, ambiguous = pick(cands)
    if ambiguous:
        print("命中多个标的，请用 6 位代码重跑：")
        for c in ambiguous:
            print(f"  {c['code']}  {c['name']}  ({c['category']})")
        return 2

    code, name = sec["code"], sec["name"]

    msg, events = calendar(code)

    if events is None:
        # 关键区分：接口不通 ≠ 没有事件。不能把「查不到」说成「为空」。
        print(f"{name}（{code}）：未能获取公司日历（接口异常），无法判断是否有重大事件。")
        return 1

    print(f"{name}（{code}）")

    # 被忽略的标的要说清楚，否则用户不知道结果对应的是哪个市场
    if ignored:
        hk = [c for c in ignored if c["category"] == "港股"]
        others = [c for c in ignored if c["category"] != "港股"]
        parts = []
        if hk:
            parts.append("同名港股 " + "、".join(c["code"] for c in hk) + " 未纳入")
        if others:
            parts.append(f"另有 {len(others)} 个非 A 股标的未纳入")
        if parts:
            print(f"（已按 A 股处理：{'；'.join(parts)}）")

    if not events:
        print("重大事件为空")
        return 0

    # 接口返回不保证按日期排序，这里统一按日期升序，无日期的排最后
    events = sorted(events, key=lambda e: (e.get("publishTime") or "9999-12-31"))
    for e in events:
        date = e.get("publishTime") or "日期未定"
        title = e.get("title") or ""
        content = e.get("content") or ""
        line = f"{date}  {title}"
        if content:
            line += f"  {content}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""交易日历工具：求上一交易日、生成默认查询窗口。

本 skill 的默认窗口口径（2026-10 起）：
    窗口 = [严格小于今天的最近交易日, 今天]

为什么不是"当日减一天"：
- 今天为交易日（周一至周五、非节假日）→ 窗口是「上一个交易日 + 今天」，一次覆盖完整的一个交易日周期。
  公告多在下班后与夜间披露，只看今天会漏掉昨天盘后的那一批。
- 今天非交易日（周末/节假日）→ 右侧仍是今天，左侧是最近交易日。周六跑就覆盖「周五 + 周六」，
  周末照常披露的公告不会漏。
- 跨长假 → 窗口是连续日期区间，不跳过中间日期。节后首日跑，会把整个假期披露的公告一并捞出来。

交易日来自上证综指（000001.SH）的日 K 日期——交易所数据本身，比手工维护节假日表可靠，
也自动涵盖临时休市与调休。接口不可用时退化为"仅跳周末"的估算，并在结果里标 estimated=True，
调用方必须把这一点转述给用户。

用法:
  python3 trading_day.py                      # 打印今天的默认窗口
  python3 trading_day.py --today 2026-10-08   # 指定日期（模拟/测试）
  python3 trading_day.py --json               # 机器可读
  python3 trading_day.py --list-trading 10    # 列出最近 N 个交易日
"""

import argparse
import datetime as dt
import json
import sys
import urllib.request

CST = dt.timezone(dt.timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def today_cst():
    return dt.datetime.now(CST).date()


def _fetch_json(url, referer):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Referer": referer, "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _days_tencent(limit):
    """腾讯指数日 K。返回升序日期字符串列表，失败返回 []。"""
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param=sh000001,day,,,{limit},qfq"
    )
    try:
        j = _fetch_json(url, "https://gu.qq.com/")
        node = (j.get("data") or {}).get("sh000001") or {}
        rows = node.get("day") or node.get("qfqday") or []
        return [r[0] for r in rows if r and len(r[0]) == 10]
    except Exception:  # noqa: BLE001
        return []


def _days_eastmoney(limit):
    """东方财富指数日 K，备用源。返回升序日期字符串列表，失败返回 []。"""
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        "?secid=1.000001&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56"
        f"&klt=101&fqt=1&end=20500101&lmt={limit}"
    )
    try:
        j = _fetch_json(url, "https://quote.eastmoney.com/")
        rows = ((j.get("data") or {}).get("klines")) or []
        return [r.split(",")[0] for r in rows if r]
    except Exception:  # noqa: BLE001
        return []


def recent_trading_days(limit=40):
    """最近 limit 个交易日，升序。两个源都失败时返回 []。"""
    days = _days_tencent(limit) or _days_eastmoney(limit)
    return sorted(set(days))


def _prev_weekday(day):
    """仅跳过周末的粗略估算，接口不可用时兜底。"""
    d = day - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def prev_trading_day(today=None, calendar=None):
    """严格小于 today 的最近交易日。

    返回 (date_str, estimated)。estimated=True 表示交易日历不可用，
    结果按"只跳周末"估算，跨法定节假日时可能不准。
    """
    today = today or today_cst()
    days = calendar if calendar is not None else recent_trading_days(40)
    earlier = [d for d in days if d < today.isoformat()]
    if earlier:
        return max(earlier), False
    return _prev_weekday(today), True


def default_window(today=None):
    """默认查询窗口 = [上一交易日, 今天]。

    返回 (start, end, meta)，meta 含 previous_trading_day / estimated / calendar_source。
    """
    today = today or today_cst()
    days = recent_trading_days(40)
    prev, estimated = prev_trading_day(today, days)
    meta = {
        "today": today.isoformat(),
        "is_trading_day": today.isoformat() in set(days) if days else None,
        "previous_trading_day": prev,
        "estimated": estimated,
        "calendar_days": len(days),
    }
    return prev, today.isoformat(), meta


def describe(start, end, meta):
    """给人类看的一句话说明，供调用方原样转述。"""
    if not meta.get("estimated"):
        return f"窗口 {start} ~ {end}（当日 + 上一交易日；已跳过周末与法定节假日）"
    return (
        f"窗口 {start} ~ {end}（当日 + 上一「工作日」；**交易日历获取失败，"
        f"仅按周末规则估算，若跨法定节假日请手动核对**）"
    )


def main():
    ap = argparse.ArgumentParser(description="交易日历：求上一交易日与默认窗口")
    ap.add_argument("--today", help="指定今天 YYYY-MM-DD，默认取北京时间当天")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--list-trading", type=int, metavar="N", help="列出最近 N 个交易日")
    args = ap.parse_args()

    if args.list_trading:
        days = recent_trading_days(max(args.list_trading, 1))
        if not days:
            print("未能获取交易日历（两个数据源都失败）")
            return 2
        for d in days[-args.list_trading:]:
            print(d)
        return 0

    if args.today:
        try:
            day = dt.date.fromisoformat(args.today)
        except ValueError:
            print(f"日期格式不对：{args.today}，应为 YYYY-MM-DD")
            return 2
    else:
        day = None

    start, end, meta = default_window(day)

    if args.json:
        print(json.dumps({"start": start, "end": end, **meta}, ensure_ascii=False, indent=2))
        return 0

    print(describe(start, end, meta))
    if meta["is_trading_day"] is False:
        print(f"  注意：{meta['today']} 非交易日，左侧窗口取最近交易日 {start}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

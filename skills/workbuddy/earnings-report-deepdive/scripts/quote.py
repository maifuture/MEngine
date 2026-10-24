#!/usr/bin/env python3
"""A 股行情快照 + 公告日对齐器（东方财富主源，腾讯备用源）。

回答两个问题：
  1) 这只票现在什么价位、什么体量、什么估值、上市以来极值在哪？
  2) 某条公告披露后，市场第一个能反应的交易日走成什么样？

用法:
  python3 quote_snapshot.py --symbol 688825 --kline 15
  python3 quote_snapshot.py --symbol 688825 --align 2026-10-17 --issue-price 8.66

--align 是关键：给定公告披露日（可以是周六/节假日），自动定位其后的第一个交易日，
给出当日涨跌幅、成交额与放量倍数。周末公告的市场反应只能看这一天。
免费接口会限流，故内置腾讯备用源（备用源无成交额，用成交量替代做量比）。
仅依赖标准库。
"""

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CST = dt.timezone(dt.timedelta(hours=8))
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def secid(code):
    return f"{'1' if code.startswith('6') else '0'}.{code}"


def txcode(code):
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return f"sz{code}"


def fetch(url, encoding="utf-8", retries=2, referer=None):
    """返回文本；全部失败返回 None（由调用方决定是否切换备用源）。"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": referer or "https://quote.eastmoney.com/"}
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode(encoding, "ignore")
        except Exception:  # noqa: BLE001
            if attempt + 1 < retries:
                time.sleep(1.2 * (attempt + 1))
    return None


def scaled(value):
    if value is None or value == "-":
        return None
    try:
        return round(int(value) / 100.0, 2)
    except (TypeError, ValueError):
        return None


# ---------------- 快照 ----------------

def _snapshot_em(code):
    fields = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f84,f85,f116,f117,f162,f167,f168,f169,f170"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid(code)}&fields={fields}"
    raw = fetch(url, retries=1)
    if not raw:
        return None
    data = (json.loads(raw) or {}).get("data") or {}
    if not data:
        return None
    return {
        "source": "eastmoney",
        "code": data.get("f57"),
        "name": data.get("f58"),
        "last": scaled(data.get("f43")),
        "prev_close": scaled(data.get("f60")),
        "open": scaled(data.get("f46")),
        "high": scaled(data.get("f44")),
        "low": scaled(data.get("f45")),
        "change_pct": scaled(data.get("f170")),
        "change": scaled(data.get("f169")),
        "amount_yuan": data.get("f48") or None,
        "turnover_pct": scaled(data.get("f168")),
        "market_cap_yuan": data.get("f116") or None,
        "float_cap_yuan": data.get("f117") or None,
        "total_shares": data.get("f84"),
        "float_shares": data.get("f85"),
        "pe_ttm": scaled(data.get("f162")),
        "pb": scaled(data.get("f167")),
    }


def _snapshot_tx(code):
    raw = fetch(f"https://qt.gtimg.cn/q={txcode(code)}", encoding="gbk", referer="https://gu.qq.com/")
    if not raw or "=" not in raw:
        return None
    body = raw.split('="', 1)[1].rstrip('";\n')
    f = body.split("~")
    if len(f) < 49:
        return None

    def num(i, div=1.0):
        try:
            return round(float(f[i]) / div, 2)
        except (IndexError, ValueError):
            return None

    total_cap = num(45)
    float_cap = num(44)
    amt_wan = num(37)  # 腾讯成交额单位为万元
    return {
        "source": "tencent",
        "code": f[2],
        "name": f[1],
        "last": num(3),
        "prev_close": num(4),
        "open": num(5),
        "high": num(33),
        "low": num(34),
        "change_pct": num(32),
        "change": num(31),
        "amount_yuan": amt_wan * 1e4 if amt_wan else None,
        "turnover_pct": num(38),
        "market_cap_yuan": total_cap * 1e8 if total_cap else None,
        "float_cap_yuan": float_cap * 1e8 if float_cap else None,
        "total_shares": None,
        "float_shares": None,
        "pe_ttm": num(52),
        "pb": num(46),
    }


def snapshot(code):
    snap = _snapshot_em(code) or _snapshot_tx(code)
    if not snap:
        raise SystemExit(f"[quote] 主备数据源均失败，无法获取 {code} 行情")
    if snap.get("last") in (None, 0):
        snap["pre_market"] = True
    else:
        snap["pre_market"] = False
        if not snap.get("change") and snap.get("prev_close"):
            snap["change"] = round(snap["last"] - snap["prev_close"], 2)
        if not snap.get("change_pct") and snap.get("prev_close"):
            snap["change_pct"] = round(snap["change"] / snap["prev_close"] * 100, 2)
    return snap


# ---------------- 日 K ----------------

def _klines_em(code, limit):
    fields = (
        "?secid={sid}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        "&klt=101&fqt=1&end=20500101&lmt={lmt}"
    ).format(sid=secid(code), lmt=limit)
    # 主域被限流时换同族备用域，最后才降级到腾讯
    for host in ("https://push2his.eastmoney.com", "https://push2.eastmoney.com"):
        raw = fetch(host + "/api/qt/stock/kline/get" + fields, retries=2)
        if not raw:
            continue
        try:
            data = (json.loads(raw) or {}).get("data") or {}
        except json.JSONDecodeError:
            continue
        if not data.get("klines"):
            continue
        rows = []
        for line in data["klines"]:
            p = line.split(",")
            rows.append(
                {
                    "date": p[0],
                    "open": float(p[1]),
                    "close": float(p[2]),
                    "high": float(p[3]),
                    "low": float(p[4]),
                    "volume": float(p[5]),
                    "amount": float(p[6]) or None,
                    "amplitude": float(p[7]),
                    "change_pct": float(p[8]),
                    "change": float(p[9]),
                    "turnover": float(p[10]),
                }
            )
        return rows, data.get("dktotal"), data.get("name"), "eastmoney"
    return None


def _klines_tx(code, limit):
    # 多取一根，用于给首根算涨跌幅，算完丢弃
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={txcode(code)},day,,,{limit + 1},qfq"
    raw = fetch(url, referer="https://gu.qq.com/")
    if not raw:
        return None
    data = (json.loads(raw) or {}).get("data") or {}
    node = data.get(txcode(code)) or {}
    series = node.get("qfqday") or node.get("day")
    if not series:
        return None
    rows = []
    for p in series:
        rows.append(
            {
                "date": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "volume": float(p[5]),
                "amount": None,
                "amplitude": None,
                "change_pct": None,
                "change": None,
                "turnover": None,
            }
        )
    for i in range(1, len(rows)):
        prev = rows[i - 1]["close"]
        if prev:
            rows[i]["change"] = round(rows[i]["close"] - prev, 2)
            rows[i]["change_pct"] = round((rows[i]["close"] / prev - 1) * 100, 2)
    # 只有确认还有更早数据时才丢掉垫算的那一根；否则保留真实上市首日
    if len(series) == limit + 1:
        rows = rows[1:]
    return rows, len(rows), None, "tencent"


def klines(code, limit=250):
    out = _klines_em(code, limit) or _klines_tx(code, limit)
    if not out:
        raise SystemExit(f"[quote] 主备数据源均失败，无法获取 {code} 日 K")
    return out


def main():
    ap = argparse.ArgumentParser(description="行情快照与公告日对齐")
    ap.add_argument("--symbol", required=True, help="股票代码，如 688825")
    ap.add_argument("--kline", type=int, default=15, help="打印最近 N 个交易日")
    ap.add_argument("--align", help="公告披露日 YYYY-MM-DD，定位其后首个交易日表现")
    ap.add_argument("--issue-price", type=float, help="发行价，用于计算相对发行价涨跌幅")
    ap.add_argument("--out", help="输出 JSON 文件路径")
    args = ap.parse_args()

    snap = snapshot(args.symbol)
    rows, total, _, src = klines(args.symbol, 250)
    if not rows:
        raise SystemExit("[quote] 无 K 线数据")

    listing = rows[0]["date"] if total and total < 250 else None
    payload = {
        "snapshot": snap,
        "listing_date": listing,
        "total_bars": total,
        "kline_source": src,
        "klines": rows[-args.kline:],
    }

    if args.issue_price and snap.get("last"):
        payload["vs_issue_price_pct"] = round((snap["last"] / args.issue_price - 1) * 100, 2)

    print(f"== {snap['name']} ({snap['code']}) ==  [数据源 {snap['source']}]")
    if snap["pre_market"]:
        print(f"  盘前/未开盘，昨收 {snap['prev_close']} 元")
    else:
        print(f"  最新 {snap['last']} 元   {snap['change']:+.2f} ({snap['change_pct']:+.2f}%)")
    dash = lambda v: "—" if not v else f"{v}"
    print(f"  昨收 {snap['prev_close']}   开 {dash(snap['open'])}   高 {dash(snap['high'])}   低 {dash(snap['low'])}")
    if snap.get("market_cap_yuan"):
        print(f"  总市值 {snap['market_cap_yuan']/1e8:,.0f} 亿元   流通市值 {(snap.get('float_cap_yuan') or 0)/1e8:,.0f} 亿元")
    print(f"  PE(动) {dash(snap.get('pe_ttm'))}   PB {dash(snap.get('pb'))}   换手 {dash(snap.get('turnover_pct'))}%")
    if listing:
        print(f"  上市首日（K 线首根）{listing}，上市以来共 {total} 个交易日")
    if payload.get("vs_issue_price_pct") is not None:
        print(f"  相对发行价 {args.issue_price} 元：{payload['vs_issue_price_pct']:+.2f}%")

    print(f"\n-- 最近 {min(args.kline, len(rows))} 个交易日 --" + ("  [备用源，无成交额]" if src == "tencent" else ""))
    for r in rows[-args.kline:]:
        wd = WEEKDAYS[dt.date.fromisoformat(r["date"]).weekday()]
        chg = f"{r['change_pct']:+.2f}" if r["change_pct"] is not None else "—"
        amt = f"{r['amount']/1e8:.2f}" if r["amount"] else "—"
        print(f"  {r['date']} {wd} 收{r['close']:>8.2f}  {chg:>7}%  额{amt:>9}亿")

    if listing:
        lo = min(rows, key=lambda r: r["low"])
        hi = max(rows, key=lambda r: r["high"])
        payload["extreme"] = {"low": lo["low"], "low_date": lo["date"], "high": hi["high"], "high_date": hi["date"]}
        print(f"\n  上市以来最低 {lo['low']}（{lo['date']}）   最高 {hi['high']}（{hi['date']}）")
        print("  判定「创新低/新高」必须区分盘中价与收盘价，两者结论可能不同。")

    if args.align:
        target = dt.date.fromisoformat(args.align)
        after = [r for r in rows if dt.date.fromisoformat(r["date"]) > target]
        if not after:
            print(f"\n[align] {args.align} 之后暂无交易日数据")
        else:
            hit = after[0]
            idx = rows.index(hit)
            base = rows[max(0, idx - 5):idx]
            metric = "amount" if hit["amount"] else "volume"
            avg = sum(r[metric] for r in base) / len(base) if base and all(r[metric] for r in base) else None
            wd = WEEKDAYS[dt.date.fromisoformat(hit["date"]).weekday()]
            gap = (dt.date.fromisoformat(hit["date"]) - target).days
            info = {
                "announce_date": args.align,
                "first_trading_day": hit["date"],
                "weekday": wd,
                "gap_in_days": gap,
                "close": hit["close"],
                "change_pct": hit["change_pct"],
                "amount_yuan": hit["amount"],
                "volume_ratio_vs_5d": round(hit[metric] / avg, 2) if avg else None,
            }
            payload["align"] = info
            amt_txt = f"{hit['amount']/1e8:.2f} 亿元" if hit["amount"] else "（备用源无成交额）"
            print("\n-- 公告日对齐 --")
            print(f"  披露日 {args.align} -> 首个交易日 {hit['date']} {wd}（相隔 {gap} 天）")
            print(f"  当日 {hit['change_pct']:+.2f}%（收 {hit['close']}），成交 {amt_txt}，量比 {info['volume_ratio_vs_5d'] or '—'}")
            if gap > 1:
                print("  注意：披露日非交易日，市场反应被推迟，公告的利好/利空要从这一天倒推。")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.out}")


if __name__ == "__main__":
    main()

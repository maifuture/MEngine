#!/usr/bin/env python3
"""巨潮资讯网 (cninfo.com.cn) 公告抓取器。

定位股票 -> 拉公告列表 -> 下载 PDF -> 提取文本 -> 输出结构化 JSON 与人类可读摘要。

用法:
  python3 cninfo_fetch.py --symbol 688825                      # 默认窗口：当日 + 上一交易日
  python3 cninfo_fetch.py --symbol 688825 --days 1              # 仅当日
  python3 cninfo_fetch.py --keyword 长鑫 --days 30 --out ./cninfo_out   # 近期模式：一个月
  python3 cninfo_fetch.py --symbol 688825 --start 2026-09-20 --end 2026-10-21
  python3 cninfo_fetch.py --symbol 688825 --no-download

默认窗口按真实交易日历（上证综指日 K）跳过周末与法定节假日，取 [上一交易日, 今天]。

仅依赖标准库；PDF 文本提取需 pypdf（缺失时自动降级为只下载，并打印安装提示）。
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 同目录模块（交易日历），脚本可能被绝对路径调用，显式补 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trading_day  # noqa: E402

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BASES = ("https://www.cninfo.com.cn", "http://www.cninfo.com.cn")
CST = dt.timezone(dt.timedelta(hours=8))
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 标题关键词 -> 分量提示。用于快速识别需要精读的公告，不是评级。
HEAVY_PATTERNS = [
    (r"业绩预告|业绩快报|季度报告|年度报告|半年度报告", "财务"),
    (r"回购", "回购"),
    (r"减持|增持|权益变动|要约收购", "股东行为"),
    (r"重大资产重组|收购|出售资产|资产置换|股权转让", "资本运作"),
    (r"发行|可转债|定增|向特定对象", "融资"),
    (r"诉讼|仲裁|处罚|立案|风险警示|退市|问询", "风险"),
    (r"分红|利润分配|权益分派", "分配"),
    (r"关联交易", "关联交易"),
    (r"募投|募集资金|超募", "募投"),
    (r"对外投资|投资建设|扩产|项目", "投资"),
    (r"股东会|股东大会|董事会决议", "决议"),
    (r"股票交易异常波动|澄清", "异动"),
    (r"停牌|复牌", "停复牌"),
    (r"高管|辞职|聘任", "人事"),
    (r"章程|治理制度|议事规则|管理制度", "治理"),
]
# 判定为"程序性/低信息量"的标题特征
PROCEDURAL = re.compile(
    r"法律意见书|核查意见|审核报告|验资报告|见证|决议公告|会议资料|通知$"
    r"|投资者关系活动记录表"
)
# A+H 公司会把港股日常披露同步到 A 股公告列表，对判断 A 股影响基本是噪音
HK_DISCLOSURE = re.compile(r"H股公告|港股公告|翌日披露|月报表|董事会会议通告|股东大会通告|股份发行人的证券变动")


def http_json(path, data, referer=None, retries=2):
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
    last = None
    for base in BASES:
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    base + path,
                    data=body,
                    headers={
                        "User-Agent": UA,
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Accept": "*/*",
                        "Referer": referer or (base + "/new/index"),
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8", "ignore"))
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(1.0 * (attempt + 1))
    raise SystemExit(f"[cninfo] 请求失败 {path}: {last}")


def http_bytes(url, referer="https://www.cninfo.com.cn/", retries=2):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise SystemExit(f"[cninfo] 下载失败 {url}: {last}")


def column_for(code):
    if code.startswith("6"):
        return "sse"
    if code.startswith(("0", "3")):
        return "szse"
    if code.startswith(("4", "8", "9")):
        return "bj"
    return "sse"


def resolve_stock(keyword=None, symbol=None):
    key = symbol or keyword
    hits = http_json(
        "/new/information/topSearch/query",
        {"keyWord": key, "maxNum": 10},
        referer="https://www.cninfo.com.cn/new/index",
    ) or []
    if not hits:
        raise SystemExit(f"[cninfo] 未找到标的: {key}")
    if symbol:
        for h in hits:
            if h.get("code") == symbol:
                return h
    if len(hits) == 1:
        return hits[0]
    # A 股优先（同一名称常同时命中港股/新三板，取错市场会导致后续列表为空）
    a_share = [h for h in hits if h.get("category") == "A股"]
    if len(a_share) == 1:
        return a_share[0]
    listing = "\n".join(
        f"  {h.get('code')} {h.get('zwjc')} ({h.get('category')})" for h in hits
    )
    print(f"[cninfo] 关键词命中多个标的，务必确认代码：\n{listing}", file=sys.stderr)
    return (a_share or hits)[0]


def ts_to_date(ms):
    return dt.datetime.fromtimestamp(ms / 1000, CST).date()


def guess_kind(title):
    for pattern, label in HEAVY_PATTERNS:
        if re.search(pattern, title or ""):
            return label
    return "其他"


def extract_text(pdf_path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, "pypdf 未安装，跳过文本提取"
    try:
        reader = PdfReader(pdf_path)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return text, None
    except Exception as exc:  # noqa: BLE001
        return None, f"PDF 解析失败: {exc}"


def pypdf_available():
    """检测 PDF 文本提取依赖。缺失时必须显式告警，不能让"没装库"看起来像"文件里没文本"。"""
    try:
        from pypdf import PdfReader  # noqa: F401
        return True
    except ImportError:
        return False


def market_probe(day):
    """全市场某日公告总条数，用于 0 条时自检接口是否正常。

    返回 (条数, 错误说明)。条数为 None 表示请求失败。
    目的：把"公司没披露"与"接口没打通/代码错配"区分开，但不产生任何分析动作。
    """
    try:
        j = http_json(
            "/new/hisAnnouncement/query",
            {
                "pageNum": 1,
                "pageSize": 30,
                "column": "sse",
                "tabName": "fulltext",
                "stock": "",
                "seDate": f"{day}~{day}",
                "isHLtitle": "true",
            },
            referer="https://www.cninfo.com.cn/",
        )
        return j.get("totalRecordNum"), None
    except SystemExit as exc:
        return None, str(exc)


def verify_market(date_arg, today):
    """接口自证：只回答"全市场某日有多少条公告"，不涉及任何标的、不产生分析动作。

    用途：某标的当日 0 条时，把"公司确无披露"与"接口没打通／代码错配"区分开。

    退出码：0 = 接口正常且全市场有公告；1 = 接口通但全市场 0 条（疑似非交易日）；
            2 = 请求失败或日期非法（无法确认）。
    """
    day = today.isoformat() if date_arg in (None, "", "today") else date_arg
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        print(f"日期格式不对：{day}，应为 YYYY-MM-DD")
        return 2

    n, err = market_probe(day)
    if n is None:
        print(f"接口自检失败：未能取到 {day} 的全市场公告数（{err or '无返回'}）")
        print("结论：无法确认接口是否正常，不要断言公司无披露。")
        return 2

    print(f"接口自检：全市场 {day} 共 {n} 条")
    if n:
        print("反证通过，接口正常。若某标的当日 0 条，可判定其确无披露。")
        return 0
    print("反证未通过：全市场亦为 0 条，大概率是非交易日或接口异常，不要断言公司无披露。")
    return 1


def main():
    ap = argparse.ArgumentParser(description="巨潮资讯网公告抓取")
    ap.add_argument("--keyword", help="股票名称或简称关键词")
    ap.add_argument("--symbol", help="股票代码，如 688825")
    ap.add_argument("--days", type=int, default=None,
                    help="自然日窗口：从今天往前 N 个自然日（含今天）。省略 = 默认窗口「当日 + 上一交易日」（跳过周末与法定节假日）；--days 1 = 仅当日；近期模式用 --days 30（一个月上限）")
    ap.add_argument("--start", help="起始日 YYYY-MM-DD，与 --end 配合使用")
    ap.add_argument("--end", help="截止日 YYYY-MM-DD")
    ap.add_argument("--out", default="./cninfo_out", help="输出目录")
    ap.add_argument("--no-download", action="store_true", help="只列公告，不下载 PDF")
    ap.add_argument(
        "--verify-market", nargs="?", const="today", metavar="DATE",
        help="只做接口自证：打印全市场某日公告总条数后退出，不需要 --keyword/--symbol。DATE 省略则为今天",
    )
    ap.add_argument("--today", metavar="DATE",
                    help="把「今天」覆盖为指定日期 YYYY-MM-DD（调试/补算口径用，默认取北京时间当天）")
    args = ap.parse_args()

    today = dt.datetime.now(CST).date()
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            ap.error("--today 需要 YYYY-MM-DD 格式")

    if args.verify_market:
        return verify_market(args.verify_market, today)

    if not args.keyword and not args.symbol:
        ap.error("需要 --keyword 或 --symbol")
    window_meta = None
    if args.start and args.end:
        start, end = args.start, args.end
    elif args.days is None:
        # 默认窗口：当日 + 上一交易日（跳过周末与法定节假日）
        start, end, window_meta = trading_day.default_window(today)
    else:
        end_d = today
        start_d = today - dt.timedelta(days=max(args.days - 1, 0))
        start, end = start_d.isoformat(), end_d.isoformat()

    stock = resolve_stock(args.keyword, args.symbol)
    code, org_id, name = stock["code"], stock["orgId"], stock.get("zwjc") or ""
    column = column_for(code)

    result = http_json(
        "/new/hisAnnouncement/query",
        {
            "pageNum": 1,
            "pageSize": 100,
            "column": column,
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{code},{org_id}",
            "searchkey": "",
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{start}~{end}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        },
        referer=f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}&orgId={org_id}",
    )

    raw = result.get("announcements") or []
    items = []
    for a in raw:
        d = ts_to_date(a["announcementTime"])
        title = (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
        adjunct = a.get("adjunctUrl") or ""
        is_hk = bool(HK_DISCLOSURE.search(title))
        items.append(
            {
                "date": d.isoformat(),
                "weekday": WEEKDAYS[d.weekday()],
                "is_weekend": d.weekday() >= 5,
                "id": a.get("announcementId"),
                "title": title,
                "kind": guess_kind(title),
                "procedural": bool(PROCEDURAL.search(title)) or is_hk,
                "hk_disclosure": is_hk,
                "pdf_url": f"https://static.cninfo.com.cn/{adjunct}" if adjunct else None,
                "size_kb": a.get("adjunctSize"),
            }
        )
    items.sort(key=lambda x: (x["date"], x["id"] or ""), reverse=True)

    warnings = []
    empty_notes = []
    probe = None
    if not items:
        # 0 条时自动做一次接口自检，避免把"接口没打通"误报成"公司无披露"。
        # 自检只判断接口是否正常，不构成任何分析动作。
        n, err = market_probe(end)
        probe = {"date": end, "market_total": n, "error": err}
        if start == end:
            end_d0 = dt.date.fromisoformat(end)
            empty_notes.append(f"{start}（{WEEKDAYS[end_d0.weekday()]}）无公告。口径为仅当日（--days 1）。")
        elif window_meta is not None:
            empty_notes.append(
                f"{start} ~ {end} 内无公告（默认口径：当日 + 上一交易日，已跳过周末与法定节假日）。"
                + ("⚠ 交易日历获取失败，上一交易日为按周末规则估算，跨假期请手动核对。" if window_meta.get("estimated") else "")
            )
        else:
            empty_notes.append(f"区间 {start} ~ {end} 内无公告。窗口固定为一个月，不要为找公告继续扩窗；如与预期不符，先核对代码/orgId。")
        if err:
            empty_notes.append(f"接口自检失败：{err}。反证未通过 → 手动补跑一次；仍失败才写「未能确认」，不能断言公司无披露。")
        elif n == 0:
            empty_notes.append(f"接口自检：全市场 {end} 亦为 0 条 → 反证未通过，大概率是非交易日或接口异常，请手动补跑一次再下结论。")
        else:
            empty_notes.append(f"接口自检：全市场 {end} 共 {n} 条 → 反证通过，接口正常，公司确无披露。")

    if not args.no_download and items:
        os.makedirs(args.out, exist_ok=True)
        if not pypdf_available():
            warnings.append(
                "pypdf 未安装：本批次只下载了 PDF，未提取任何文本，读不到原文。"
                "装好后重跑一次：python3 -m pip install pypdf（Windows 用 python -m pip install pypdf）"
            )
        for it in items:
            if not it["pdf_url"]:
                continue
            pdf_path = os.path.join(args.out, f"{it['id']}.PDF")
            if not os.path.exists(pdf_path):
                open(pdf_path, "wb").write(http_bytes(it["pdf_url"]))
            it["local_pdf"] = pdf_path
            text, err = extract_text(pdf_path)
            if text is None:
                it["text_error"] = err
            else:
                txt_path = pdf_path[:-4] + ".txt"
                with open(txt_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                it["local_txt"] = txt_path
                it["chars"] = len(text)
                if len(text) < 200:
                    it["text_error"] = "文本极少，可能是扫描件/图片型 PDF，需 OCR"
                    warnings.append(f"{it['id']} 文本仅 {len(text)} 字符，建议改用视觉方式读原文")

    weekend_items = [i for i in items if i["is_weekend"]]
    if weekend_items:
        dates = sorted({f"{i['date']}（{i['weekday']}）" for i in weekend_items})
        warnings.append(
            "存在非交易日披露：" + "、".join(dates)
            + "。市场无法当日反应，必须用 quote_snapshot.py --align 定位其后的首个交易日表现。"
        )

    hk_items = [i for i in items if i["hk_disclosure"]]
    a_items = [i for i in items if not i["hk_disclosure"]]
    if hk_items:
        if not a_items:
            warnings.append(
                f"本批次 {len(hk_items)} 条全部为 H 股/港股例行披露，A 股无实质公告；评级通常为「中性」。"
            )
        else:
            warnings.append(
                f"其中 {len(hk_items)} 条为 H 股/港股例行披露（翌日披露报表等），"
                "多为与 A 股同步的重复信息，不要当作新增事件。"
            )

    substantive = [i for i in a_items if not i["procedural"]]
    if items and not substantive and len(hk_items) < len(items):
        warnings.append("本批次无实质性公告（均为程序性/例行文件），信息增量接近零，评级多为「中性」。")

    payload = {
        "stock": {"code": code, "name": name, "org_id": org_id, "column": column},
        "range": {"start": start, "end": end},
        "window": window_meta or {"mode": "explicit", "start": start, "end": end},
        "count": len(items),
        "empty": not items,
        "market_probe": probe,
        "announcements": items,
        "warnings": warnings + empty_notes,
        "generated_at": dt.datetime.now(CST).isoformat(timespec="seconds"),
    }

    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "announcements.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"== {name} ({code})  {start} ~ {end}  共 {len(items)} 条 ==")
    if window_meta is not None:
        tail = "  ⚠ 交易日历获取失败，上一交易日为估算值" if window_meta.get("estimated") else ""
        print(f"   口径：当日 + 上一交易日（上一交易日 {window_meta['previous_trading_day']}）{tail}")
    for it in items:
        if it["hk_disclosure"]:
            flag = "H股披露"
        elif it["procedural"]:
            flag = "程序性"
        else:
            flag = it["kind"]
        chars = f"  {it['chars']}字" if it.get("chars") else ""
        print(f"  {it['date']} {it['weekday']}  [{flag}] {it['title']}{chars}")
        if it.get("local_pdf"):
            print(f"      -> {it['local_pdf']}")
    if empty_notes:
        print("\n-- 无公告 --")
        for w in empty_notes:
            print(f"  * {w}")
        conclusion = "当日无公告" if start == end else f"{start} ~ {end} 内无公告"
        print(f"  结论：直接回复「{conclusion}」并结束，不再做进一步分析。")
    if warnings:
        print("\n-- 提示 --")
        for w in warnings:
            print(f"  * {w}")
    print(f"\nJSON: {json_path}")


if __name__ == "__main__":
    sys.exit(main())

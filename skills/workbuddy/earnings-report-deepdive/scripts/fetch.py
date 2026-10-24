#!/usr/bin/env python3
"""巨潮资讯网 (cninfo.com.cn) 公告抓取器。

定位股票 -> 拉公告列表 -> 下载 PDF -> 提取文本 -> 输出结构化 JSON 与人类可读摘要。

用法:
  python3 cninfo_fetch.py --keyword 长鑫 --days 3 --out ./cninfo_out
  python3 cninfo_fetch.py --symbol 688825 --start 2026-09-20 --end 2026-10-21
  python3 cninfo_fetch.py --symbol 688825 --days 2 --no-download

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

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BASES = ("https://www.cninfo.com.cn", "http://www.cninfo.com.cn")
CST = dt.timezone(dt.timedelta(hours=8))
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 标题关键词 -> 分量提示。用于快速识别需要精读的公告，不是评级。
# 第一行与 EARNINGS_CORE 口径保持一致：业绩预告的四种变体
# （预增/预盈/预减/预亏）标题里没有「业绩预告」四个字，漏了会打不上 [财务] 标签。
HEAVY_PATTERNS = [
    (r"业绩预告|业绩快报|业绩预[增盈减亏]|季度报告|年度报告|半年度报告", "财务"),
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


# ---- 业绩披露类文件的识别口径（当日模式与最新模式共用，口径必须一致）----
#
# 踩坑记录（勿简化为一句「业绩预告」）：
#   1. 「业绩预增/预减/预盈/预亏」四种标题**不含**「业绩预告」四个字。
#      例：《2026年前三季度业绩预增公告》。只写「业绩预告」会整类漏掉。
#   2. 标题常以「……的自愿性披露公告」结尾。早期版本用 `公告$` 做噪音过滤，
#      结果把《2026年前三季度业绩预告的自愿性披露公告》本身也筛掉了
#      ——长鑫科技（688825）2026-10-17 就是这种写法，实测因此退化到 56 天前的半年报。
#      噪音过滤必须点名具体衍生件，不能用 `公告$` 这种通配后缀。
EARNINGS_CORE = re.compile(
    r"业绩预告|业绩快报|业绩预[增盈减亏]"  # 预告 + 四种变体（预增/预盈/预减/预亏）
    r"|第?[一三]季度报告|半年度报告|年度报告"
)
# 命中核心词、但属于衍生件或重复件的排除项
EARNINGS_NOISE = re.compile(
    r"英文|（英）|\(英\)"      # 英文版与中文版重复（实测标题写作「……业绩预告（英）」）
    r"|更正|修订"               # 打补丁的旧文件，不是新的业绩披露
    r"|说明会|专项说明"          # 业绩说明会是会议通知；专项说明是会计师出的旁证文件
    r"|会计师事务所"             # 「XX会计师事务所关于……业绩预告的专项说明」不是公司自己的披露
    r"|问询"                    # 监管问询，属风险类
    r"|审计报告|财务报表|附注|摘录"  # 报告的附属件
    r"|已取消"
    r"|法律意见书|核查意见|审核报告|验资报告|会议资料|投资者关系活动记录表|通知$"
)


def is_earnings(title):
    """标题是否属于本 skill 分析的业绩披露四类文件（含业绩预告的各种变体）。"""
    t = (title or "").replace("<em>", "").replace("</em>", "")
    return bool(EARNINGS_CORE.search(t)) and not EARNINGS_NOISE.search(t)


# --latest 的回溯窗口序列：不知道最近一份是哪天发的，就逐级外扩，命中即停
LATEST_SPANS = (10, 30, 60, 120, 240, 400)



def query_window(code, org_id, start, end, max_pages=10):
    """拉取某时间窗口内的公告，自动翻页。

    为什么必须翻页：接口按日期倒序返回，pageSize 被服务端忽略、恒定 30 条。
    只取第 1 页的话，公告密集的公司（A+H 公司尤其多，港股月报表、翌日披露报表
    会把窗口塞满）会把窗口内较早的定期报告挤出前 30 条；此时扩大窗口也无用，
    因为第 1 页永远只回最新的 30 条——扩窗只会被更多新公告填满。

    翻完整个窗口而不是「命中即停」：同日披露常同时有全文与摘要两条，
    恰好在页边界上时「命中即停」会只拿到摘要、丢掉全文。
    """
    column = column_for(code)
    referer = f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}&orgId={org_id}"
    items, total = [], None

    for page in range(1, max_pages + 1):
        data = http_json(
            "/new/hisAnnouncement/query",
            {
                "pageNum": page,
                "pageSize": 30,  # 踩坑记录：服务端忽略此值，实际恒定 30 条，勿改
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
            referer=referer,
        )
        if total is None:
            total = data.get("totalRecordNum") or 0
        batch = data.get("announcements") or []
        if not batch:
            break

        for a in batch:
            d = ts_to_date(a["announcementTime"])
            title = (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
            adjunct = a.get("adjunctUrl") or ""
            items.append(
                {
                    "date": d,
                    "weekday": WEEKDAYS[d.weekday()],
                    "id": a.get("announcementId"),
                    "title": title,
                    "pdf_url": f"https://static.cninfo.com.cn/{adjunct}" if adjunct else None,
                }
            )

        if len(items) >= total or len(batch) < 30:
            break

    items.sort(key=lambda x: (x["date"], x["id"] or ""), reverse=True)
    return items


def run_latest(code, org_id, name, today, out, no_download):
    hits, used_span = [], None
    for span in LATEST_SPANS:
        start = (today - dt.timedelta(days=span)).isoformat()
        items = query_window(code, org_id, start, today.isoformat())
        cands = [i for i in items if is_earnings(i["title"])]
        if cands:
            # 同一天可能同时有全文与摘要：全文优先，摘要排后
            cands.sort(key=lambda x: (x["date"], -x["title"].count("摘要")), reverse=True)
            hits, used_span = cands, span
            break

    print(f"== {name} ({code}) 最近一份业绩披露（向前回溯 {used_span if used_span else '>400'} 天）==")
    if not hits:
        print("  未找到业绩预告（含预增/预减/预盈/预亏）/定期报告/摘要。新股或数据异常，需人工核对。")
        print("\n提示：这不是「当日无披露」，而是回溯 400 天内都没有业绩类文件。")
        return

    # 只保留最近一个披露日的全部相关文件（如年报 + 摘要）
    latest_date = hits[0]["date"]
    batch = [h for h in hits if h["date"] == latest_date]
    days_ago = (today - latest_date).days

    if days_ago > 0:
        print(f"  ⚠ 非当日披露，距今天 {days_ago} 天。分析前必须向用户说明这一点。")

    os.makedirs(out, exist_ok=True)
    for it in batch:
        mark = "摘要" if "摘要" in it["title"] else "全文"
        print(f"  {it['date']} {it['weekday']}  [{mark}] {it['title']}  距今 {days_ago} 天")
        if no_download or not it["pdf_url"]:
            continue
        pdf_path = os.path.join(out, f"{it['id']}.PDF")
        if not os.path.exists(pdf_path):
            open(pdf_path, "wb").write(http_bytes(it["pdf_url"]))
        text, err = extract_text(pdf_path)
        if text is None:
            print(f"      -> {pdf_path}  （{err}）")
        else:
            txt_path = pdf_path[:-4] + ".txt"
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"      -> {txt_path}  ({len(text)} 字)")
            if len(text) < 200:
                print("      * 文本极少，可能是扫描件，需改用视觉方式读原文")

    if len(batch) > 1 and any("摘要" in b["title"] for b in batch):
        print("\n  同批次含摘要与全文。以全文为准，摘要只用于交叉核对。")


def main():
    ap = argparse.ArgumentParser(description="巨潮资讯网公告抓取")
    ap.add_argument("--keyword", help="股票名称或简称关键词")
    ap.add_argument("--symbol", help="股票代码，如 688825")
    ap.add_argument("--days", type=int, default=3, help="从今天往前 N 个自然日（含今天），默认 3")
    ap.add_argument("--start", help="起始日 YYYY-MM-DD，与 --end 配合使用")
    ap.add_argument("--end", help="截止日 YYYY-MM-DD")
    ap.add_argument("--latest", action="store_true",
                    help="查找最近一份业绩预告/定期报告/摘要，自动向前扩展窗口（用于「最新数据」场景）")
    ap.add_argument("--out", default="./cninfo_out", help="输出目录")
    ap.add_argument("--no-download", action="store_true", help="只列公告，不下载 PDF")
    args = ap.parse_args()

    if not args.keyword and not args.symbol:
        ap.error("需要 --keyword 或 --symbol")

    today = dt.datetime.now(CST).date()

    if args.latest:
        stock = resolve_stock(args.keyword, args.symbol)
        run_latest(
            stock["code"], stock["orgId"], stock.get("zwjc") or "",
            today, args.out, args.no_download,
        )
        return

    if args.start and args.end:
        start, end = args.start, args.end
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
    if not items:
        warnings.append(
            f"区间 {start} ~ {end} 内无公告。若用户问的是「今天/昨天」，需自行确认是否确实无披露，"
            f"并把检索窗口扩大到 10 天以上排查周末公告。"
        )

    if not args.no_download and items:
        os.makedirs(args.out, exist_ok=True)
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
        "count": len(items),
        "announcements": items,
        "warnings": warnings,
        "generated_at": dt.datetime.now(CST).isoformat(timespec="seconds"),
    }

    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "announcements.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"== {name} ({code})  {start} ~ {end}  共 {len(items)} 条 ==")
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
    if warnings:
        print("\n-- 提示 --")
        for w in warnings:
            print(f"  * {w}")
    print(f"\nJSON: {json_path}")


if __name__ == "__main__":
    main()

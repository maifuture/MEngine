#!/usr/bin/env python3
"""巨潮资讯网（cninfo）定期报告检索与下载 / 收入构成提取。

用法:
    python3 cninfo_periodic.py list  --code 002270
    python3 cninfo_periodic.py fetch --code 002270 --outdir ./reports
    python3 cninfo_periodic.py fetch --code 华明装备 --outdir ./reports --json

功能:
  1. 用 topSearch 接口把「股票代码 / 股票简称」解析成 code + orgId + 简称（沪深京通用）
  2. 用 hisAnnouncement/query 拉取定期报告（年报 / 半年报 / 一季报 / 三季报）列表
  3. 下载最新一期 PDF 并用 pdfplumber 提取全文，自动切出「营业收入构成」「主要会计数据」章节

依赖:
  pip install pdfplumber   # 仅 fetch 子命令需要；缺失时回退 pypdf
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE = "http://www.cninfo.com.cn"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
}

# 定期报告标题识别。注意 A 股存在两套写法：
#   "2026年第一季度报告" / "2026年一季度报告" / "2026第一季度报告" 都要命中
PERIODIC_PATTERNS = [
    ("年报", re.compile(r"(\d{4})\s*年年度报告")),
    ("半年报", re.compile(r"(\d{4})\s*年?(半年度报告|中期报告)")),
    ("三季报", re.compile(r"(\d{4})\s*年第?三季度报告")),
    ("一季报", re.compile(r"(\d{4})\s*年第?一季度报告")),
]
EXCLUDE = re.compile(r"摘要|英文|English|问询|回复|意见|说明|摘录|公告$|债券|评级|已取消|取消")


def _request(url, data=None, timeout=30):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return raw.decode("utf-8", "ignore")


def resolve(keyword):
    """股票代码或简称 -> (code, name, orgId)。"""
    raw = _request(BASE + "/new/information/topSearch/query",
                   {"keyWord": keyword, "maxNum": 10})
    items = json.loads(raw)
    if not items:
        sys.exit(f"[x] 巨潮未检索到「{keyword}」，请检查代码或简称")
    for it in items:
        if it.get("category") == "A股" and it.get("delisted") != "true":
            return it["code"], it["zwjc"], it["orgId"]
    it = items[0]
    return it["code"], it["zwjc"], it["orgId"]


def _column(code):
    if code.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "sse"
    if code.startswith(("800", "430", "83", "87", "88", "92")):
        return "bj"
    return "szse"


def list_periodic(code, orgid, start, end, column=None, max_pages=20):
    """返回 [(日期, 类型, 期次, 标题, 绝对URL)]，按日期倒序。

    注意：接口的 type=category_* 过滤在实测中会被忽略（返回全量），
    因此只能翻页 + 本地按标题正则筛定期报告。
    """
    import datetime
    column = column or _column(code)
    out = []
    for page in range(1, max_pages + 1):
        payload = {
            "pageNum": str(page), "pageSize": "30", "column": column,
            "tabName": "fulltext", "plate": "", "stock": f"{code},{orgid}",
            "searchkey": "", "secDate": f"{start}~{end}", "type": "",
            "trade": "", "seDate": "",
        }
        data = json.loads(_request(BASE + "/new/hisAnnouncement/query", payload))
        anns = data.get("announcements") or []
        if not anns:
            break
        oldest = None
        for a in anns:
            dt = datetime.datetime.fromtimestamp(a["announcementTime"] / 1000)
            date = dt.strftime("%Y-%m-%d")
            oldest = min(oldest, date) if oldest else date
            title = re.sub(r"<[^>]+>", "", a["announcementTitle"]).strip()
            if EXCLUDE.search(title):
                continue
            for kind, pat in PERIODIC_PATTERNS:
                m = pat.search(title)
                if m:
                    out.append((date, kind, m.group(1), title,
                                "http://static.cninfo.com.cn/" + a["adjunctUrl"]))
                    break
        # 已经翻到区间起点之前，停止
        if oldest and oldest <= start:
            break
        if len(anns) < 30:
            break
    seen, uniq = set(), []
    for r in out:
        key = (r[2], r[1])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    uniq.sort(key=lambda x: x[0], reverse=True)
    return uniq


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())
    return os.path.getsize(path)


def pdf_text(path):
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(path) as pdf:
            for p in pdf.pages:
                pages.append(p.extract_text() or "")
        return "\n".join(pages), len(pages)
    except ImportError:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((pg.extract_text() or "") for pg in reader.pages), len(reader.pages)


def slices(text, keywords, span=2600):
    """按关键词切出上下文片段，用于定位收入构成表。"""
    out = []
    for kw in keywords:
        i = text.find(kw)
        if i >= 0:
            out.append((kw, text[max(0, i - 200): i + span]))
    return out


def main():
    ap = argparse.ArgumentParser(description="巨潮定期报告检索 / 下载 / 收入构成提取")
    ap.add_argument("action", choices=["list", "fetch"])
    ap.add_argument("--code", required=True, help="股票代码或简称，如 002270 / 华明装备")
    ap.add_argument("--since", default=None, help="起始日期 YYYY-MM-DD，默认向前 3 年")
    ap.add_argument("--until", default=None, help="截止日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--page-chars", type=int, default=2600, help="每个章节截取字符数")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON 摘要")
    args = ap.parse_args()

    import datetime
    today = datetime.date.today()
    until = args.until or today.isoformat()
    since = args.since or (today - datetime.timedelta(days=365 * 3)).isoformat()

    code, name, orgid = resolve(args.code)
    print(f"[i] {name}（{code}）orgId={orgid}")

    reports = list_periodic(code, orgid, since, until)
    if not reports:
        sys.exit(f"[x] {since}~{until} 内未检索到定期报告，可用 --since 放宽区间")
    print(f"[i] {since}~{until} 定期报告 {len(reports)} 份：")
    for d, kind, yr, title, url in reports:
        print(f"    {d}  {kind}  {yr}  {title}")
    if args.action == "list":
        if args.json:
            print(json.dumps([{"date": d, "kind": k, "year": y, "title": t, "url": u}
                              for d, k, y, t, u in reports], ensure_ascii=False, indent=2))
        return

    d, kind, yr, title, url = reports[0]
    os.makedirs(args.outdir, exist_ok=True)
    pdf_path = os.path.join(args.outdir, f"{name}{yr}年{kind}.pdf")
    size = download(url, pdf_path)
    print(f"[i] 已下载最新一期《{title}》 -> {pdf_path}（{size/1048576:.2f} MB）")

    text, npages = pdf_text(pdf_path)
    txt_path = os.path.splitext(pdf_path)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[i] 全文 {npages} 页已转文本 -> {txt_path}")

    # 记录外部原始出处，供 build_index.py 生成外链（报告第九章用）
    ann_id = os.path.splitext(os.path.basename(url))[0]
    sources_path = os.path.join(args.outdir, "sources.json")
    sources = {}
    if os.path.exists(sources_path):
        try:
            with open(sources_path, encoding="utf-8") as f:
                sources = json.load(f)
        except (OSError, ValueError):
            sources = {}
    sources[os.path.basename(pdf_path)] = {
        "title": title,
        "date": d,
        "kind": kind,
        "year": yr,
        "code": code,
        "name": name,
        "orgId": orgid,
        "announcementId": ann_id,
        "pdfUrl": url,
        "detailUrl": ("https://www.cninfo.com.cn/new/disclosure/detail"
                      f"?stockCode={code}&announcementId={ann_id}"
                      f"&orgId={orgid}&announcementTime={d}"),
        "pages": npages,
        "bytes": size,
    }
    with open(sources_path, "w", encoding="utf-8") as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)
    print(f"[i] 原文出处已记录 -> {sources_path}")

    for kw in ["营业收入构成", "主要会计数据和财务指标"]:
        for k, chunk in slices(text, [kw], args.page_chars):
            print(f"\n===== {k} =====\n{chunk}")

    if args.json:
        print(json.dumps({"code": code, "name": name, "latest": {
            "date": d, "kind": kind, "year": yr, "title": title, "url": url,
            "pdf": pdf_path, "txt": txt_path, "pages": npages}},
            ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

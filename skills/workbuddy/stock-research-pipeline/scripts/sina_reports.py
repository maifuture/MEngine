#!/usr/bin/env python3
"""新浪财经研究报告频道检索与正文抓取（个股研报 / 行业研报）。

用法:
    python3 sina_reports.py company  --code 002270 --months 3 --outdir ./reports
    python3 sina_reports.py industry --keywords 电网,变压器,特高压 --months 3 --outdir ./reports
    python3 sina_reports.py industry --keywords 电网 --months 3 --download 8
    python3 sina_reports.py codes                     # 打印申万二级行业代码表

设计要点（都是实测踩过的坑，改动前请先读 references/data-sources.md）:
  * 站点是 GB2312/GB18030 编码，中文查询参数必须按 GBK 编码后再 urlencode，
    直接用 UTF-8 编码会返回"没有找到相关内容"。
  * 行业研报列表分页参数是 p（不是 page）；列表最深只到约第 60 页（回溯约 1 个月）。
  * 个股研报列表不分页，一页即可覆盖一年以上。
  * 正文在 <div class="blk_container"> 内，剥标签后即为研报摘要/正文。
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HOST = "https://stock.finance.sina.com.cn"
LIST_COMPANY = HOST + "/stock/go.php/vReport_List/kind/search/index.phtml?symbol={sym}&t1=all"
LIST_SEARCH = HOST + "/stock/go.php/vReport_List/kind/search/index.phtml?{q}"
LIST_INDUSTRY = HOST + "/stock/go.php/vReport_List/kind/industry/index.phtml?p={p}"
SHOW = HOST + "/stock/go.php/vReport_Show/kind/{kind}/rptid/{rid}/index.phtml"
CODES_JS = "http://finance.sina.com.cn/stock/reaserchyjbg/report/report_industry_sw2.js"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                              "Referer": "https://finance.sina.com.cn/"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("gb18030", "ignore")


def gbk(s):
    return urllib.parse.quote(s.encode("gb2312", "ignore"))


def strip(html):
    html = html.replace("&nbsp;", " ")
    html = re.sub(r"<br\s*/?>", "\n", html)
    html = re.sub(r"<[^>]+>", "\n", html)
    lines = (l.strip(" \u3000\t") for l in html.split("\n"))
    return "\n".join(l for l in lines if l)


def parse_rows(html):
    """解析研报列表表格 -> [dict]；只保留带 rptid 的行。"""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        m = re.search(r"rptid/(\d+)/", tr)
        if not m:
            continue
        cells = [re.sub(r"\s+", " ", strip(c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 5:
            continue
        t = re.search(r'title="([^"]*)"', tr)
        rows.append({
            "id": m.group(1),
            "title": (t.group(1) if t else cells[1]).strip(),
            "cat": cells[2],
            "date": cells[3],
            "org": cells[4],
        })
    return rows


def asset_symbol(code):
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + code
    if code.startswith(("8", "4", "92")):
        return "bj" + code
    return "sz" + code


def cutoff(months):
    d = datetime.date.today()
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    day = min(d.day, [31, 29 if y % 4 == 0 else 28, 31, 30, 31, 30, 31, 31,
                      30, 31, 30, 31][m - 1])
    return datetime.date(y, m, day).isoformat()


def list_company(code):
    html = fetch(LIST_COMPANY.format(sym=asset_symbol(code)))
    rows = parse_rows(html)
    for r in rows:
        r["kind"] = "search"
    return rows


def list_industry_by_channel(keywords, max_pages=60):
    """遍历行业研报频道（倒序），按标题关键词过滤。覆盖广但只能回溯约 1 个月。"""
    pat = re.compile("|".join(map(re.escape, keywords)))
    out, oldest = [], None
    for p in range(1, max_pages + 1):
        try:
            html = fetch(LIST_INDUSTRY.format(p=p))
        except Exception:
            break
        rows = parse_rows(html)
        if not rows:
            break
        for r in rows:
            oldest = r["date"] if oldest is None else min(oldest, r["date"])
            if pat.search(r["title"]):
                r["kind"] = "industry"
                out.append(r)
    return out, oldest


def list_industry_by_title(keywords, max_pages=5):
    """标题检索，能挖到更早月份，但结果条数少（站点检索索引有限）。"""
    out = []
    for kw in keywords:
        for p in range(1, max_pages + 1):
            q = f"t1=5&title={gbk(kw)}&p={p}"
            try:
                rows = parse_rows(fetch(LIST_SEARCH.format(q=q)))
            except Exception:
                break
            if not rows:
                break
            for r in rows:
                if r["cat"] == "行业":
                    r["kind"] = "industry"
                    out.append(r)
            if len(rows) < 40:
                break
    return out


def fetch_body(rid, kind):
    url = SHOW.format(kind=kind, rid=rid)
    html = fetch(url)
    m = re.search(r'<div class="blk_container">(.*?)</div>', html, re.S)
    body = strip(m.group(1)) if m else ""
    h1 = re.search(r"<h1>(.*?)</h1>", html, re.S)
    title = strip(h1.group(1)) if h1 else ""
    return url, title, body


def save(rec, outdir, tag=""):
    url, title, body = fetch_body(rec["id"], rec["kind"])
    os.makedirs(outdir, exist_ok=True)
    fn = os.path.join(outdir, f"{tag}{rec['date']}_{rec['org'][:6]}_{rec['id']}.txt")
    with open(fn, "w", encoding="utf-8") as f:
        f.write(f"标题：{rec['title'] or title}\n机构：{rec['org']}\n"
                f"日期：{rec['date']}\n类别：{rec['cat']}\n来源：{url}\n\n{body}\n")
    return fn, len(body)


def main():
    ap = argparse.ArgumentParser(description="新浪财经研报检索 / 正文下载")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("company", help="个股研报")
    c.add_argument("--code", required=True)
    c.add_argument("--months", type=int, default=3)
    c.add_argument("--outdir", default="reports")
    c.add_argument("--limit", type=int, default=40)
    c.add_argument("--list-only", action="store_true")
    c.add_argument("--json", action="store_true")

    i = sub.add_parser("industry", help="行业研报")
    i.add_argument("--keywords", required=True, help="逗号分隔，如 电网,变压器,特高压")
    i.add_argument("--months", type=int, default=3)
    i.add_argument("--max-pages", type=int, default=60)
    i.add_argument("--outdir", default="reports")
    i.add_argument("--download", type=int, default=0, help="下载前 N 篇正文，0 表示不下载")
    i.add_argument("--pick", default="", help="只下载标题含该子串的篇目，逗号分隔")
    i.add_argument("--json", action="store_true")

    sub.add_parser("codes", help="打印申万二级行业代码表")

    a = ap.parse_args()

    if a.cmd == "codes":
        s = fetch(CODES_JS)
        for m in re.finditer(r'\{svalue:"([^"]+)",id:"([^"]+)"\}', s):
            print(f"{m.group(2):16s} {m.group(1)}")
        return

    start = cutoff(a.months)
    print(f"[i] 时间窗口: {start} ~ {datetime.date.today().isoformat()}")

    if a.cmd == "company":
        rows = [r for r in list_company(a.code) if r["date"] >= start]
        rows.sort(key=lambda r: r["date"], reverse=True)
        print(f"[i] 个股研报 {len(rows)} 篇")
        for r in rows:
            print(f"    {r['date']}  {r['cat']}  {r['org'][:12]:12s}  {r['title'][:60]}")
        if not rows:
            print("[!] 无近期研报 —— 按流程要求，直接报告『无近期研报』并结束")
            return
        done = []
        if not a.list_only:
            for r in rows[:a.limit]:
                fn, n = save(r, a.outdir)
                done.append({"file": fn, "chars": n, **r})
                print(f"    -> {fn} ({n} 字)")
        if a.json:
            print(json.dumps({"window_start": start, "count": len(rows),
                              "reports": done or rows}, ensure_ascii=False, indent=2))
        return

    kws = [k.strip() for k in a.keywords.split(",") if k.strip()]
    chan, oldest = list_industry_by_channel(kws, a.max_pages)
    title = list_industry_by_title(kws)
    merged = {}
    for r in chan + title:
        if r["date"] >= start:
            merged.setdefault(r["id"], r)
    rows = sorted(merged.values(), key=lambda r: r["date"], reverse=True)
    print(f"[i] 行业研报命中 {len(rows)} 篇"
          f"（频道遍历最早到 {oldest}，标题检索补更早月份）")
    if oldest and oldest > "2000-01-01" and start < oldest:
        print(f"[!] 注意：频道列表最深仅回溯到 {oldest}，{start} ~ {oldest} 区间依赖标题检索，"
              f"可能存在遗漏，报告中必须标注")
    for r in rows:
        print(f"    {r['date']}  {r['org'][:12]:12s}  {r['title'][:70]}")
    if not rows:
        print("[!] 无近期研报 —— 按流程要求，直接报告『无近期研报』并结束")
        return

    picks = [p.strip() for p in a.pick.split(",") if p.strip()]
    targets = [r for r in rows if any(p in r["title"] for p in picks)] if picks else rows
    done = []
    for r in targets[: a.download]:
        fn, n = save(r, a.outdir, tag="行业_")
        done.append({"file": fn, "chars": n, **r})
        print(f"    -> {fn} ({n} 字)")
    if a.json:
        print(json.dumps({"window_start": start, "count": len(rows),
                          "downloaded": done}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

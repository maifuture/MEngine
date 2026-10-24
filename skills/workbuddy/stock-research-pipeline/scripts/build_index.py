#!/usr/bin/env python3
"""生成报告第九章「原文出处」区块 —— 默认输出**外部原始链接**（非本地文件）。

用法:
    python3 build_index.py --dir ./reports                       # 默认：外部原始链接
    python3 build_index.py --dir ./reports --check               # 顺带逐条实测 HTTP 200
    python3 build_index.py --dir ./reports --link-mode relative  # 离线场景：本地相对路径
    python3 build_index.py --dir ./reports --format md|json

参数:
    --dir        存放财报 PDF 与研报 txt 的目录（默认 ./reports）
    --link-mode  external（默认，原始出处）| relative（本地相对路径）
    --check      生成后逐条请求验证链接可用性（需联网）
    --timeout    单条链接探测超时秒数（默认 15）
    --format     html|md|json

原始链接从哪来:
    *.txt  -> 文件头部第 4 行「来源：https://stock.finance.sina.com.cn/...」，由 sina_reports.py 落盘时写入
    *.pdf  -> 同目录 sources.json（cninfo_periodic.py fetch 时自动写入 pdfUrl / detailUrl / pages）
              没有 sources.json 时，该行降级为纯文本，不生成死链

分类规则:
    *.pdf                    -> 一手财报（定期报告）
    行业_*.txt               -> 行业研报
    其他 *.txt               -> 个股研报

设计要点:
    报告会在三种宿主里被打开：本机双击（file://）、目录根 http 服务、内置预览面板。
    预览面板只提供单个 HTML、不暴露同级目录，任何本地链接都会 404 —— 所以第九章改指
    外部原始出处：链接由上游站点提供，不依赖本地目录是否被暴露。
"""
import argparse
import glob
import html as H
import json
import os
import re
import sys
import urllib.request

HDR = re.compile(r"^(标题|机构|日期|类别|来源)[:：]\s*(.*)$")
ORG_SHORT = {
    "平安证券股份有限公司": "平安证券",
    "长城证券股份有限公司": "长城证券",
    "中国银河证券股份有限公司": "中国银河",
    "中信建投证券股份有限公司": "中信建投",
    "中国国际金融股份有限公司": "中金公司",
    "东方证券股份有限公司": "东方证券",
    "中泰证券股份有限公司": "中泰证券",
    "东吴证券股份有限公司": "东吴证券",
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "Chrome/120 Safari/537.36")

TD_DATE = ('<td style="padding:7px 10px;border-bottom:1px solid #eceff3;'
           'font-size:12.5px;color:#7b8494;white-space:nowrap;">{}</td>')
TD_MAIN = ('<td style="padding:7px 10px;border-bottom:1px solid #eceff3;font-size:13px;'
           'line-height:1.6;">{}</td>')
TD_SITE = ('<td style="padding:7px 10px;border-bottom:1px solid #eceff3;font-size:11.5px;'
           'color:#95a0b3;text-align:right;white-space:nowrap;">{}</td>')
A = ('<a href="{url}" target="_blank" rel="noopener noreferrer" title="{title}" '
     'style="color:#1f4e79;text-decoration:none;border-bottom:1px solid #c9d6e4;">{text}</a>')
ORG = '<span style="color:#7b8494;">{}</span>'


def short_org(name):
    return ORG_SHORT.get(name, name)


def read_meta(path):
    meta = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for _ in range(8):
                line = f.readline()
                if not line:
                    break
                m = HDR.match(line.strip())
                if m:
                    meta[m.group(1)] = m.group(2).strip()
    except OSError:
        pass
    return meta


def human(n):
    return f"{n/1048576:.2f} MB" if n >= 1048576 else f"{max(1, n//1024)} KB"


def collect(root):
    """返回 {pdf: [...], company: [...], industry: [...]}，每项含外链字段。"""
    sources_path = os.path.join(root, "sources.json")
    sources = {}
    if os.path.exists(sources_path):
        try:
            with open(sources_path, encoding="utf-8") as f:
                sources = json.load(f)
        except (OSError, ValueError):
            sources = {}

    files = {"pdf": [], "company": [], "industry": []}
    for p in sorted(glob.glob(os.path.join(root, "*"))):
        name = os.path.basename(p)
        if not os.path.isfile(p):
            continue
        low = name.lower()
        size = os.path.getsize(p)
        if low.endswith(".pdf"):
            s = sources.get(name, {})
            files["pdf"].append({
                "name": name,
                "title": s.get("title") or os.path.splitext(name)[0],
                "date": s.get("date", ""),
                "pages": s.get("pages"),
                "url": s.get("pdfUrl", ""),
                "detail": s.get("detailUrl", ""),
                "bytes": size,
                "size": human(size),
            })
        elif low.endswith(".txt"):
            meta = read_meta(p)
            m = re.search(r"_(\d{9,})\.txt$", name)
            files["industry" if name.startswith("行业_") else "company"].append({
                "name": name,
                "title": meta.get("标题") or os.path.splitext(name)[0],
                "inst": short_org(meta.get("机构", "")),
                "date": meta.get("日期", ""),
                "url": meta.get("来源", ""),
                "rptid": m.group(1) if m else "",
                "rel": name,
                "bytes": size,
                "size": human(size),
            })
    for k in ("company", "industry"):
        files[k].sort(key=lambda x: x.get("date", ""), reverse=True)
    files["pdf"].sort(key=lambda x: x.get("date", ""), reverse=True)
    return files


def _row(date, main, site):
    return ("      <tr>\n        " + TD_DATE.format(date) + "\n        "
            + TD_MAIN.format(main) + "\n        " + TD_SITE.format(site) + "\n      </tr>")


def _no_link(title):
    return (H.escape(title)
            + ' <span style="color:#c0392b;font-size:11.5px;">（未取到原始链接）</span>')


def to_html(files, root, link_mode="external"):
    def txt_rows(group):
        out = []
        for it in group:
            if link_mode == "external" and it["url"]:
                main = (A.format(url=it["url"], title=it["title"], text=H.escape(it["title"]))
                        + " " + ORG.format(H.escape(it["inst"])))
                site = f'新浪财经 · {it["rptid"]}' if it["rptid"] else "新浪财经"
            elif link_mode == "relative":
                main = (A.format(url=it["rel"], title=it["name"], text=H.escape(it["title"]))
                        + " " + ORG.format(H.escape(it["inst"])))
                site = "本地文件"
            else:
                main = _no_link(it["title"]) + " " + ORG.format(H.escape(it["inst"]))
                site = "—"
            out.append(_row(it["date"], main, site))
        return "\n".join(out)

    def pdf_rows():
        out = []
        for it in files["pdf"]:
            if link_mode == "external" and it["url"]:
                main = A.format(url=it["url"], title="巨潮资讯网原文 PDF",
                                text=H.escape(it["title"]))
                bits = []
                if it["date"]:
                    bits.append(f"{it['date']} 披露")
                if it["pages"]:
                    bits.append(f"{it['pages']} 页")
                if bits:
                    main += (f' <span style="color:#7b8494;font-size:12.5px;">'
                             f'{" · ".join(bits)}</span>')
                sub = []
                if it["detail"]:
                    sub.append(A.format(url=it["detail"], title="巨潮资讯网公告详情页",
                                        text="公告详情页"))
                sub.append(A.format(url=it["url"], title="PDF 直链", text="PDF 直链")
                           + f'（{it["size"]}）')
                main += ('<br><span style="font-size:11.5px;color:#95a0b3;">'
                         + "　·　".join(sub) + "</span>")
                site = "巨潮资讯"
            elif link_mode == "relative":
                main = A.format(url=it["name"], title=it["name"], text=H.escape(it["title"]))
                site = "本地文件"
            else:
                main = _no_link(it["title"])
                site = "—"
            out.append(_row(it["date"], main, site))
        return "\n".join(out)

    head = ('  <h2 style="font-size:19px;font-weight:700;margin:34px 0 12px;padding-left:11px;'
            'border-left:4px solid #1f3a5f;">九、原文出处（外部原始链接）</h2>\n')

    if link_mode == "external":
        lead = ('  <div style="font-size:12.5px;color:#54607a;line-height:1.9;margin-bottom:12px;">\n'
                '    本章只给<b>外部原始出处</b>，不做本地文件跳转。财报指向<b>巨潮资讯网</b>公告原文，'
                '研报指向<b>新浪财经</b>研究报告频道原文页。链接失效时，可在对应站点用'
                '「股票代码 + 标题」或 rptid 检索。\n  </div>\n')
    else:
        lead = ('  <div style="font-size:12.5px;color:#54607a;line-height:1.9;margin-bottom:12px;">\n'
                f'    本地目录：<code style="background:#f0f2f6;padding:1px 5px;border-radius:2px;'
                f'font-size:11.5px;">{root}</code>　（相对路径，本机双击或以此目录为根起 http 服务可点开）\n  </div>\n')

    body = ['  <div style="background:#fff;border:1px solid #e3e7ee;border-radius:4px;'
            'padding:16px 18px;font-size:13px;line-height:1.9;color:#33405a;">']

    if files["pdf"]:
        body.append('    <div style="font-size:13px;font-weight:700;color:#1f3a5f;">'
                    '一手财报（巨潮资讯网）</div>')
        body.append('    <table style="width:100%;border-collapse:collapse;margin-top:4px;">')
        body.append(pdf_rows())
        body.append('    </table>')
    if files["company"]:
        body.append(f'    <div style="margin-top:16px;font-size:13px;font-weight:700;color:#1f3a5f;">'
                    f'个股研报（{len(files["company"])} 篇 · 新浪财经）</div>')
        body.append('    <table style="width:100%;border-collapse:collapse;margin-top:4px;">')
        body.append(txt_rows(files["company"]))
        body.append('    </table>')
    if files["industry"]:
        body.append(f'    <div style="margin-top:16px;font-size:13px;font-weight:700;color:#1f3a5f;">'
                    f'行业研报（{len(files["industry"])} 篇 · 新浪财经）</div>')
        body.append('    <table style="width:100%;border-collapse:collapse;margin-top:4px;">')
        body.append(txt_rows(files["industry"]))
        body.append('    </table>')
    body.append('  </div>')

    tail = ('\n  <div style="font-size:12px;color:#95a0b3;margin-top:8px;">\n'
            '    财报为一手来源（巨潮资讯网原文 PDF）；研报正文取自新浪财经研究报告频道公开页面，'
            '每篇附 rptid 便于回溯核对。新浪研报页为网页正文形式，无 PDF 直链；'
            '巨潮 PDF 为交易所指定披露文件，可直接下载留存。\n  </div>')
    return head + lead + "\n".join(body) + tail


def to_md(files, link_mode="external"):
    out = []
    for key, label in [("pdf", "一手财报"), ("company", "个股研报"), ("industry", "行业研报")]:
        if not files[key]:
            continue
        out.append(f"**{label}**")
        for it in files[key]:
            url = it.get("url") if link_mode == "external" else it.get("rel")
            if not url:
                out.append(f"- {it.get('date', '')} {it['title']}（未取到原始链接）")
                continue
            head = " ".join(x for x in (it.get("date", ""), it.get("inst", "")) if x)
            out.append(f"- {head} [{it['title']}]({url})")
        out.append("")
    return "\n".join(out)


def check_links(files, timeout=15):
    urls = [(it["title"][:40], it["url"]) for k in files for it in files[k] if it.get("url")]
    if not urls:
        print("[!] 没有可校验的外部链接", file=sys.stderr)
        return 0
    ok = bad = 0
    print(f"[i] 逐条探测 {len(urls)} 条外部链接 …", file=sys.stderr)
    for title, url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                n = len(r.read())
                status = r.status
            if status == 200 and n > 5000:
                ok += 1
            else:
                bad += 1
                print(f"    [!] {status} {n:>8,}  {title}", file=sys.stderr)
        except Exception as e:                                    # noqa: BLE001
            bad += 1
            print(f"    [x] {title} -> {e}", file=sys.stderr)
    print(f"[i] 链接校验：可用 {ok} / 异常 {bad}", file=sys.stderr)
    return bad


def main():
    ap = argparse.ArgumentParser(description="生成第九章原文出处区块")
    ap.add_argument("--dir", default="reports")
    ap.add_argument("--link-mode", choices=["external", "relative"], default="external",
                    help="external（默认，原始出处）/ relative（本地相对路径）")
    ap.add_argument("--check", action="store_true", help="生成后逐条探测链接可用性")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--format", choices=["html", "md", "json"], default="html")
    a = ap.parse_args()

    root = os.path.abspath(a.dir)
    if not os.path.isdir(root):
        raise SystemExit(f"[x] 目录不存在：{root}")
    files = collect(root)
    total = sum(len(v) for v in files.values())
    if total == 0:
        raise SystemExit(f"[x] {root} 下没有可索引的 pdf/txt 文件")

    if a.format == "html":
        print(to_html(files, root, a.link_mode))
    elif a.format == "md":
        print(to_md(files, a.link_mode))
    else:
        print(json.dumps(files, ensure_ascii=False, indent=2))

    no_url = [it["title"] for k in files for it in files[k] if not it.get("url")]
    if a.link_mode == "external" and no_url:
        print(f"[!] {len(no_url)} 个文件缺原始链接：{no_url[:3]}…"
              "（pdf 需 sources.json，txt 需头部「来源：」行）", file=sys.stderr)

    print(f"\n[i] 已索引 {total} 个文件：财报 {len(files['pdf'])} / "
          f"个股研报 {len(files['company'])} / 行业研报 {len(files['industry'])}"
          f"；链接模式={a.link_mode}", file=sys.stderr)

    if a.check and check_links(files, a.timeout):
        sys.exit(1)


if __name__ == "__main__":
    main()

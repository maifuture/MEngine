# 数据源与接口备忘（全部实测）

本文件记录本流程用到的三个数据源的**可用接口、参数、编码陷阱与已知限制**。改脚本前先读这里，不要靠猜参数。

---

## 一、巨潮资讯网 cninfo.com.cn —— 定期报告（一手来源）

### 1.1 代码 / 简称 → orgId

```
POST http://www.cninfo.com.cn/new/information/topSearch/query
Content-Type: application/x-www-form-urlencoded
body: keyWord=002270&maxNum=10
```
返回 JSON 数组，取 `category == "A股"` 且 `delisted != "true"` 的第一条，字段：
`code`（6 位）、`zwjc`（简称）、`orgId`、`type`（shj/szj）。

- **沪深通用**，无需分别下载 `szse_stock.json` / `sse_stock.json`（后者 404）。
- `keyWord` 传简称也能命中，用于「用户只给了股票名字」的场景。

### 1.2 公告列表

```
POST http://www.cninfo.com.cn/new/hisAnnouncement/query
body（表单）:
  pageNum=1  pageSize=30
  column=szse|sse|bj        # 6xx/688 用 sse，其余用 szse
  tabName=fulltext
  plate=
  stock={code},{orgId}      # 逗号分隔，缺 orgId 会返回空
  searchkey=
  secDate=2025-01-01~2026-10-24
  type=
```
返回 `{totalAnnouncement, announcements:[{announcementTitle, announcementTime(ms), adjunctUrl}]}`。

**关键坑（务必记住）**：

1. **`type=category_ndbg_szsh;...` 过滤会被服务端忽略**。实测无论传不传该参数，`totalAnnouncement` 都是全量（如 2135），返回的 30 条永远是「最近 30 条公告」而非定期报告。→ 只能**翻页 + 本地按标题正则筛**。
2. `pageSize` 最大给 30 稳定；翻页至出现空数组或 `oldest <= since` 即停（脚本默认最多 20 页）。
3. 返回时间戳是**毫秒**，需 `/1000` 再 `fromtimestamp`。
4. 需要带 `Referer: http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice`，否则可能 403。
5. PDF 直链前缀：`http://static.cninfo.com.cn/` + `adjunctUrl`。

### 1.3 定期报告标题的两套写法（正则必须同时兼容）

| 类型 | 常见标题形态 |
|---|---|
| 年报 | `2025年年度报告`（必定含「年年度报告」） |
| 半年报 | `2026年半年度报告` / `2026半年度报告` / `中期报告` |
| 一季报 | `2026年一季度报告`、`2026年第一季度报告`、`2026第一季度报告` |
| 三季报 | `2025年三季度报告`、`2025年第三季度报告` |

→ 正则用 `(\d{4})\s*年第?[一三]季度报告`、`(\d{4})\s*年?(半年度报告|中期报告)`。

**排除项**：`摘要`、`英文`、`财务报告`（附件）、`问询/回复/意见/说明`、`债券`、`已取消`。注意「2025年半年度财务报告」是审计附件，不是正文。

### 1.4 PDF → 文本 → 收入构成

- 解析库：`pdfplumber`（首选，表格错行少），回退 `pypdf`。
- 半年报/年报正文一般 150—250 页，全文提取约 3—5 秒。
- 定位锚点（文本检索，无需页码）：
  - `营业收入构成` —— 其后的表格即「分行业 / 分产品 / 分地区」三段，是本流程判定 Top 2 的唯一依据
  - `主要会计数据和财务指标` —— 营收、归母、扣非、现金流、EPS、ROE
  - `主要的业绩驱动因素` / `主营业务分析` —— 往往藏着口径解释（如「海外收入占比已超 40%（直接+间接）」）
- 表格是**竖排折行**的，`pdfplumber` 输出会出现「金额换行、占比单独一行」的情况，需要人工读，不要用正则硬解金额。

---

## 二、新浪财经研报 finance.sina.com.cn —— 研报（非一手，需核实原文）

站点根：`https://stock.finance.sina.com.cn`

### 2.1 编码：**GB2312 / GB18030**，不是 UTF-8

- 请求：直接抓字节，再 `.decode("gb18030", "ignore")`。
- **中文查询参数必须先 `.encode("gb2312")` 再 `urlencode`**。用 UTF-8 编码查 `title=电网` 会返回「没有找到相关内容」，这是最容易误判成「无研报」的坑。ASCII 参数（`sw2_630800`、`sz002270`）不受影响。

### 2.2 个股研报

```
列表: /stock/go.php/vReport_List/kind/search/index.phtml?symbol=sz002270&t1=all
详情: /stock/go.php/vReport_Show/kind/search/rptid/{rptid}/index.phtml
```
- `symbol` 前缀：沪 `sh`、深 `sz`、京 `bj`。
- **列表不分页**，单页即可拿到约 40 条、覆盖一年以上，无需翻页。
- 列表行结构：`序号 | 标题(带 title 属性存全称) | 类别 | 日期 | 机构 | 研究员`；`类别` 取值 `公司 / 创业板 / 行业 / 策略 / 债券`。
- 详情页正文在 `<div class="blk_container">` 内，`<br/>` 与 `&nbsp;` 是排版符，剥标签后即为研报摘要正文（通常 600—2500 字）。
- 详情页**没有 PDF 直链**，新浪托管的是文本版；「下载研报」= 保存这段正文。

### 2.3 行业研报

```
频道列表: /stock/go.php/vReport_List/kind/industry/index.phtml?p={页码}
标题检索: /stock/go.php/vReport_List/kind/search/index.phtml?t1=5&title={GBK编码}&p={页码}
行业筛选: /stock/go.php/vReport_List/kind/search/index.phtml?t1=3&industry=sw2_630800&p={页码}
行业代码表: http://finance.sina.com.cn/stock/reaserchyjbg/report/report_industry_sw2.js
```

三条关键限制，直接影响「近 3 个月研报」能不能拿全：

1. **频道列表分页参数是 `p`，不是 `page`**。`page=` 会被忽略，第 2 页起返回同一批内容，看起来"抓到了"其实全是重复。
2. **频道列表最深只到约第 60 页**（p=61 起返回空），对应约 **1 个月**的行业研报（实测 2026-10-24 时最早到 2026-09-28）。更早月份必须靠标题检索补。
3. **`t1=3&industry=` 筛出来的是「该公司属于该行业的个股研报」，不是行业研报**（`类别` 全是 `公司/创业板`）。想要行业级观点只能用频道列表 + 标题关键词，或 `t1=5` 标题检索（只能精确命中，结果条数少）。

→ 结论：行业研报**永远无法保证穷尽**，交付时必须写明「在库可匹配篇目 + 回溯边界」，不得表述为「全部研报」。

### 2.4 行业代码（申万二级，`codes` 子命令可直接打印）

与电力设备 / 装备制造相关：

| 代码 | 行业 |
|---|---|
| `sw2_630800` | 电网设备 |
| `sw2_630100` | 电机Ⅱ |
| `sw2_630300` | 其他电源设备Ⅱ |
| `sw2_630500` | 光伏设备 |
| `sw2_630600` | 风电设备 |
| `sw2_630700` | 电池 |
| `sw2_640100` | 通用设备 |
| `sw2_640200` | 专用设备 |
| `sw2_640700` | 自动化设备 |

### 2.5 目标价 / 一致预期（可选交叉校验）

行情与机构预测可走 `domain_search` 工具（westock/neodata 源），能一次拿到：收盘价、PE(TTM)、PB、股息率、52 周区间、YTD、MA/BOLL/KDJ、主力资金 5/10/20 日、机构综合目标价、分月评级分布。用于第五章「股价与估值位置」比自建爬虫快得多。

---

## 三、贯穿全流程的校验纪律

| 检查项 | 做法 |
|---|---|
| 最新报告期是否最新 | 先按当前日期推算「理论上应已披露的期次」（4 月底前有年报+一季报，8 月底前有半年报，10 月底前有三季报），再与检索结果比对；不一致就要在报告里说明原因 |
| 研报数字与财报冲突 | 以半年报/年报 PDF 原文为准，研报口径标注「需核实原文」并说明差异 |
| 同一指标多券商不一致 | 检查是否只是笔误（如直接/间接出口增速互换、净利率口径混淆），在报告的「口径校验」章节列明并说明本报告采用哪个 |
| 目标价 | 只作为「机构观点」引用，不作为本报告结论；本报告自己给的是估值区间 + 情景 |

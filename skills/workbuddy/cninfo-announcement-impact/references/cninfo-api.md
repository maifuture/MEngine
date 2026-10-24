# 数据接口速查（cninfo / 行情）

脚本已封装下列接口，正常使用不必读本文件。仅在接口失效、字段异常、需要扩展检索维度时查阅。

## 一、巨潮资讯网

基础域：`https://www.cninfo.com.cn`（http 同样可用，https 证书偶有问题时自动降级）
三个必需请求头：`User-Agent`（浏览器 UA）、`Referer: https://www.cninfo.com.cn/`、`Content-Type: application/x-www-form-urlencoded; charset=UTF-8`

### 1. 标的检索

```
POST /new/information/topSearch/query
keyWord=长鑫&maxNum=10
```
返回数组，关键字段：`code`（股票代码）、`orgId`（**后续查询必填**）、`zwjc`（简称）、`category`（A股）、`type`（shj=沪市科创板，szj=深市，bj=BJ）。

同一个关键词可能命中多个标的（A 股 / 港股 / 新三板），必须用 code 精确匹配，不要取第一条。

### 2. 公告列表

```
POST /new/hisAnnouncement/query
pageNum=1
pageSize=100          # 上限一般 100
column=sse            # sse=沪市  szse=深市  bj=BJ，由代码首位决定
tabName=fulltext
stock=688825,9920000008   # 格式必须为 "代码,orgId"，缺 orgId 返回空
seDate=2026-09-20~2026-10-21   # 含首含尾，格式 YYYY-MM-DD~YYYY-MM-DD
category=             # 可留空；或传类别码做过滤
searchkey=            # 全文关键词检索，可留空
isHLtitle=true
```
返回：`totalRecordNum`、`announcements[]`。
单条关键字段：`announcementId`（下载用）、`announcementTitle`（可能含 `<em>` 高亮标签，需剥离）、`announcementTime`（**毫秒时间戳，为披露日 00:00 北京时间**）、`adjunctUrl`（形如 `finalpage/2026-10-20/1225611371.PDF`）、`adjunctSize`（KB）。

`category` 常用类别码（省流量时可只取感兴趣类别）：`category_ndbg_szsh` 年报、`category_bndbg_szsh` 半年报、`category_yjdbg_szsh` 季报、`category_yjygjxz_szsh` 业绩预告。留空最省事，一次拿全。

### 3. 公告原文下载

```
GET https://static.cninfo.com.cn/finalpage/<YYYY-MM-DD>/<announcementId>.PDF
```
**路径里的日期必须用该公告的披露日**（即 `announcementTime` 换算出的日期），用错日期 404。
PDF 直链无需 cookie，但需带 UA 与 Referer。

### 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `announcements: null` / total=0 | `stock` 缺 orgId，或 `column` 与代码市场不匹配 | 回到第 1 步重取 orgId，核对 column |
| 区间内应为空但确实有公告 | `seDate` 格式错（用了 `~` 之外的分隔符）或起止颠倒 | 严格 `起~止` |
| 日期差一天 | 时间戳按 UTC 解释 | 按 `UTC+8` 换算 |
| PDF 404 | 下载路径日期用了今天而非披露日 | 用 `announcementTime` 的日期 |
| 返回 HTML 而非 JSON | 触发了反爬验证 | 补 Referer / `X-Requested-With`，降频重试 |

## 二、行情：东方财富（主源）

- 快照：`https://push2.eastmoney.com/api/qt/stock/get?secid=1.688825&fields=f43,f60,f116,...`
- 日 K：`https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.688825&klt=101&fqt=1&lmt=250&fields1=...&fields2=f51,f52,...`
- `secid` 规则：`1.` 前缀 = 沪市（6 开头），`0.` 前缀 = 深市/北交所。
- `klt=101` 日线、`102` 周线、`103` 月线；`fqt=1` 前复权、`0` 不复权、`2` 后复权。

快照字段（价格类需 **÷100**，涨跌幅同样 ÷100）：

| 字段 | 含义 | 字段 | 含义 |
|---|---|---|---|
| f43 | 最新价 | f60 | 昨收 |
| f44/f45/f46 | 最高/最低/今开 | f47/f48 | 成交量(手)/成交额(元) |
| f168/f170/f169 | 换手率/涨跌幅/涨跌额 | f116/f117 | 总市值/流通市值(元) |
| f84/f85 | 总股本/流通股 | f162/f167 | PE(动)/PB |

日 K 返回 `klines[]`，逗号分隔顺序：`日期,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率`。`dktotal` 为该股上市以来总根数，可用来判断"是否已覆盖上市首日"。

**注意：未开盘时 f43 可能为 0，但 f60（昨收）有值。不要用 0 当现价算涨跌幅。**

## 三、行情：腾讯（备用源）

免费接口会限流（东财表现为 `Remote end closed connection without response`），脚本自动切换：

- 快照：`https://qt.gtimg.cn/q=sh688825`（**GBK 编码**，`~` 分隔）
- 日 K：`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh688825,day,,,250,qfq`

腾讯快照字段索引：`1` 名称、`2` 代码、`3` 现价、`4` 昨收、`5` 今开、`30` 时间、`31` 涨跌、`32` 涨跌幅%、`33` 最高、`34` 最低、`37` 成交额(万元)、`38` 换手率%、`44` 流通市值(亿)、`45` 总市值(亿)、`46` PB、`52` PE(动)。

腾讯日 K 每项为 `[日期, 开, 收, 高, 低, 成交量]`——**注意是"开、收、高、低"，不是 OHLC**，且**无成交额**。备用源下量比只能用成交量算，报告中需注明口径差异。

## 四、使用注意

- 两个行情源都是非官方接口，字段与服务可能随时变动。取到数据后先用常识校验（昨收是否与上一交易日收盘一致、市值量级是否合理）。
- 单次会话内不要高频轮询同一接口，会被限流。需要多标的时逐个串行、间隔 1 秒以上。
- 交易日历用真实日 K 日期序列反查最准，不要自己用"跳过周末"推算——法定节假日会算错。

# MEngine

A 股研究工作流的一组 [WorkBuddy](https://www.workbuddy.cn) skill，都放在本目录下。

每个 skill 都是自包含的：`SKILL.md` 说明流程与判定口径，`scripts/` 是可独立运行的 Python 脚本，`references/` 是分析手册与接口细节。

## 包含什么

| Skill | 作用 |
|---|---|
| `stock-research-pipeline` | 六步流水线：最新定期报告 → Top 2 收入来源 → 行业与概念板块 → 近 3 个月个股与行业研报 → 短中长期指引，产出单文件 HTML 研报 |
| `cninfo-announcement-impact` | 抓巨潮公告、下载 PDF 原文、判定对短期股价的影响等级（重大利空／利空／中性／利好／重大利好） |
| `earnings-report-deepdive` | 业绩预告／快报／季报／半年报／年报的核查与深研：同比环比拆解、内部矛盾检查、风险预警 |
| `stock-event-calendar` | 读巨潮「交易信息 → 公司日历」，输出已排定事件；纯标准库，无需装任何依赖 |

## 环境要求

- Python **3.9 或更高**
- 除 PDF 文本提取外，所有脚本只用标准库
- PDF 解析需要第三方包：`pypdf`（`stock-research-pipeline` 优先用 `pdfplumber`，缺失时回退 `pypdf`）

## 安装

### 1. 克隆

```bash
git clone https://github.com/maifuture/MEngine.git
```

### 2. 依赖自检

每个需要 PDF 解析的 skill 都带一个自检脚本，**只诊断、不安装**：

```bash
cd MEngine/skills/workbuddy/cninfo-announcement-impact
python3 scripts/check_env.py
```

它会打印解释器路径、版本、是否在虚拟环境、是否受 PEP 668 保护、依赖是否就位，并给出**一条**适配你当前环境的安装命令。退出码 `0` 表示齐全，`1` 表示有缺失。

### 3. 安装依赖（需要你确认）

自检脚本不会自己装东西。请先看清楚它建议的命令，再决定要不要执行——`pip install` 会改动你的 Python 环境。

四种情况对应四条不同路径，自检会替你选好：

| 你的环境 | 建议做法 |
|---|---|
| 已在虚拟环境内 | 直接 `python -m pip install pypdf` |
| 普通环境，非系统托管 | `python3 -m pip install --user pypdf`（装到用户目录，不需要 sudo） |
| 受 PEP 668 保护（Debian/Ubuntu 的 python3、Homebrew Python、macOS CLT python3 常见） | 建专用虚拟环境，pip 会拒绝直接装 |
| 没有 pip | 先 `python -m ensurepip --upgrade` |

装完复验：

```bash
python3 scripts/check_env.py --quiet   # 退出码 0 即就位
```

> **为什么不在脚本里自动装？** 装包会写用户环境，而且装到哪个解释器决定了脚本能不能用。把命令交给你，你随时可以拒绝，也不会在你不知情的情况下改动系统 Python。

### 4. 放进 WorkBuddy 的 skill 目录

把本目录下的四个子目录复制到你的 skill 目录：

| 平台 | 用户级 skill 目录 |
|---|---|
| macOS / Linux | `~/.workbuddy/skills/` |
| Windows | `%USERPROFILE%\.workbuddy\skills\` |

放好后在 WorkBuddy 里可以直接用自然语言触发，例如「帮我看看长鑫科技最近的公告」「纳芯微最新财报怎么样」。

## 跨平台说明

- 命令里的 `python3`：Windows 上改用 `python` 或 `py -3`
- 路径分隔符：Windows 用 `\`
- Windows 终端中文乱码时，先设 `set PYTHONIOENCODING=utf-8`（PowerShell：`$env:PYTHONIOENCODING="utf-8"`）
- 脚本写文件统一用 UTF-8，避免中文系统默认 GBK 导致的编码错误

## 数据来源

巨潮资讯网（cninfo.com.cn）公告与公司日历、新浪财经研报、腾讯／新浪行情接口。全部为公开数据，脚本直接请求，不经过任何中转服务。

## 免责声明

本项目只做数据获取与分析流程的自动化，**不构成投资建议**。评级是基于公告原文与行情反应的方法论输出，不能替代你自己的判断。接口为第三方公开服务，可能变更或限流，脚本异常时以官方披露为准。

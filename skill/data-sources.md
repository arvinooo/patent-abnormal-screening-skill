# 取数与缓存（data-sources.md）

> 只讲怎么拿数据、怎么落缓存。判据和阈值见 SKILL.md / rules.md。

## 0. PDF 提取（免费，第一步）

```bash
python3 scripts/extract_pdf.py <a.pdf> [<b.pdf> ...]        # 逐个输出 JSON 行
python3 scripts/extract_pdf.py --force <a.pdf>             # 改了脚本后强制重解析
```

输出字段：`pub / app_no / app_date / applicants / inventors / agency / agent / ipc / title / abstract / claim1 / claims / tech_field / pages / text_file`

- 全文落在 `cache/pdf/<文件名>.txt`，字段落在 `cache/pdf/<文件名>.json`；第二次跑同一 PDF 直接命中，不重解析。
- 已适配：发明申请（(71)申请人）、实用新型（(73)专利权人、(10)授权公告号）、2021 前老文本（(74) 里叫"代理人"）、多申请人（地址块后的"申请人/专利权人"续行）、首页把 IPC 挤进 (71)/(72)/(74) 的排版。
- `applicants` 为空或 `agency` 为空**可能真的是 PDF 没有该著录项**（例：CN122600366A 无 (74)）——先 grep 全文确认，别当解析失败。
- 公式是图片对象，不作证据；扫描件先 OCR（chi_sim）。
- 用户给的申请人与著录项不一致 → 报告必须标注。

## 1. 工商数据（①社保 / 主营 / 标签）

**一个接口拿齐：参保人数 + 经营范围 + 行业分类 + 企业标签。每主体调 1 次，落 `company` 缓存。**

> 企查查和天眼查**都有参保人数**（企查查「参保人数」＝天眼查 `socialStaffNum`，同一公示口径）。
> **只查一家就行**：默认企查查，它额度宽松；天眼查只当降级备选。两家都查一遍是白花额度。

### 企查查（主源）

额度（2026-11 官方口径）：注册送 500 积分（30 天）；每日送 100（当日有效）；充值 1 元 = 10 积分；同一企业每月最多扣 100 积分、满额当月免费。

```bash
# 取 Key（一次性）：https://agent.qcc.com/guide 注册 → 右上头像 →「获取我的 API KEY」
npm install -g qcc-agent-cli
qcc init --authorization "Bearer <KEY>"    # 落 ~/.qcc/config.json（权限600）
qcc check
qcc company get_company_registration_info "<公司名>" --json
```

MCP 直连（备选）：

```json
{"mcpServers":{"qcc-company":{"url":"https://agent.qcc.com/mcp/company/stream",
  "headers":{"Authorization":"Bearer <KEY>"}}}}
```

返回含「参保人数」「人员规模」「国标行业」「经营范围」「企业类型」，与天眼查 `socialStaffNum` 口径一致。
开放平台 410（无参保，别买错）/ 736（含 InsuredCount，6 元/次）只在批量离线时考虑。

### 天眼查（备选，免费额度小）

```bash
npx -y tyc-cli@latest login --no-block      # OAuth 设备码，打印 URL + 验证码
npx -y tyc-cli@latest login --resume        # 网页授权后换 token（自动刷新）
npx -y tyc-cli@latest company registration-info "<申请人>" --compact
```

字段：`socialStaffNum`（社保，0 与查无主体要区分）、`tags`（高企等）、`businessScope`、`industry`。
可选补充：`company scale`、`intellectual_property ipr-score`、`company profile`（含"专利信息 N 条"）。patents/年报/风险/财务 403 → 用 profile 或 web_search 替代并标"权限受限"。

### 兜底顺序

1. 天眼查 402/额度不足 → **立即切企查查**，不重试不等待
2. 企查查积分不足 → ①社保标「未核」，跳过该维、不作否决
3. 两家都查无 → 标「未核（主体不在库）」；**院校/医院不在工商库属正常**，按 PDF (71) 判类型
4. 参保是公示口径，可能滞后/年报为 0（新设未申报）→ 报告写"公示值 + 查询日期"

### 落缓存

```bash
python3 scripts/cache.py set company "<申请人全名>" --json '{
  "insured": 9, "scope": "细胞技术研发和应用；...", "industry": "科技推广和应用服务业",
  "tags": ["高新技术企业"], "scale": "小型", "source": "企查查", "note": ""}'
```

## 2. 代理师代理量（②，香橼树）

### 2.0 bsk 会话纪律（②/⑤ 所有浏览器取数通用，先看这一节）

`bsk session start` 每调一次就在 Edge 里新开一个窗口，没有「复用」参数。所以：

1. **全批一个会话**：动手前先 `bsk session list --json`。已有会话 → 记下 `session_id` 并全批复用（每条 bsk 命令都带 `--session <id>`，MCP 工具就传 `session`）；一个都没有 → `bsk session start` 只开一次。
2. **不按案、不按维开新会话**：②代理量（香橼树）和发明人件数/跨机构（星河智源）在同一个窗口里换标签页做，用 `bsk tab create`。
3. **子 agent 不碰浏览器**：每个子 agent 自己 start 就各开一个窗口。浏览器步骤只由主 agent 串行跑。
4. **收尾必关**：`bsk session stop <id>` 收**位置参数**；写成 `bsk session stop --session <id>` 返回 `cli_parse_error`，窗口根本关不掉。成功、报错、中断三条路都要关，批末兜底 `bsk session stop --all`。
5. `scripts/mindflow_inventor.py` 默认复用已有会话、只关自己开的那个；不要加 `--new-session`。

需求：某代理师**当年 1月1日至今的累计代理量**（只用当年口径，不取滚动 12 个月、不取历史年份）。

- 入口：`https://main.xysip.com/#/pt/orgData/agentStatistics`，需浏览器已登录；未登录 → `browser_assist request-help` 请人工登录一次（凭据不写文件）
- 页面只渲染框架没有表格 → **刷新一次**
- **不要走「案卷查询」页**——该账号下无代理师筛选项

单人流程：填「代理师姓名」→ 点「搜索」→ 读该行 **当年组三个数中的第一个（总量）**。

行读法：`张亮 21 0 21 405 0 405 705 0 705 163 0 163 96 0 96 1390 0 1390`——列按年份排（2022→2025→2026→合计），每年三个数 = 总量/公海/非公海，**只要当年那组的第一个**（示例 2026 组 = 96）。

> ⚠️ 列是按年份顺排的，**每年都会往后挪一列**。别按"倒数第 4 个"这种位置数去取，先看表头年份，再定位那一年。年份 = 系统当前年，不要写死。

批量脚本（一次 evaluate 循环查多人；9 人可能超时 → `--timeout 180s` 或 3 人一组）：

```js
(async()=>{
  const names=["程凌军","向群","张亮"];   // 按需替换
  const out={}, inp=document.querySelector(".el-form input");
  for (const n of names){
    inp.value=n;
    inp.dispatchEvent(new Event("input",{bubbles:true}));
    inp.dispatchEvent(new Event("change",{bubbles:true}));
    await new Promise(r=>setTimeout(r,200));
    [...document.querySelectorAll("button")].find(b=>b.innerText.trim()==="搜索")?.click();
    await new Promise(r=>setTimeout(r,2600));
    out[n]=[...document.querySelectorAll(".el-table__row")].map(r=>r.innerText.replace(/\s+/g," ").trim()).slice(0,1);
  }
  return JSON.stringify(out);
})()
```

落缓存（命名空间 `agent_month`，有效期 1 天，代理量天天涨）。**存滚动 12 个月口径，不存"当年累计"**：

```bash
python3 scripts/cache.py set agent_month "张亮|2026-11-10" --json '{
  "agent":"张亮","status":"ok","value":35,
  "window":["2025-11-10","2026-11-10"],
  "months":{"2025-11":4,"2025-12":9},
  "ytd_2026":22,
  "components":{"2025-11":4,"2025-12":9,"2026年1-11月合计":22},
  "scope":"本所口径·取总量(含公海)","as_of":"2026-11-10","_source":"香橼树月度统计 + 年度统计"}'
```

- 键必须是 `姓名|窗口锚点`（锚点=本案申请日，无申请日则查询日），换锚点就是另一条缓存，不会串窗口。
- 2025 两个月取月度页逐月值；2026 年可只取年度页的"年至今合计"（判定只需要合计），月拆分没取就在 `note` 写清楚，**不许编造月值**。
- 旧的 `cache/agent.json`（`total_ytd` 当年累计口径）已作废：`_schema=1 / _valid=false`，脚本不再读它。

报告一行：`<代理师> 2026-01-01 至今 代理量 = N 件（取数日 YYYY-MM-DD）`，与 >500 对照。
PDF (74) 可能有**多位代理师**（"专利代理师 孙中勤 杨途军"）→ 逐人取数，任一 >500 即 ②不过。

## 3. 发明人（当年件数 + 申请人一致性·跨机构）

**两条路，浏览器优先（0 积分），MCP 兜底（10 积分/人）。**

### 3.1 最快路径：跑脚本，一条命令

```bash
python3 scripts/mindflow_inventor.py --names "金锐博,王键,张钦崇" --out cache/inventor_batch.json
# 复用已有会话、只关自己开的那个；单人就把 --names 去掉直接传名字
```

脚本做两件事，**能省的动作全省掉**：

| 判据 | 检索式 | 取数方式 | 备注 |
|---|---|---|---|
| 当年件数 | `IN:(姓名) AND APD:[YYYY0101 TO 今天]` | **MCP 结构化记录**，按 `application_number` 去重计件 | 不论发明人位次；`F_IN` 只含第一发明人，不用于本判定 |
| 浏览器校验 | `IN:(姓名) AND APD:[...]` | 读「检索到 N 组」或即时预览条数 | 交叉校验用，最终以去重件数为准 |
| 名下全部专利的申请人 | `IN:(姓名)` | 点检索，读结果表「当前申请(专利权)人」列 | 锁身份仍以 `inventors[0]`＝本人 |

**阈值判断**：件数 ≤5 → 通过；>5 → 不过。截断（total 数千）时去重件数是下限，报告要标。

实测：焦劼 `IN` 当年 5 条记录 → 去重 5 件；27 人一轮 MCP 约 270 积分、6 分钟。

### 3.2 手工路径（脚本跑不通时照这个做，8 个动作）

1. 按 §2.0 拿到会话 `<S>`（复用现有的，没有才开）
   → `bsk tab create --session <S> --url https://www.mindflow.cn/search/fusion`
   （站内常把该 URL 重定向到上次的结果页，**必须再 navigate 一次**才落到融合检索页）
2. `bsk cooperate sync --session <S>` → 拿 `context_id` 和 refs
3. 点「检索式方式」（表格方式下没有表达式输入框）
4. 填「请输入检索式」= `IN:(姓名) AND APD:[YYYY0101 TO 今天]`
5. 读 `当前检索共 N 条专利` → ≤5 就收工
6. 再填 `IN:(姓名)` → 提交
7. 读结果页 `检索到 N 组（共 M 条）`
8. 读表格行的「当前申请(专利权)人」列

**结果表字段**（够跨机构判定用）：
`公开(公告)号｜标题｜申请日｜公开(公告)日｜法律状态/事件｜当前申请(专利权)人｜IPC｜公开类型｜申请号｜专利有效性｜保全次数`

### 3.3 四个必然踩到的坑（照抄绕法）

| 坑 | 现象 | 绕法 |
|---|---|---|
| 合作上下文 | `requires the context returned by the latest cooperate sync` | 每个动作前先 `cooperate sync`，把 `context_id` 传给动作；`navigate` 这类无目标动作还要带 `--observation <id>` |
| refs 每轮重排 | 上一轮的 `@e146` 下一轮变成别的元素 | 动作紧跟 sync，**只用同一轮的 ref**，绝不跨轮复用 |
| 检索按钮被遮 | 右下角「在线咨询」气泡压住按钮，click 报 `Could not clear the extension overlay` | **聚焦按钮再按 Enter**（`press --ref <检索按钮> Enter`），不要试图点它 |
| 表格虚拟滚动 | `bsk extract table` 返回 `rows_returned: 0` | 从 `observe` 的 `row "..."` / `cell "..."` 文本里解析 |

另外：sync 在页面没变化时只返回 `kind=unchanged`、不带 observation 文本，这时补一次 `observe`。

会话怎么开、怎么复用、怎么关（`bsk session stop <id>` 是位置参数），见 §2.0。

### 3.4 口径与交叉校验

- **件数用「组」不用「条」**：组=一个申请；条=一份公开文本（A/B 文、一案双申会让条数虚高）。
- **件数一律用 `IN:(姓名)`**：`IN` 命中发明人一栏的任意位次，按 `application_number` 去重计件；`F_IN` 只含第一发明人，会漏件，不用于本判定（跨机构判定锁身份才用 `inventors[0]`）。
- **必做交叉校验**：浏览器件数 > 5（要用到"不过"结论）时，用 MCP 复核一次。两源不一致 → 件数标「待核查」，别直接判非正常。实测王键 2026 年：浏览器 26 组 vs MCP 8 件，差异来自两库收录范围不同，直接采信任何一方都会错。
- 跨机构判定用浏览器 `IN:(姓名)` 全量清单比对本案申请人；异申请人名单必须写进报告（子母公司分流、同名碰撞都会命中）。

### 3.5 MCP 兜底（浏览器不可用时）

- `https://mcp.mindflow.cn/api/mcp`，`Authorization: Bearer <JWT>`（key 在 `发明人/发明人key.txt`，凭据不进 skill/日志）
- JSON-RPC over HTTP POST，无状态；响应是 SSE 文本，只解析 `data:` 行；`result.structuredContent` 是对象，`result.content[0].text` 是同内容 JSON 字符串（别二次解析）
- 每次 tools/call 收 10 积分；连续快调约 10 次触发限流 → 间隔 ≥2.5s + 指数退避

```python
import json, re, time, urllib.request

def mcp_call(expr, max_results=1000):
    key = open("/mnt/c/Users/Arvin/Desktop/yunjia/非正常/发明人/发明人key.txt",
               encoding="utf-8").read().strip()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "patent_expression_search",
                    "arguments": {"expression": expr, "max_results": max_results}}},
        ensure_ascii=False).encode()
    req = urllib.request.Request("https://mcp.mindflow.cn/api/mcp", data=body,
        method="POST", headers={"Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    for attempt in range(8):
        try:
            raw = urllib.request.urlopen(req, timeout=300).read().decode()
            res = json.loads(re.search(r"data: (\{.*\})", raw, re.S).group(1))["result"]
            if not res.get("isError"):
                return res["structuredContent"]
            txt = res["content"][0]["text"]
            if "mcp_rate_limited" in txt:
                time.sleep(4 + 2 * attempt); continue
            raise RuntimeError(txt[:300])
        except OSError:
            time.sleep(5)
    raise RuntimeError("重试用尽")
```

检索式（MCP 语法）：
```
IN:(姓名)                                   # 全部年限
IN:(姓名) AND APD:[YYYY0101 TO YYYYMMDD]    # 当年（溢出时）
IN:(姓名) NOT ALL_AN:(本案申请人)            # 反查异申请人（溢出时）
```
- `IN:()` 是**子串匹配**；`PA:`/`AP:`/`AS:` 不存在，申请人字段是 `ANS`/`ALL_AN`
- 多人逐名 `IN:(a) OR IN:(b)`；序号写法 `IN:(1) OR IN:(2)` 会静默错检
- 计数三步：剥序号 → 只取 `inventors[0]` 全等 → 按申请号去重（raw total 不能当件数）

### 3.6 落缓存

两套取数结果都写同一个键，存**原始数据**不存派生结论：

```json
{"金锐博": {
  "expr_ytd": "IN:(金锐博) AND APD:[20260101 TO 20261104]",
  "ytd_groups": 1, "ytd_records": 1, "ytd_method": "preview",
  "expr_all": "IN:(金锐博)", "all_groups": 16, "all_records": 19,
  "rows": [{"pub": "CN121632333A", "app_date": "2026-02-05",
            "applicants": ["湖南师范大学"], "app_no": "CN202610162388.8"}],
  "_fetched": "2026-11-04", "_source": "browser"}}
```

## 4. 缓存协议

| 命名空间 | 键 | 有效期 | 内容 |
|---|---|---|---|
| `company` | 申请人全名 | 90 天 | 参保/经营范围/行业/标签/规模 |
| `agent_month` | `姓名\|窗口锚点` | 1 天 | 滚动 12 个月代理量（value + 窗口 + 分月/年至今拆分） |
| `inventor` | 第一发明人姓名 | 7 天 | 检索原样 records + total |
| `pdf` | PDF 文件名 | 永久 | PDF 字段 + 全文 txt（由 `extract_pdf.py` 直接读写 `cache/pdf/`，不走 cache.py） |
| `other` | 自定义 | — | 杂项（查新相似度、通报线索等） |

```bash
python3 scripts/cache.py get <ns> "<键>" [--max-age N]   # exit 0=命中；1=未命中/过期
python3 scripts/cache.py set <ns> "<键>" --json '<...>'
python3 scripts/cache.py list <ns>
python3 scripts/cache.py prune --max-age 365
```

**规则**

1. **先 get 再查**：命中就直接用，报告标「缓存值（原始取数日）」。
   **命中的两个必要条件：口径一致（`_schema` 与当前口径相同）+ 在有效期内**。口径对不上（例如旧 `agent` 存的是"当年累计"而非"滚动 12 个月"）一律当未命中，重查。
   **查到就写回缓存**（`cache.py set`），下次同窗口同口径直接命中；没写回等于下次再花一次成本。
2. **只缓存原始数据，不缓存派生结论**：<3 人、>500 件、>5 件、是否一致，全部每次现算。
3. 过期但没额度时：可以用，但报告要写「缓存值（YYYY-MM-DD，已过期未重查）」并进【复核】。
4. 写入是原子的（临时文件 + rename），并发跑批不会写坏。
5. 缓存目录默认 `./cache`；换目录用 `PATENT_CACHE_DIR=/path`。
6. `cache/` 是工作产物，**不进 skill 包、不提交仓库**。

## 5. 凭据

- 企查查 Key、天眼查 token、mindflow JWT、香橼树账号：**不写入 skill、不提交仓库、不进日志**
- 缺凭据 → 该维标「待核查」，说明缺什么，让用户补

## 6. 排错

| 现象 | 处理 |
|---|---|
| `extract_pdf.py` 报 `No such file` | 确认路径（文件名可能带括号/中文）；先 `ls` 核对 |
| `applicants` 为空 | grep 全文有无 (71)/(73)；实用新型是 (73) 专利权人 |
| 改了脚本但结果没变 | 加了 `--force` 没？缓存按文件大小命中 |
| `cache.py get` 一直 exit 1 | `list` 看键名是否完全一致（公司全名/姓名一字不差） |
| mindflow 401 | key 过期，找服务方换 |
| 香橼树表格空白 | 刷新一次；确认已登录该账号 |
| 天眼查 402 | 切企查查 |

## 7. 出报告（md + pdf）

```bash
python3 scripts/build_report.py <cases目录> -o report
# -> report.md（可 diff）+ report.pdf（A4，逐案表自动横排）
```

- 输入：`<cases目录>/<公开号>.json`（11 个维度键 + conclusion），以及可选的 `_batch.json`。
- 统计（各维不过数、待核查数、命中组合、命中项数分布）由脚本算，**不要手数**。
- PDF 中文字体：依次找 `PATENT_REPORT_FONT` → `C:\Windows\Fonts\simhei.ttf` → `Deng.ttf` → Noto CJK。找不到字体时**只出 md 并明确报错**，不会静默生成乱码 PDF。
- 依赖 `fpdf2`（`pip install fpdf2`）。
- 只产出 md 和 pdf 两个文件，不生成 xlsx/csv。
- 报告格式固定为「总览 + 逐件结论」：总览一行件数 + 一行不过项分布；逐件一行标题（序号 · 案件名 · 申请人 · 不过/过）+ 不过原因整句。
  **不写**附注、数据来源、复核项、取数成本，**不出现**「待核查」与 ①②⑥ / 型A型B / 阈值编号等内部代号（标准见 `SKILL.md` 第五节）。

---

# 星河智源取数（字段码 / 检索式 / 剔重名）

## 8. 星河智源字段码（已对官方文档逐个核对）

出处：<https://help.mindflow.cn/68be/71c0/f40e>（检索表达式字段和语法介绍）

| 用途 | 字段码 | 示例 |
|---|---|---|
| 全部申请人 | `ALL_AN` | `ALL_AN:(天津德吾安环境修复有限公司)` |
| 精确申请人 | `ANC_EXACT` | `ANC_EXACT:(…)` |
| 标准化申请人 | `ANCS` | `ANCS:(…)` |
| 授权时专利权人 | `GRAS` | `GRAS:(…)`（授权公告版本上的权利人） |
| 发明人（任意位次） | `IN` | `IN:(谢建政)` |
| 第一发明人 | `F_IN` | `F_IN:(谢建政)` |
| 专利类型 | `PATENT_TYPE` | 值：`发明专利` / `实用新型` / `外观设计` |
| 法律状态 | `SIMPLE_LEGAL_STATUS` | 值含：授权、实审、公开、驳回、撤回、终止、届满、放弃、部分无效、全部无效… |
| 授权日 | `ISD` | `ISD:[* TO 20261109]`（有值=曾获授权，最稳） |
| 申请日 | `APD` | `APD:[20251110 TO 20261109]` |
| 标题 | `TTL` | `TTL:(防尘开槽器)` |
| 摘要 | `ABST` | `ABST:(喷淋防尘机构)` |
| 技术领域 | `DESC_F` | `DESC_F:(…)` |
| 申请(专利权)人数量 | `ANC_COUNT` | `ANC_COUNT:[3 TO 9]` |

**已验证可用的组合检索式**（2026-11-09 实测）：

```text
# 1.1 授权发明专利：申请人到基准日有无授权发明（0 积分，看"当前检索共 N 条"）
ALL_AN:(申请人) AND PATENT_TYPE:(发明专利) AND ISD:[* TO 基准日]

# 只算"授权那一刻就是它"（排掉受让取得）
GRAS:(申请人) AND PATENT_TYPE:(发明专利) AND ISD:[* TO 基准日]

# 当前仍有效的授权发明（注意：专利终止后状态会变，会漏算，仅作交叉校验）
ALL_AN:(申请人) AND PATENT_TYPE:(发明专利) AND SIMPLE_LEGAL_STATUS:(授权)

# 授权实用新型（仅作参考，不替代 1.1）
ALL_AN:(申请人) AND PATENT_TYPE:(实用新型) AND ISD:[* TO 基准日]
```

实测样例（2026-11-09）：

| 申请人 | 授权发明 | 授权实用新型 |
|---|---:|---:|
| 天津德吾安环境修复 | 0 | 20 |
| 天津市嵘通视觉文化 | 0 | 0（名下专利 0） |
| 中科东南（陕西）环保科技 | 0 | 9 |
| 埃里克阀业（江苏） | 0 | 22 |

## 9. 剔重名取数（1.2 核心）：两个源按申请号 join

**问题**：MCP 的记录**没有 IPC**，浏览器的结果表**没有发明人列表**，两边各缺一半。

| 要的字段 | 从哪取 | 成本 |
|---|---|---|
| `application_number` / `application_date` / `title` / `inventors` / `current_applicants` / `original_applicants` | MCP `patent_expression_search` | 10 积分/次（一次拿一个发明人的全部记录） |
| `ipc`（主分类） | 星河智源浏览器「融合检索」结果表 | 0 积分 |

做法：同一检索式两边各跑一次，按 `application_number` 连接成一张表，再判重名。

MCP 单条记录字段（实测固定，`selected_fields` 参数不被接受）：

```text
publication_number / application_number / application_date / title
current_applicants / original_applicants / inventors
```

**常见名名单**（`common_names`，出现在共同发明人里不作身份锚）：

```text
张伟 王伟 王芳 李娜 刘洋 李伟 王静 张敏 李静 王秀英 张丽 李秀英
刘伟 王强 张杰 李强 王勇 张军 李军 王杰 张勇 李勇 刘杰 王涛
```

名单可扩展；判定逻辑见 `rules/20-inventor.md` §2.2。

## 10. 企业资质（专精特新，豁免依据）

```bash
qcc operation get_honor_info "<申请人全名>" --json
```

返回：`名称 / 荣誉类型 / 级别 / 认证年份 / 发布日期 / 发布单位`。

- 只返回**当前仍有效**的荣誉（查过期用 `get_historical_honor`）→ 不用自己算"认定 3 年"
- 判定：名称含"专精特新"：
  - `专精特新"小巨人"企业` + 级别`国家级` → 豁免生效
  - `专精特新中小企业` + 级别`省级` → 豁免生效
  - `创新型中小企业` / `科技型中小企业` / `高新技术企业` → **不算**
- 实测（2026-11-09）：天津银河阀门 → 小巨人（国家级 2024）；深之蓝 → 专精特新中小企业（省级 2026）；德吾安/嵘通 → 均无专精特新（嵘通有"创新型中小企业"，不算）
- 缓存：`company_honor`，90 天；1 次调用/主体

## 11. 代理师代理量（滚动 12 个月，②）

**页面**：香橼树 → 数据管理 → 「代理师**月度**统计」（`#/pt/orgData/agentYearStatistics`，注意 URL 名是 Year 但内容是月度）

**表结构**：`代理师姓名 | 1月…12月 | 合计`，每月下分 `总量 / 公海 / 非公海`；年份可改（输入框值即年份）。

**取数**：

```text
按「姓名 + 年份」查两次：今年、去年
滚动 12 个月 = 去年 11 月 + 去年 12 月 + 今年 1 月～当月
每月取第一列「总量」，逐月相加
≥300 → 非正常（T05）
```

实测校验（2026-11-09，代理师汝涛）：

| 年 | 月度 | 加总 | 年度页 |
|---|---|---|---|
| 2025 | 17,9,23,37,27,20,19,13,8,6,4,9 | 192 | 192 ✅ |
| 2026 | 3,0,4,2,1,2,5,4,1（1–10月） | 22 | 22 ✅ |

**坑**：`代理师年度统计`页按自然年分列，取不到滚动窗口——不要用它算 12 个月。
**查无此人 vs 0 件**：页面有该行、数值全 0 → 通过；页面搜不到该行 → 待核查。

## 12. 缓存命名空间

| 命名空间 | 键 | 内容 | 有效期 |
|---|---|---|---|
| `company` | 申请人全名 | 参保/经营范围/行业/法人/地址 | 90 天 |
| `company_rd` | 申请人全名 | 授权发明专利件数 + 检索式 + 基准日 | 90 天 |
| `company_honor` | 申请人全名 | 荣誉列表（名称/级别/年份） | 90 天 |
| `inventor` | 第一发明人姓名 | 原始记录（inventors/applicants/ipc）+ 检索式 | 7 天 |
| `inventor_identity` | 发明人 + 本案申请人 | 剔重名结果（保留/剔除 + 理由） | 7 天 |
| `agent_month` | `姓名\|窗口锚点` | 滚动 12 个月：value / window / months / ytd_2026 / _schema=2 | 1 天 |
| `prior_art` | 检索式哈希 | 合并去重后的候选对比文件（含公开日/申请日/申请人） | 7 天 |
| `prior_claims` | 公开号 | 该篇权利要求原文 + 取数日 | 永久 |
| `pdf` / `docx` | 文件名 | 提取字段 + 全文 | 永久 |

## 13. ③ 创新度：对比文件检索与取数

**触发条件（先看 rules/50 §3a/§3b）**：只有本案被归到**机械装置**或**方法步骤图的系统**时才跑。
归到**非四类**直接判过、**不检索**（省额度）；归到生活类/计算机算法直接不过、**也不检索**。

**目的**：判「这篇是不是炒冷饭」——把独权拆成 5–10 个特征，去库里找最接近的现有技术，算特征覆盖度。

### 13.1 两路检索（缺一路 → 该维 pending）

| 路 | 工具 | 成本 | 说明 |
|---|---|---|---|
| 布尔 | `patent_expression_search` | 10 积分/次 | `ABST:(领域词) AND ABST:(功能词)` 或 `TTL:(…)`；至少两组：精确组（专有词）+ 上位组（功能词） |
| 语义 | `semantic_patent_search` | 10 积分/次 | 输入「独权原文 + 创新点清单」，取 top 10 |
| 权项 | `patent_claims` | 10 积分/篇 | 只对最接近的 3 篇取（批量上限 100 篇/次） |

**实测教训（2026-11-09，必须写进报告的理由）**：

| 案 | 语义 top10 命中情况 | 布尔命中情况 |
|---|---|---|
| 空分冷箱扒珠光砂 | **全不相关**（气流粉碎、储煤筒仓惰化） | `ABST:(珠光砂) AND ABST:(空分)` → 61 篇，`TTL:(扒砂装置)` → 5 篇精确命中 |
| BYD 稀 PA 精馏 | 命中 CN122479428A、CN112521252A（够用） | 备用 |

结论：**语义检索在冷门领域会漏，布尔检索在表述差异大时会漏；两路都跑，交叉取并集。**

### 13.2 检索式写法（布尔）

```text
# 精确组：领域专有词
ABST:(珠光砂) AND ABST:(空分)
TTL:(扒砂装置)

# 上位组：功能词
ABST:(粉料) AND ABST:(密闭输送) AND ABST:(负压)
ABST:(惰化) AND ABST:(除尘) AND ABST:(防爆)
```

- 关键词带同义词与上下位词（扒砂/扒除/卸砂；珠光砂/膨胀珍珠岩）
- 时间窗：`AND APD:[* TO 本案申请日]`（无申请日则用查询日），把晚于本案的文件排除
- 领域词写不出来时，用 `ai_patent_search_elements`（按特征表生成关键词/IPC，注意计费）或先跑语义再回捞同义表述

### 13.3 特征覆盖度怎么算（判据见 `rules/50` §3b）

```text
单篇覆盖度 = 该篇公开的特征数 / 本案特征总数
合并覆盖度 = 最接近 1–2 篇合并公开的特征数 / 总数
公知常识、常规选择不计为"公开"；一个特征被该篇公开即计 1（不要求文字完全一致）
```

阈值 T10/T11/T12 见 `00-thresholds.md`。报告必须给逐特征对照表（F1..Fn × 对比文件，✔/✘ + 该文件对应段落/权项）。

### 13.4 缓存

| 命名空间 | 键 | 内容 | 有效期 |
|---|---|---|---|
| `prior_art` | 检索式排序后哈希 | 合并去重候选（公开号/公开日/申请日/申请人/标题）+ 用了哪几路 | 7 天 |
| `prior_claims` | 公开号 | 权利要求原文 | 永久 |

脚本入口：`scripts/v2_fetch.py` 的 `prior_art()` / `prior_claims()`，判定入口：`scripts/v2_rules.py` 的 `novelty()`。

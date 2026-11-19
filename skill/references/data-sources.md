# 取数与缓存（data-sources.md）

> 只讲怎么拿数据、怎么落缓存。判据和阈值见 SKILL.md / rules/。

## 适用范围

本文件只服务于第二层：批次查询、发明人件数、跨机构、代理师代理量、申请人主体五项。

- 第一层的文本判据（手段、效果）**不需要任何外部数据**，只用 `scripts/text_screen.py` 本地解析。
- 第二层每个第一发明人检索一次（剔重名 + 跨机构共用同一份记录），每个法定代表人查一次工商。
- 批次判据一次查询覆盖一个机构的一个月，不逐案查。

不在这两类里的检索式（申请人授权发明量、代理师代理量、专精特新荣誉）按 `rules/60-subject.md` 只作背景记录。

## 0. PDF 提取（免费，第一步）

```bash
python3 scripts/extract_pdf.py <a.pdf> [<b.pdf> ...]        # 逐个输出 JSON 行
python3 scripts/extract_pdf.py --force <a.pdf>             # 改了脚本后强制重解析
```

输出字段：`pub / app_no / app_date / applicants / inventors / agency / agent / ipc / title / abstract / claim1 / claims / tech_field / pages / text_file`

- 全文落在 `cache/pdf/<文件名>.txt`，字段落在 `cache/pdf/<文件名>.json`；第二次跑同一 PDF 直接命中，不重解析。
- 已适配：发明申请（(71)申请人）、实用新型（(73)专利权人、(10)授权公告号）、2021 前老文本（(74) 里叫"代理人"）、多申请人（地址块后的"申请人/专利权人"续行）、首页把 IPC 挤进 (71)/(72)/(74) 的排版、著录项用全角括号（（22）（71）（74））。
- 撰写稿（Word 转 PDF、代理解析导出的纯文本）：无著录项首页、无页脚「权利要求书N页 说明书M页」。文本层按章标题定位：第一处「权利要求书」之后的「1.」为权项起点，「技术领域」为说明书起点；权项页脚写作「权利要求书2」，解析时剥掉。著录项从用户表格取。
- 撰写稿的类型按正文自称判定：出现「本实用新型」且无「本发明」→ 实用新型；公开文本仍以 (12) 行判定。
- `applicants` 为空或 `agency` 为空**可能真的是 PDF 没有该著录项**（例：CN122600366A 无 (74)、撰写稿无 (71)）——先 grep 全文确认，别当解析失败。
- 公式是图片对象，不作证据；扫描件先 OCR（chi_sim）。
- 用户给的申请人与著录项不一致 → 报告必须标注。

## 1. 工商数据（①社保 / 主营 / 标签）

**一个接口拿齐：参保人数 + 经营范围 + 行业分类 + 企业标签。每主体调 1 次，落 `company` 缓存。**

> 企查查和天眼查**都有参保人数**（企查查「参保人数」＝天眼查 `socialStaffNum`，同一公示口径）。
> 同一主体只查一家：默认企查查（额度宽松），天眼查作降级备选。

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

1. **全批一个会话**：动手前先 `bsk session list --json`。已有会话 → 记下 `session_id` 并全批复用（每条 bsk 命令都带 `--session <id>`）；一个都没有 → `bsk session start` 只开一次。
2. **不按案、不按维开新会话**：②代理量（香橼树）和发明人件数/跨机构（incoPat）在同一个窗口里换标签页做，用 `bsk tab create`。
3. **子 agent 不碰浏览器**：每个子 agent 自己 start 就各开一个窗口。浏览器步骤只由主 agent 串行跑。
4. **收尾必关**：`bsk session stop <id>` 收**位置参数**；写成 `bsk session stop --session <id>` 返回 `cli_parse_error`，窗口根本关不掉。成功、报错、中断三条路都要关，批末兜底 `bsk session stop --all`。
5. `scripts/inco_inventor.py` 默认复用已有会话、只关自己开的那个；不要加 `--new-session`。

需求：代理师**滚动 12 个月**的代理量（锚本案申请日，见 `rules/65-agency.md`）。

### 2.1 最快路径：跑脚本

```bash
# 锚本案申请日；没给 --anchor 就用今天
python3 scripts/xys_agent.py 张亮 --anchor 2025-01-16
python3 scripts/xys_agent.py 张亮 --years 2024,2025      # 只取分月表，自己算窗口
python3 scripts/xys_agent.py 张亮 --anchor 2025-01-16 --no-cache
```

输出：`value`（窗口求和）、`window`、`months`（逐月总量）、`missing_months`、`years`.
页面查不到该行 → `status=not_found`，按待核查处理。

脚本按页面实际结构取数：年份字段是 `el-date-editor` 的年选择器（不是 `el-select`，
页面上第一个 `.el-select` 是每页条数）；首屏只渲染框架，脚本自动刷新一次再等 `form` 出现。
缓存分两层：`xys_month`（`姓名|年`，30 天，整年分月表）与 `agent_month`（`姓名|锚点`，1 天，窗口求和）。
同一代理师的不同锚点复用同一张分月表。

### 2.2 手工路径

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

落缓存：命名空间 `agent_month`，键 `姓名|窗口锚点`，有效期 1 天。存滚动 12 个月口径：

```bash
python3 scripts/cache.py set agent_month "张亮|2026-11-10" --json '{
  "agent":"张亮","status":"ok","value":35,
  "window":["2025-11-10","2026-11-10"],
  "months":{"2025-11":4,"2025-12":9},
  "ytd_2026":22,
  "components":{"2025-11":4,"2025-12":9,"2026年1-11月合计":22},
  "scope":"本所口径·取总量(含公海)","as_of":"2026-11-10","_source":"香橼树月度统计 + 年度统计"}'
```

- 键：`姓名|窗口锚点`，锚点取本案申请日（无申请日取查询日）；不同锚点各自一条记录。
- `xys_month` 存整年分月表（`{"agent","year","months":[...],"total","month_sum"}`），30 天有效；窗口求和由脚本现算，不缓存派生结论之外的中间值。
- 数据：2025 年两个月取月度页逐月值；2026 年取年度页「年至今合计」。月值未取时在 `note` 注明，按合计计。
- `cache/agent.json` 为当年累计口径，与滚动 12 个月不一致，脚本不读取。

## 3. 发明人（当年件数 + 申请人一致性·跨机构）

走 incoPat 浏览器，0 平台接口成本。

### 3.1 最快路径：跑脚本

```bash
python3 scripts/inco_inventor.py --names "金锐博,王键,张钦崇" --out cache/inventor_batch.json
# 复用已有会话、只关自己开的那个；单人就把 --names 去掉直接传名字
```

脚本对每个发明人：

1. `IN=(姓名) AND AD=[窗口起 TO 窗口止]` 检索；
2. 结果页每页调到 100，翻页收集结果行；
3. 按申请号去重（一案双申只算 1 件）；
4. 对本案其余发明人各跑一次同窗口 `IN=(...)`，按申请号求交集，标 `co_inventors`（剔重名用）。

结果页每页调到 100 并翻页取全（`inco_client.inventor_patents` 的 `all_pages=True`）：窗口内 131 条约 75 秒取完。`records` 须覆盖全部结果行，否则计数与剔重名都不可用。

| 判据 | 检索式 | 取数方式 |
|---|---|---|
| 窗口内件数 | `IN=(姓名) AND AD=[起 TO 止]` | 结果行按申请号去重计数 |
| 共同发明人 | 其余发明人各一次同窗口 `IN=(...)` | 与候选记录按申请号求交集 |
| 申请人（跨机构） | 结果行「申请人」列 | 直接读 |

**阈值**：去重件数 ≥5 → 不过（T03）。

### 3.2 手工路径（脚本跑不通时照这个做）

1. 打开 `https://www.incopat.com/advancedSearch/init`
2. 指令检索框填 `IN=(姓名) AND AD=[起 TO 止]`
3. 点「检索」，读结果页 `共 N 条` 与左侧类型统计
4. 页脚把每页调到 100，读结果行

**结果行字段**：`公开(公告)号｜类型｜法律状态｜标题｜公开(公告)日｜申请号｜申请日｜申请人｜IPC分类号`

### 3.3 常见问题与处理（照抄绕法）

| 坑 | 现象 | 绕法 |
|---|---|---|
| 页面脚本未就绪 | 提交即报 `search4Kin_common_... is not defined` | 等到 `typeof directSearch==='function'` 再提交；用 `inco_client.wait_ready()` |
| 结果异步渲染 | 提交后 `共 N 条` 迟迟不出现 | 轮询读取条数，最多 4 次、每次 3 秒 |
| 零结果没有「共0条」 | 页面显示「暂无匹配结果」 | 按 0 条处理，不能当检索失败 |
| 显示字段勾选被打断 | `User activity revoked this action` | 不依赖显示字段；发明人一律用交集式检索 |
| 日期写法 | `AD>=`、`AD=[* TO 止]` 报「检索式解析错误」 | 统一 `AD=[起 TO 止]`，无起点用 `19850101` |

### 3.4 口径与交叉校验

- **件数按申请号去重**：条数会因一案双申、A/B 文虚高。
- **常见名**：窗口内 >1000 条 → 标「常见名，计数不可采信 → pending」，不逐条拉全量。
- **公开滞后**：窗口内新申请可能未公开，计数是下限，方向偏松，报告标注。
- 跨机构判定用结果行「申请人」列；异申请人名单写进报告。

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

1. **先 get 再查**：命中直接使用，报告标「缓存值（原始取数日）」。
   命中条件：`_schema` 与当前口径一致，且在有效期内；任一不满足按未命中处理，重新取数。
   取数完成后立即 `cache.py set` 写回。
2. **只缓存原始数据，不缓存派生结论**：<3 人、>500 件、>5 件、是否一致，全部每次现算。
3. 过期但没额度时：可以用，但报告要写「缓存值（YYYY-MM-DD，已过期未重查）」并进【复核】。
4. 写入是原子的（临时文件 + rename），并发跑批不会写坏。
5. 缓存目录默认 `./cache`；换目录用 `PATENT_CACHE_DIR=/path`。
6. `cache/` 是工作产物，**不进 skill 包、不提交仓库**。

## 5. 凭据

- 企查查 Key、天眼查 token、香橼树账号、incoPat 账号：**不写入 skill、不提交仓库、不进日志**
- 缺凭据 → 该维标「待核查」，说明缺什么，让用户补

### 5.1 incoPat 自动登录

凭据文件：默认 `~/.incopat/credential`（权限 600），可用 `INCOPAT_CREDENTIAL_FILE` 覆盖。
格式 `{"user": "...", "password": "..."}`，由脚本写入，不进项目目录。

```bash
python3 scripts/inco_login.py --save --user <账号>      # 交互输入密码，不回显
python3 scripts/inco_login.py --status                  # 看文件是否存在、权限、账号（不显示密码）
python3 scripts/inco_login.py --check                   # 检查并完成登录
```

取数脚本在首次检索时调用 `inco_client.ensure_login()`：

1. 已登录 → 直接取数，不动页面；
2. 未登录且凭据文件存在 → 自动走 `/newLogin`（填 `#u` / `#p`，点 `input.try`），登录后继续；
3. 未登录且无凭据 → 报错并提示先跑 `inco_login.py --save`。

登录态存在浏览器 profile 里，同一浏览器下次开窗通常仍在；凭据文件只在退出登录后起作用。

## 6. 排错

| 现象 | 处理 |
|---|---|
| `extract_pdf.py` 报 `No such file` | 确认路径（文件名可能带括号/中文）；先 `ls` 核对 |
| `applicants` 为空 | grep 全文有无 (71)/(73)；实用新型是 (73) 专利权人 |
| 改了脚本但结果没变 | 加了 `--force` 没？缓存按文件大小命中 |
| `cache.py get` 一直 exit 1 | `list` 看键名是否完全一致（公司全名/姓名一字不差） |
| incoPat 未登录 | 有凭据文件时脚本会自动登录；报 `need_credential` 就先跑 `inco_login.py --save` |
| 香橼树表格空白 | 刷新一次；确认已登录该账号 |
| 天眼查 402 | 切企查查 |

## 7. 出报告（md）

```bash
python3 scripts/text_screen.py <本批目录> --json 结果.json
python3 scripts/build_report.py 结果.json -o 报告.md --title 批次名
```

- 输入：`text_screen.py` 输出的结果 JSON（每案一行：means / effect / batch / verdict / hints）。
- 报告固定三块：结论计数、非正常表、正常表、待核表；每案一行，原因一句话。
- 只产出 md。报告不写证据原文、取数日、批次口径与成本；这些留在 `cases/*.json`。
- 格式要求见 `SKILL.md` 第四节。


## 8. incoPat 字段码（指令检索）

入口：<https://www.incopat.com/advancedSearch/init> 的「指令检索」输入框（`#textarea`）。
语法：`字段=值`，值用圆括号包裹；**日期区间用方括号** `AD=[起 TO 止]`。
不支持 `AD>=20261109`、`AD=[* TO 20261109]`（报「检索式解析错误」）；无起点时用足够早的日期，如 `AD=[19850101 TO 基准日]`。

| 用途 | 字段码 | 示例 |
|---|---|---|
| 申请人 | `AP` | `AP=(天津德吾安环境修复有限公司)` |
| 发明（设计）人 | `IN` | `IN=(谢建政)` |
| 代理人 | `AT` | `AT=(童淑霞)` |
| 代理机构 | `AGC` | |
| 标题 | `TI` | `TI=(防尘开槽器)` |
| 摘要 | `AB` | `AB=(喷淋防尘机构)` |
| 标题摘要 | `TIAB` | |
| 权利要求 | `CLAIM` | |
| 说明书 | `DES` | |
| 主要著录信息 | `ALL` | 简单检索框默认落成 `ALL=(关键词)` |
| 公开（公告）号 | `PN` | `PN=(CN213625156U)` |
| 申请号 | `AN` | |
| 国际 IPC 分类 | `IPC` | |
| CPC 分类 | `CPC` | |
| 申请日 | `AD` | `AD=[20251110 TO 20261109]` |
| 公开（公告）日 | `PD` | |
| 法律状态文字 | `LG` | |
| 专利有效性 | `STATUS` | |
| 当前法律状态 | `STATUS-LITE` | |

专利类型不走字段码：检索页左侧「数据范围」勾选发明申请/发明授权/实用新型/外观，结果页左侧再按类型分组统计（「发明授权(N)」即授权发明文本数）。

## 9. 剔重名取数（1.2 核心）：结果行 + 共同发明人反查

incoPat 结果行自带申请号、申请日、公开日、标题、申请人、IPC、类型，单源自足，直接落字段。

| 要的字段 | 来源 |
|---|---|
| 申请号 / 申请日 / 公开日 / 标题 / 申请人 / IPC / 类型 | 结果行（`inco_client.parse_row`） |
| 共同发明人 | **交集式**：`IN=(第一发明人) AND IN=(本案其余发明人) AND AD=[窗口起 TO 窗口止]`，一次检索直接得到两人共同署名的记录；需逐条标共同发明人时再按申请号比对 |

不用「读记录的发明人名单」：结果行默认不显示发明人，勾选「显示字段 → 发明(设计)人」在自动化下会被页面反复打断，不纳入脚本流程。

窗口内同名记录超过 300 条（常见名或跨领域同名）时不要拉全量，直接用交集式计数；交集为 0 即按 0 件记。本案只有一个发明人、无共同发明人可比 → 改用 `AP=(本案申请人) AND IN=(第一发明人)` 锚定，件数与跨机构只在该集合内数。详见 `rules/40-inventor.md` §2。

判定逻辑仍是 `rules/40-inventor.md` §2：有共同发明人重合（非常见名）→ 保留；否则按 IPC 领域兜底。

**常见名名单**（`common_names`，不作身份锚）：

```text
张伟 王伟 王芳 李娜 刘洋 李伟 王静 张敏 李静 王秀英 张丽 李秀英
刘伟 王强 张杰 李强 王勇 张军 李军 王杰 张勇 李勇 刘杰 王涛
```

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

## 11. 代理师代理量（补充）

取数与脚本见 §2。页面与判据：

- 用「代理师统计」（`#/pt/orgData/agentStatistics`）；「代理师年度统计」（`#/pt/orgData/agentYearStatistics`）按自然年分列，取不到滚动窗口，不用它算 12 个月。
- 月度表每月下分 `总量 / 公海 / 非公海`，取第一列「总量」逐月相加；滚动 12 个月 = 去年 11 月 + 去年 12 月 + 今年 1 月～当月。
- ≥300 件 → 命中（T22）；页面有该行、数值全 0 → 通过；页面搜不到该行 → na，本维不可评估。

实测校验（2026-11-09，代理师汝涛）：2025 年逐月加总 192，与年度页一致；2026 年 1–10 月加总 22，与年度页一致。

## 12. 缓存命名空间

| 命名空间 | 键 | 内容 | 有效期 |
|---|---|---|---|
| `company` | 申请人全名 | 参保/经营范围/行业/法人/地址 | 90 天 |
| `company_rd` | 申请人全名 | 授权发明专利件数 + 检索式 + 基准日 | 90 天 |
| `company_honor` | 申请人全名 | 荣誉列表（名称/级别/年份） | 90 天 |
| `inventor` | 第一发明人 + 窗口 + 共同发明人集合 | 原始记录（co_inventors/applicants/ipc）+ 检索式 | 7 天 |
| `inventor_identity` | 发明人 + 本案申请人 | 剔重名结果（保留/剔除 + 理由） | 7 天 |
| `xys_month` | `姓名\|年` | 香橼树整年分月表：months / total / month_sum | 30 天 |
| `agent_month` | `姓名\|窗口锚点` | 滚动 12 个月：value / window / months / ytd_2026 / _schema=2 | 1 天 |
| `pdf` / `docx` | 文件名 | 提取字段 + 全文 | 永久 |

## 第二阶段数据来源速查

- 工商四项（类型/科技属性/社保/主营）：企查查 `get_company_registration_info`，缓存 90 天，键 = 申请人全名
- 1e 授权发明：incoPat 指令检索（浏览器，无接口费用），缓存 90 天，键 = 申请人全名

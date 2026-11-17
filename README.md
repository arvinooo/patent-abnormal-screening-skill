# 专利非正常申请筛查 Skill（版本库）

一句话：这是「判断一件中国专利申请是不是非正常申请（国知局 77 号令）」这套 skill 的版本库，用来做**版本留档和回滚**。

当前口径是**两层流程**：先只读文件按三项判据出「非正常 / 存疑 / 可提交」，只有存疑案才去查发明人、跨机构等主体数据。

## 版本与分支

| 分支 / tag | 内容 | 用途 |
|---|---|---|
| `main` | 当前口径：两层流程（文本层三项判据 + 存疑案主体层） | 在用的版本 |
| `tag v1.0` | 最早基线：11 维全量、社保 <3、代理师 >500 | 回滚点 |

## 目录结构（`main`）

```
skill/                     ← 就是正在用的那个 skill 目录，可以原样拷回去
├── SKILL.md               主文件：两层流程、铁律、三档映射、报告要求
├── rules/
│   ├── 00-thresholds.md       阈值总表（唯一出口，改阈值只改这里）
│   ├── 10-means.md            手段判据（第八条~第十三条）
│   ├── 20-effect.md           效果判据（第十四条~第十七条）+ 机械/实用新型豁免
│   ├── 30-batch.md            批次判据（第十八条~第二十条）与取数口径
│   ├── 40-illegal.md          违法 / 公德 / 公共利益
│   ├── 50-similar.md          多件相似（拆分型 / 复制型）
│   ├── 60-inventor.md         第二层：发明人件数（含剔重名）
│   ├── 70-cross-org.md        第二层：跨机构聚组
│   └── 80-subject.md          背景项（授权发明/主营/社保/代理量）与专精特新豁免
├── data-sources.md        怎么取数：字段码、检索式、缓存
├── cases.md               标定数据与判例
└── scripts/
    ├── text_screen.py     第一层：三项判据打分，输出三档（不取数）
    ├── extract_pdf.py     公开文本 PDF → 著录项/权1/技术领域（带缓存）
    ├── cache.py           外部数据缓存读写（company / inventor / agent_month）
    ├── inco_login.py      incoPat 登录与凭据
    ├── inco_client.py     incoPat 浏览器客户端
    ├── inco_inventor.py   发明人记录检索
    ├── xys_agent.py       香橼树代理师月度代理量
    └── bsk_helper.py      浏览器操作辅助

docs/
└── 非正常判定标准.md      文本层判定标准（八章三十条，面向律师与审核人员）
```

## 怎么用

把 `skill/` 里的内容放进 skill 加载目录即可：

```bash
rsync -a skill/ ~/项目路径/.dsh/skills/patent-abnormal-screening/
```

第一层可以直接跑：

```bash
python3 scripts/text_screen.py <本批 PDF 目录> --json 结果.json
python3 scripts/text_screen.py <目录> --batch-from-input   # 送审文件覆盖机构当月全部时
```

输出每案一行 JSON：`means` / `effect` / `batch` / `verdict` / `hints`，最后一行为汇总。

需要给律师一份可读标准、或人工复核时，用 `docs/非正常判定标准.md`；它与 `skill/rules/` 的口径一致。

依赖：Python 3.10+，`pypdf`（缺失时回退 `pdfplumber`）；外部取数需 `bsk`（浏览器）与 `qcc`（企查查 CLI）。

## 怎么回滚

1. **只回滚某一个文件**

```bash
git checkout <commit> -- skill/rules/10-means.md
```

2. **整目录回滚到某一历史版本**

```bash
git checkout <commit> -- skill/
```

3. **两层流程之前的 11 维版本**：`git log --oneline -- skill/SKILL.md` 找到重写前的提交，按上面两种方式取回。

## 版本

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-11-05 | 基线。11 维全量判定；社保 <3、代理师 >500 |
| v2.1.1 | 2026-11-11 | 11 维版本末版：剔重名、跨机构、专精特新豁免、③ 先归四类再分流 |
| v3.0 | 2026-11-17 | 两层流程：文本层三项判据先行，存疑案才查主体 |

## 两条硬约束

- **阈值只写在 `skill/rules/00-thresholds.md`**：改判据先改那张表，`scripts/text_screen.py` 的常量与之同步。
- **发明人计数必须先剔重名**：按姓名检索会把同名他人算进来（`王猛` 窗口内可命中上万组），计数只认共同发明人锚定的记录；常见名判为存疑。

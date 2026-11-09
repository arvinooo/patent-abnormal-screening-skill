# 专利非正常申请筛查 Skill（版本库）

一句话：这是「判断一件中国专利申请是不是非正常申请（国知局 77 号令）」这套 skill 的版本库，用来做**版本留档和回滚**。

`v1.0` 是**还没按同事那份《AI 非正常检测标准调整》改的基线版本**——也就是说，如果之后改坏了，回到这个 tag 就是现在能跑通的那一版。

## 版本与分支

| 分支 / tag | 内容 | 用途 |
|---|---|---|
| `main` + `tag v1.0` | v1 口径（当年计数、代理 >500、⑤b 硬一致） | **线上在用的稳定版**，回滚点 |
| `v2-draft` | v2 口径（剔重名、跨机构、专精特新豁免、代理 ≥300） | 待评审，未合并、未打 tag |

## 目录结构（v2-draft 分支）

```
skill/                     ← 就是正在用的那个 skill 目录，可以原样拷回去
├── SKILL.md               主文件：铁律、判定流程、维度总表、状态映射、报告要求
├── rules/                 一维一个文件，按需读
│   ├── 00-thresholds.md       阈值总表（唯一出口，改阈值只改这里）
│   ├── 10-applicant.md        ①类型/科技属性/社保/主营 + 1.1 研发能力
│   ├── 20-inventor.md         1.2 剔重名 + 计数 + ⑤b
│   ├── 30-cross-org.md        2.1 跨机构聚组
│   ├── 40-agency.md           ②代理师滚动 12 个月
│   ├── 50-drafting.md         ③技术领域 + ④撰写
│   ├── 60-multi-similar.md    ⑥多件相似
│   ├── 70-illegal.md          违法/公德/公共利益
│   └── 80-exemptions.md       专精特新豁免 + waived 状态
├── data-sources.md        怎么取数：字段码、检索式、缓存、成本
├── cases.md               标定案例 + v2 回归基线
└── scripts/
    ├── extract_pdf.py     公开文本 PDF → 著录项/权1/技术领域（带缓存）
    ├── cache.py           外部数据缓存读写（company/agent/inventor）
    ├── mindflow_inventor.py  发明人取数（星河智源，浏览器，0 积分）
    └── build_report.py    cases/*.json → report.md + report.pdf

reference/
└── 非正常-规则细化.pdf     判据的原始来源文档

tools/
└── extract_docx.py        额外加的 Word 提取器（不是 skill 本体，输入是 .docx 时用）
```

## 怎么用

把 `skill/` 里的内容放进 skill 加载目录即可：

```bash
rsync -a skill/ ~/项目路径/.dsh/skills/patent-abnormal-screening/
```

（这个 skill 是靠 `SKILL.md` + `rules.md` 驱动的，脚本是辅助工具，缺一不可。）

依赖：Python 3.10+，`python-docx`、`pdfplumber`、`fpdf2`、`pypdf`（可选）；外部工具 `bsk`（浏览器取数）、`qcc`（企查查 CLI）。

## 怎么回滚

三种方式，按场景选：

1. **只回滚某一个文件**（最常见，比如 rules.md 改坏了）

```bash
git checkout v1.0 -- skill/rules.md
```

2. **整目录回到 v1.0**

```bash
git checkout v1.0 -- skill/
```

3. **手上没有网络/没有仓库**：用发布里附的 `git bundle`（`patent-abnormal-screening-skill-v1.0.bundle`）恢复

```bash
git clone patent-abnormal-screening-skill-v1.0.bundle repo-restored
```

## 版本

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-11-05 | 基线。11 维全量判定；阈值：社保 <3、代理师 >500、⑤a >5（不论位次）、⑤b 严格一致 |
| v2-draft | 2026-11-09 | 剔重名计数、1.1 研发能力兜底、跨机构、专精特新豁免、代理 ≥300（分支，未合并） |

v2 的口径与理由见 `CHANGELOG.md` 与 `skill/rules/`。

## 两个必须知道的坑

- **改判据必须同时改 `rules.md` 的阈值总表**，那张表是唯一出口，散落在别处的数字都只是引用。
- **⑤b 对高频姓名天然误伤**：`王猛` 名下能在库里检索到上万组、`陈海佳` 上千组，纯按姓名判一定冤。这是 v1.0 已知缺陷，v2 想解决的就是它。

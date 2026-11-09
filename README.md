# 专利非正常申请筛查 Skill（版本库）

一句话：这是「判断一件中国专利申请是不是非正常申请（国知局 77 号令）」这套 skill 的版本库，用来做**版本留档和回滚**。

`v1.0` 是**还没按同事那份《AI 非正常检测标准调整》改的基线版本**——也就是说，如果之后改坏了，回到这个 tag 就是现在能跑通的那一版。

## 目录结构

```
skill/                     ← 就是正在用的那个 skill 目录，可以原样拷回去
├── SKILL.md               主文件：铁律、11 个维度、判定总表、执行顺序
├── rules.md               判据细则：阈值总表、各维边界、判例、77 号令对照
├── data-sources.md        怎么取数：企查查 / 香橼树 / 星河智源 / 缓存协议
├── cases.md               标定案例：正常/非正常各 3 件 + 回归基线
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

v2（同事提的《AI 非正常检测标准调整》）**还没做**，见 `CHANGELOG.md` 里的待议清单。

## 两个必须知道的坑

- **改判据必须同时改 `rules.md` 的阈值总表**，那张表是唯一出口，散落在别处的数字都只是引用。
- **⑤b 对高频姓名天然误伤**：`王猛` 名下能在库里检索到上万组、`陈海佳` 上千组，纯按姓名判一定冤。这是 v1.0 已知缺陷，v2 想解决的就是它。

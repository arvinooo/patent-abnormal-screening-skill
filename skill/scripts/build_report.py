#!/usr/bin/env python3
"""批量筛查报告（简版）：cases/*.json -> report.md + report.pdf

用法:
    python3 scripts/build_report.py <cases_dir> [-o report] [--title "标题"]

输入:
    <cases_dir>/<案件>.json   每案一份（字段见 SKILL.md「输出」节）
    <cases_dir>/_batch.json   可选，批次元信息：title / rules_version / sources / cost / note

输出:
    <out>.md   总览 + 逐件结论（过/不过 + 不过原因）
    <out>.pdf  同内容排版件

字体: 依次尝试 PATENT_REPORT_FONT / Windows simhei.ttf / Deng.ttf / Noto CJK。
      找不到 CJK 字体时只写 md 并明确警告（不静默降级成乱码 PDF）。
依赖: fpdf2
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter

DIMS = [
    "①类型", "①科技属性", "①社保", "①主营匹配",
    "②代理量",
    "③技术领域", "④撰写",
    "⑤a当年件数", "⑤b一致性",
    "⑥多件相似", "违法公德",
]
DIMS_V2 = [
    "①类型", "①科技属性", "①社保", "①主营匹配", "有无授权发明专利",
    "发明人件数", "跨机构", "②代理量",
    "③技术领域", "④撰写", "⑥多件相似", "违法公德",
]
# 旧 case JSON 里叫「研发能力」，读入时统一改名
DIM_ALIAS = {"研发能力": "有无授权发明专利"}

SHORT_LABEL = {
    "①类型": "①类型", "①科技属性": "①科技属性", "①社保": "社保", "①主营匹配": "①主营",
    "有无授权发明专利": "授权发明专利", "发明人件数": "发明人件数", "跨机构": "跨机构",
    "②代理量": "②代理量", "③技术领域": "③技术领域", "④撰写": "④撰写",
    "⑥多件相似": "⑥多件相似", "违法公德": "违法公德",
    "⑤a当年件数": "⑤a", "⑤b一致性": "⑤b",
}
CONCL = ("非正常", "正常", "待核查")

FONT_CANDIDATES = [
    os.environ.get("PATENT_REPORT_FONT", ""),
    r"C:\Windows\Fonts\simhei.ttf",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/mnt/c/Windows/Fonts/Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def label(c):
    return c.get("pub") or c.get("title") or "—"


def load_cases(cases_dir):
    batch = {}
    bpath = os.path.join(cases_dir, "_batch.json")
    if os.path.exists(bpath):
        with open(bpath, encoding="utf-8") as f:
            batch = json.load(f)
    cases = []
    for path in sorted(glob.glob(os.path.join(cases_dir, "*.json"))):
        if os.path.basename(path).startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        rec.setdefault("dims", {})
        rec.setdefault("notes", {})
        rec.setdefault("conclusion", "待核查")
        for old, new in DIM_ALIAS.items():
            if old in rec["dims"] and new not in rec["dims"]:
                rec["dims"][new] = rec["dims"].pop(old)
            if old in rec["notes"] and new not in rec["notes"]:
                rec["notes"][new] = rec["notes"].pop(old)
        cases.append(rec)
    return cases, batch


def pick_dims(cases):
    keys = set()
    for c in cases:
        keys.update((c.get("dims") or {}).keys())
    return DIMS_V2 if ("有无授权发明专利" in keys or "发明人件数" in keys) else DIMS


def compute(cases, dims):
    stat = {d: Counter() for d in dims}
    for c in cases:
        for d in dims:
            stat[d][c["dims"].get(d, "pending")] += 1
    return stat, Counter(c["conclusion"] for c in cases)


def _brief_reason(c, dim):
    """不过原因：优先 fail_reasons，其次 notes 首段，截短。"""
    txt = (c.get("fail_reasons") or {}).get(dim)
    if not txt:
        txt = (c.get("notes") or {}).get(dim) or ""
        txt = re.split(r"；|→", txt)[0]
    return txt.strip()[:60] or (SHORT_LABEL.get(dim, dim) + "不过")


def _case_line(c, dims):
    concl = c["conclusion"]
    if concl == "非正常":
        fails = [d for d in dims if c["dims"].get(d) == "fail"]
        reasons = "；".join("%s %s" % (SHORT_LABEL.get(d, d), _brief_reason(c, d)) for d in fails)
        line = "**不过**：%s" % reasons
    elif concl == "正常":
        line = "**过**"
    else:
        miss = [SHORT_LABEL.get(d, d) for d in dims if c["dims"].get(d) == "pending"]
        line = "**待核查**：缺 %s" % ("、".join(miss) if miss else "数据")
    pend = [SHORT_LABEL.get(d, d) for d in dims
            if c["dims"].get(d) == "pending" and concl != "待核查"]
    if pend:
        line += "（待核查项：%s）" % "、".join(pend)
    return line


def build_md(cases, batch, out_base, dims, stat, concl):
    n = len(cases)
    now = time.strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# %s\n" % (batch.get("title") or "专利非正常申请批量筛查报告"))
    L.append("- 生成时间：%s" % now)
    L.append("- 案件数：%d" % n)
    L.append("- 判定口径：任一维不过 = 非正常；缺数据 = 待核查\n")

    L.append("## 总览\n")
    L.append("- 不过 **%d** 件，过 **%d** 件，待核查 **%d** 件。"
             % (concl["非正常"], concl["正常"], concl["待核查"]))
    fails = [(SHORT_LABEL.get(d, d), stat[d]["fail"]) for d in dims if stat[d]["fail"]]
    if fails:
        L.append("- 不过项分布：%s。" % "；".join("%s %d 件" % (k, v) for k, v in fails))
    pend = [(SHORT_LABEL.get(d, d), stat[d]["pending"]) for d in dims if stat[d]["pending"]]
    if pend:
        L.append("- 待核查项：%s。" % "；".join("%s %d 件" % (k, v) for k, v in pend))
    if batch.get("note"):
        L.append("\n> %s" % batch["note"])
    L.append("")

    L.append("## 逐件结论\n")
    for i, c in enumerate(cases, 1):
        app = (c.get("applicants") or [""])[0]
        L.append("%d. **%s**（%s）—— %s" % (i, label(c), app, _case_line(c, dims)))
    L.append("")

    srcs = batch.get("sources") or {}
    revs = [c for c in cases if c.get("review")]
    if srcs or revs or batch.get("cost"):
        L.append("## 附注\n")
        if srcs:
            L.append("**数据来源与取数日**\n")
            for k, v in srcs.items():
                L.append("- %s %s：%s" % (k, v.get("date", ""), v.get("note", "")))
            L.append("")
        if revs:
            L.append("**复核项**\n")
            group = {}
            for c in revs:
                group.setdefault(c["review"].strip(), []).append(label(c))
            for txt, labels in group.items():
                if len(labels) == 1:
                    L.append("- %s：%s" % (labels[0], txt))
                else:
                    L.append("- %d 件：%s（%s%s）"
                             % (len(labels), txt, "、".join(labels[:3]),
                                " 等" if len(labels) > 3 else ""))
            L.append("")
        if batch.get("cost"):
            L.append("**取数成本**：%s\n" % json.dumps(batch["cost"], ensure_ascii=False))

    with open(out_base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def _pick_font():
    for p in FONT_CANDIDATES:
        if p and os.path.exists(p) and not p.lower().endswith(".ttc"):
            return p
    for p in FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def build_pdf(cases, batch, out_base, dims, stat, concl):
    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
    except ImportError:
        return False, "未安装 fpdf2（pip install fpdf2）"

    font = _pick_font()
    if not font:
        return False, "找不到可嵌入的中文字体；设 PATENT_REPORT_FONT 指向一个 .ttf"

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(12, 14, 12)
    pdf.set_auto_page_break(auto=True, margin=14)
    try:
        pdf.add_font("CJK", "", font)
    except Exception as e:  # noqa: BLE001
        return False, "字体加载失败 %s: %s" % (font, e)

    def h(txt, size=13):
        pdf.ln(3)
        pdf.set_font("CJK", "", size)
        pdf.multi_cell(0, size * 0.7, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def p(txt, size=9.5):
        pdf.set_font("CJK", "", size)
        pdf.multi_cell(0, 5.6, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.add_page()
    pdf.set_font("CJK", "", 17)
    pdf.multi_cell(0, 10, batch.get("title") or "专利非正常申请批量筛查报告",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    p("生成时间 %s｜案件数 %d｜判定口径：任一维不过 = 非正常，缺数据 = 待核查"
      % (time.strftime("%Y-%m-%d %H:%M"), len(cases)))

    h("总览")
    p("不过 %d 件，过 %d 件，待核查 %d 件。"
      % (concl["非正常"], concl["正常"], concl["待核查"]))
    fails = [(SHORT_LABEL.get(d, d), stat[d]["fail"]) for d in dims if stat[d]["fail"]]
    if fails:
        p("不过项分布：" + "；".join("%s %d 件" % (k, v) for k, v in fails) + "。")
    pend = [(SHORT_LABEL.get(d, d), stat[d]["pending"]) for d in dims if stat[d]["pending"]]
    if pend:
        p("待核查项：" + "；".join("%s %d 件" % (k, v) for k, v in pend) + "。")

    h("逐件结论")
    for i, c in enumerate(cases, 1):
        app = (c.get("applicants") or [""])[0]
        p("%d. %s（%s）—— %s" % (i, label(c), app, _case_line(c, dims).replace("**", "")))

    srcs = batch.get("sources") or {}
    revs = [c for c in cases if c.get("review")]
    if srcs or revs:
        h("附注")
        if srcs:
            p("数据来源与取数日：", size=10)
            for k, v in srcs.items():
                p("· %s %s：%s" % (k, v.get("date", ""), v.get("note", "")))
        if revs:
            p("复核项：", size=10)
            group = {}
            for c in revs:
                group.setdefault(c["review"].strip(), []).append(label(c))
            for txt, labels in group.items():
                if len(labels) == 1:
                    p("· %s：%s" % (labels[0], txt))
                else:
                    p("· %d 件：%s（%s%s）" % (len(labels), txt, "、".join(labels[:3]),
                                              " 等" if len(labels) > 3 else ""))

    pdf.output(out_base + ".pdf")
    return True, ""


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cases_dir")
    ap.add_argument("-o", "--out", default="report")
    ap.add_argument("--title", default="")
    a = ap.parse_args(argv[1:])

    cases, batch = load_cases(a.cases_dir)
    if not cases:
        print("没有找到案件 JSON：%s/*.json" % a.cases_dir)
        return 2
    if a.title:
        batch["title"] = a.title

    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    dims = pick_dims(cases)
    stat, concl = compute(cases, dims)
    build_md(cases, batch, out, dims, stat, concl)
    print("md  ->", out + ".md")
    ok, why = build_pdf(cases, batch, out, dims, stat, concl)
    print(("pdf -> " + out + ".pdf") if ok else ("pdf 跳过：" + why))
    print("非正常 %d / 正常 %d / 待核查 %d；不过项合计 %d"
          % (concl["非正常"], concl["正常"], concl["待核查"],
             sum(stat[d]["fail"] for d in dims)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

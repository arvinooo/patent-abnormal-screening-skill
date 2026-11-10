#!/usr/bin/env python3
"""批量筛查报告（简版）：cases/*.json -> report.md + report.pdf

用法:
    python3 scripts/build_report.py <cases_dir> [-o report] [--title "标题"]

输入:
    <cases_dir>/<案件>.json   每案一份（字段见 SKILL.md「输出」节）
    <cases_dir>/_batch.json   可选，批次元信息：title / sources / cost / note

输出:
    <out>.md   总览 + 逐件结论
    <out>.pdf  同内容排版件（A4，页脚带页码）

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
DIM_ALIAS = {"研发能力": "有无授权发明专利"}

SHORT_LABEL = {
    "①类型": "①类型", "①科技属性": "①科技属性", "①社保": "社保", "①主营匹配": "①主营",
    "有无授权发明专利": "有无授权发明专利", "发明人件数": "发明人件数", "跨机构": "跨机构",
    "②代理量": "②代理量", "③技术领域": "③技术领域", "④撰写": "④撰写",
    "⑥多件相似": "⑥多件相似", "违法公德": "违法公德",
    "⑤a当年件数": "⑤a", "⑤b一致性": "⑤b",
}
CONCL = ("非正常", "正常", "待核查")

NAVY = (31, 56, 100)
RED = (170, 45, 40)
GREEN = (39, 110, 60)
GREY = (110, 115, 122)
LIGHT = (236, 238, 242)

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
    txt = (c.get("fail_reasons") or {}).get(dim)
    if not txt:
        txt = (c.get("notes") or {}).get(dim) or ""
        txt = re.split(r"；|→", txt)[0]
    return txt.strip()[:60] or "不过"


def _fails(c, dims):
    return [(SHORT_LABEL.get(d, d), _brief_reason(c, d)) for d in dims if c["dims"].get(d) == "fail"]


def _pends(c, dims):
    return [SHORT_LABEL.get(d, d) for d in dims if c["dims"].get(d) == "pending"]


def _verdict(c):
    return {"非正常": "不过", "正常": "过", "待核查": "待核查"}.get(c["conclusion"], c["conclusion"])


def build_md(cases, batch, out_base, dims, stat, concl):
    n = len(cases)
    now = time.strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# %s\n" % (batch.get("title") or "专利非正常申请批量筛查报告"))
    L.append("<sub>生成 %s · 案件 %d 件 · 判定口径：任一维不过 = 非正常，缺数据 = 待核查</sub>\n"
             % (now, n))

    L.append("## 总览\n")
    L.append("| 结论 | 件数 |")
    L.append("|:---|---:|")
    L.append("| 不过 | %d |" % concl["非正常"])
    L.append("| 过 | %d |" % concl["正常"])
    L.append("| 待核查 | %d |\n" % concl["待核查"])
    fails = ["%s %d 件" % (SHORT_LABEL.get(d, d), stat[d]["fail"]) for d in dims if stat[d]["fail"]]
    pends = ["%s %d 件" % (SHORT_LABEL.get(d, d), stat[d]["pending"]) for d in dims if stat[d]["pending"]]
    if fails:
        L.append("**不过项**　%s\n" % "　·　".join(fails))
    if pends:
        L.append("**待核查项**　%s\n" % "　·　".join(pends))

    L.append("## 逐件结论\n")
    for i, c in enumerate(cases, 1):
        app = (c.get("applicants") or [""])[0]
        L.append("### %d. %s · %s · %s\n" % (i, label(c), app, _verdict(c)))
        fs = _fails(c, dims)
        if fs:
            L.append("- **不过原因**：%s" % "；".join("%s %s" % (k, v) for k, v in fs))
        ps = _pends(c, dims)
        if ps:
            L.append("- **待核查**：%s" % "、".join(ps))
        L.append("")

    srcs = batch.get("sources") or {}
    revs = [c for c in cases if c.get("review")]
    if srcs or revs or batch.get("cost"):
        L.append("## 附注\n")
        if batch.get("note"):
            L.append("**批次说明**\n")
            L.append("> %s\n" % batch["note"])
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
            L.append("**取数成本**　%s\n" % json.dumps(batch["cost"], ensure_ascii=False))

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

    class Report(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("CJK", "", 8)
            self.set_text_color(*GREY)
            self.cell(0, 6, "第 %d / {nb} 页 · %s"
                      % (self.page_no(), time.strftime("%Y-%m-%d")),
                      align="C")

    pdf = Report(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(auto=True, margin=18)
    try:
        pdf.add_font("CJK", "", font)
    except Exception as e:  # noqa: BLE001
        return False, "字体加载失败 %s: %s" % (font, e)

    W = pdf.w - 32

    def text(t, size=9.5, color=(60, 62, 66), lh=None, x=None, w=None):
        pdf.set_font("CJK", "", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(w or 0, lh or size * 0.62, t,
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")

    def h1(t):
        pdf.ln(1)
        pdf.set_font("CJK", "", 17)
        pdf.set_text_color(*NAVY)
        pdf.multi_cell(0, 9.5, t, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.5)
        y = pdf.get_y()
        pdf.line(16, y, 16 + W, y)
        pdf.ln(3)

    def h2(t):
        pdf.ln(2.5)
        pdf.set_font("CJK", "", 11.5)
        pdf.set_text_color(*NAVY)
        pdf.multi_cell(0, 6.5, t, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(0.5)

    def badge(txt, color, size=8):
        pdf.set_font("CJK", "", size)
        w = pdf.get_string_width(txt) + 6
        x, y = pdf.get_x(), pdf.get_y()
        pdf.set_fill_color(*color)
        pdf.rect(x, y, w, 5.4, style="F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(x, y)
        pdf.cell(w, 5.4, txt, align="C")
        pdf.set_x(x + w + 2)

    # 标题区
    pdf.add_page()
    h1(batch.get("title") or "专利非正常申请批量筛查报告")
    text("生成时间 %s　·　案件 %d 件　·　判定口径：任一维不过 = 非正常，缺数据 = 待核查"
         % (time.strftime("%Y-%m-%d %H:%M"), len(cases)), 8.8, GREY)
    pdf.ln(2)

    # 总览：三块数字
    h2("总览")
    boxw = (W - 8) / 3
    y0 = pdf.get_y()
    items = [("不过", concl["非正常"], RED), ("过", concl["正常"], GREEN),
             ("待核查", concl["待核查"], GREY)]
    for i, (k, v, col) in enumerate(items):
        x = 16 + i * (boxw + 4)
        pdf.set_fill_color(*LIGHT)
        pdf.rect(x, y0, boxw, 15, style="F")
        pdf.set_font("CJK", "", 15)
        pdf.set_text_color(*col)
        pdf.set_xy(x, y0 + 1.6)
        pdf.cell(boxw, 7, str(v), align="C")
        pdf.set_font("CJK", "", 8.5)
        pdf.set_text_color(*GREY)
        pdf.set_xy(x, y0 + 9)
        pdf.cell(boxw, 5, k, align="C")
    pdf.set_y(y0 + 17)

    fails = ["%s %d 件" % (SHORT_LABEL.get(d, d), stat[d]["fail"]) for d in dims if stat[d]["fail"]]
    pends = ["%s %d 件" % (SHORT_LABEL.get(d, d), stat[d]["pending"]) for d in dims if stat[d]["pending"]]
    if fails:
        text("不过项　" + "　·　".join(fails), 9.2, RED)
    if pends:
        text("待核查项　" + "　·　".join(pends), 9.2, GREY)

    # 逐件结论
    h2("逐件结论")
    for i, c in enumerate(cases, 1):
        app = (c.get("applicants") or [""])[0]
        v = _verdict(c)
        col = {"不过": RED, "过": GREEN}.get(v, GREY)
        if pdf.get_y() > pdf.h - 42:
            pdf.add_page()
        # 序号 + 名称
        pdf.set_font("CJK", "", 10.2)
        pdf.set_text_color(*NAVY)
        num = "%d. " % i
        pdf.set_x(16)
        pdf.cell(pdf.get_string_width(num), 6.2, num)
        pdf.multi_cell(W - pdf.get_string_width(num) - 16, 6.2, label(c),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        # 申请人 + 结论徽标
        pdf.set_font("CJK", "", 8.4)
        pdf.set_text_color(*GREY)
        pdf.set_x(16)
        aw = pdf.get_string_width(app)
        pdf.cell(aw + 3, 5.2, app)
        badge(v, col)
        pdf.ln(6)
        # 不过原因
        for k, val in _fails(c, dims):
            pdf.set_x(19)
            text("· %s　%s" % (k, val), 9.0, (60, 62, 66), 5.4)
        ps = _pends(c, dims)
        if ps:
            pdf.set_x(19)
            text("· 待核查　" + "、".join(ps), 8.6, GREY, 5.2)
        # 分隔
        pdf.ln(1.5)
        pdf.set_draw_color(*LIGHT)
        pdf.set_line_width(0.3)
        y = pdf.get_y()
        pdf.line(16, y, 16 + W, y)
        pdf.ln(2.5)

    # 附注
    srcs = batch.get("sources") or {}
    revs = [c for c in cases if c.get("review")]
    if srcs or revs:
        if pdf.get_y() > pdf.h - 55:
            pdf.add_page()
        h2("附注")
        if batch.get("note"):
            text("批次说明", 9.4, NAVY)
            pdf.set_x(19)
            text(batch["note"], 8.6, (85, 88, 92), 5.2)
            pdf.ln(1)
        if srcs:
            text("数据来源与取数日", 9.4, NAVY)
            for k, v in srcs.items():
                pdf.set_x(19)
                text("· %s %s　%s" % (k, v.get("date", ""), v.get("note", "")), 8.6, (85, 88, 92), 5.2)
            pdf.ln(1)
        if revs:
            text("复核项", 9.4, NAVY)
            group = {}
            for c in revs:
                group.setdefault(c["review"].strip(), []).append(label(c))
            for txt, labels in group.items():
                pdf.set_x(19)
                head = labels[0] if len(labels) == 1 else "%d 件" % len(labels)
                text("· %s：%s" % (head, txt), 8.6, (85, 88, 92), 5.2)

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

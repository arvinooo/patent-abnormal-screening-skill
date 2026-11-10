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
    "①类型", "①科技属性", "①社保", "①主营匹配", "授权发明专利",
    "发明人件数", "跨机构", "②代理量",
    "③技术领域", "④撰写", "⑥多件相似", "违法公德",
]
DIM_ALIAS = {"研发能力": "授权发明专利", "有无授权发明专利": "授权发明专利"}

SHORT_LABEL = {
    "①类型": "申请人类型", "①科技属性": "科技属性", "①社保": "社保人数",
    "①主营匹配": "主营匹配", "授权发明专利": "授权发明专利",
    "发明人件数": "发明人申请件数", "跨机构": "发明人跨机构申请",
    "②代理量": "代理师代理量", "③技术领域": "技术领域创新度", "④撰写": "撰写布局",
    "⑥多件相似": "多件相似", "违法公德": "违法公德",
    "⑤a当年件数": "当年申请件数", "⑤b一致性": "申请人一致性",
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
    return DIMS_V2 if ("授权发明专利" in keys or "发明人件数" in keys) else DIMS


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
    """不过原因：只给一句人话，前面不加维度代号。"""
    out = [_brief_reason(c, d) for d in dims if c["dims"].get(d) == "fail"]
    if not out and c["conclusion"] != "正常":
        out = ["关键数据缺失，未取得可放行的证据"]
    return out


def _verdict(c):
    """报告只有两态：非正常=不过，其余（含数据缺失）=过不了就写不过，不出现第三态。"""
    return "过" if c["conclusion"] == "正常" else "不过"


def build_md(cases, batch, out_base, dims, stat, concl):
    n = len(cases)
    n_pass = n - concl["非正常"]
    now = time.strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# %s\n" % (batch.get("title") or "专利非正常申请批量筛查报告"))
    L.append("<sub>生成 %s · 案件 %d 件 · 判定口径：任一维不过 = 非正常</sub>\n"
             % (now, n))

    L.append("## 总览\n")
    L.append("| 结论 | 件数 |")
    L.append("|:---|---:|")
    L.append("| 不过 | %d |" % concl["非正常"])
    L.append("| 过 | %d |\n" % n_pass)
    fails = ["%s %d 件" % (SHORT_LABEL.get(d, d), stat[d]["fail"]) for d in dims if stat[d]["fail"]]
    if fails:
        L.append("**不过项**　%s\n" % "　·　".join(fails))

    L.append("## 逐件结论\n")
    for i, c in enumerate(cases, 1):
        app = (c.get("applicants") or [""])[0]
        L.append("### %d. %s · %s · %s\n" % (i, label(c), app, _verdict(c)))
        fs = _fails(c, dims)
        if fs:
            L.append("- **不过原因**：%s" % "；".join(fs))
        L.append("")

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
                      % (self.page_no(), time.strftime("%Y-%m-%d")), align="C")

    n_pass = len(cases) - concl["非正常"]
    pdf = Report(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(auto=False)
    try:
        pdf.add_font("CJK", "", font)
    except Exception as e:  # noqa: BLE001
        return False, "字体加载失败 %s: %s" % (font, e)

    W = pdf.w - pdf.l_margin - pdf.r_margin

    def _wrap(txt, width, size):
        """按实际字宽逐字断行（中文没有空格，不能靠 word wrap）。"""
        pdf.set_font("CJK", "", size)
        out, cur = [], ""
        for ch in txt:
            if pdf.get_string_width(cur + ch) > width and cur:
                out.append(cur)
                cur = ch
            else:
                cur += ch
        out.append(cur)
        return out

    def para(txt, size=9.5, color=(60, 62, 66), lh=None, x=16, width=None):
        lh = lh or size * 0.62
        width = width or (W - (x - pdf.l_margin))
        for ln in _wrap(txt, width, size):
            if pdf.get_y() + lh > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.set_font("CJK", "", size)
            pdf.set_text_color(*color)
            pdf.set_x(x)
            pdf.cell(width, lh, ln, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def h1(t):
        pdf.ln(1)
        para(t, 17, NAVY, 9.5)
        pdf.ln(1)
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.5)
        y = pdf.get_y()
        pdf.line(16, y, 16 + W, y)
        pdf.ln(3)

    def h2(t):
        pdf.ln(2.5)
        para(t, 11.5, NAVY, 6.5)
        pdf.ln(0.5)

    def badge(txt, color, size=8):
        pdf.set_font("CJK", "", size)
        w = pdf.get_string_width(txt) + 6
        x, y = pdf.get_x(), pdf.get_y() + 0.4
        pdf.set_fill_color(*color)
        pdf.rect(x, y, w, 5.2, style="F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(x, y)
        pdf.cell(w, 5.2, txt, align="C")
        pdf.set_x(x + w + 2)

    pdf.add_page()
    h1(batch.get("title") or "专利非正常申请批量筛查报告")
    para("生成时间 %s　·　案件 %d 件　·　判定口径：任一维不过 = 非正常"
         % (time.strftime("%Y-%m-%d %H:%M"), len(cases)), 8.8, GREY)
    pdf.ln(2)

    h2("总览")
    boxw = (W - 6) / 2
    y0 = pdf.get_y()
    items = [("不过", concl["非正常"], RED), ("过", n_pass, GREEN)]
    for i, (k, v, col) in enumerate(items):
        x = pdf.l_margin + i * (boxw + 6)
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
    if fails:
        para("不过项　" + "　·　".join(fails), 9.2, RED)

    h2("逐件结论")
    for i, c in enumerate(cases, 1):
        app = (c.get("applicants") or [""])[0]
        v = _verdict(c)
        col = {"不过": RED, "过": GREEN}.get(v, GREY)
        need = 22 + 4 * (len(_fails(c, dims)) + 1)
        if pdf.get_y() + need > pdf.h - pdf.b_margin:
            pdf.add_page()
        para("%d.　%s" % (i, label(c)), 10.2, NAVY, 6.0)
        pdf.set_font("CJK", "", 8.4)
        pdf.set_text_color(*GREY)
        pdf.set_x(16)
        aw = pdf.get_string_width(app)
        pdf.cell(aw + 3, 5.2, app)
        badge(v, col)
        pdf.ln(6)
        for val in _fails(c, dims):
            para("· " + val, 9.0, (60, 62, 66), 5.4, x=19)

        pdf.ln(1.5)
        pdf.set_draw_color(*LIGHT)
        pdf.set_line_width(0.3)
        y = pdf.get_y()
        pdf.line(16, y, 16 + W, y)
        pdf.ln(2.5)

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
    print("非正常 %d / 正常 %d；不过项合计 %d"
          % (concl["非正常"], concl["正常"],
             sum(stat[d]["fail"] for d in dims)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

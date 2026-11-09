#!/usr/bin/env python3
"""专利公开文本 DOCX -> 结构化字段（带缓存）。对齐 extract_pdf.py 的输出字段。

用法:
    python3 scripts/extract_docx.py <file.docx> [--force] [--app "申请人"] [--title "名称"]

输出: 每个 docx 一个 JSON 对象（stdout 一行一个）。
全文落 cache/docx/<key>.txt，字段落 cache/docx/<key>.json；命中缓存不重解析。

字段: pub/app_no/app_date/applicants/inventors/agency/agent/ipc/title/abstract/
      claim1/claims/tech_field/sections/text_file
注: 这类 docx 一般不含著录项目（(71)/(72)/(74)），申请人等由外部参数补。
"""
import hashlib
import json
import os
import re
import sys

CACHE = os.environ.get("PATENT_CACHE_DIR", "cache")

HEAD_PATTERNS = [
    ("abstract_head", re.compile(r"^说\s*明\s*书\s*摘\s*要$")),
    ("abstract_fig", re.compile(r"^摘\s*要\s*附\s*图$")),
    ("claims_head", re.compile(r"^权\s*利\s*要\s*求\s*书$")),
    ("spec_head", re.compile(r"^说\s*明\s*书$")),
    ("draw_head", re.compile(r"^说\s*明\s*书\s*附\s*图$")),
]
SEC = {
    "技术领域": re.compile(r"^技\s*术\s*领\s*域$"),
    "背景技术": re.compile(r"^背\s*景\s*技\s*术$"),
    "发明内容": re.compile(r"^发\s*明\s*内\s*容$"),
    "附图说明": re.compile(r"^附\s*图\s*说\s*明$"),
    "具体实施方式": re.compile(r"^具\s*体\s*实\s*施\s*方\s*式$"),
}
FIG_LINE = re.compile(r"^图\s*\d+")
CLAIM_NO = re.compile(r"^\s*(\d{1,2})\s*[.、．]\s*")


def _key(path):
    name = os.path.basename(path)
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12] + "_" + re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name)[:80]


def _doc_lines(doc):
    """正文段落 + 表格文字，按文档顺序。"""
    from docx.oxml.ns import qn
    out = []
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            from docx.text.paragraph import Paragraph
            t = Paragraph(child, doc).text.strip()
            if t:
                out.append(t)
        elif child.tag == qn("w:tbl"):
            from docx.table import Table
            tb = Table(child, doc)
            for row in tb.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    out.append(" | ".join(cells))
    return out


def parse_docx(path, force=False, app=None, title_hint=None):
    os.makedirs(os.path.join(CACHE, "docx"), exist_ok=True)
    key = _key(path)
    txt_path = os.path.join(CACHE, "docx", key + ".txt")
    json_path = os.path.join(CACHE, "docx", key + ".json")
    if not force and os.path.exists(json_path) and os.path.exists(txt_path):
        rec = json.load(open(json_path, encoding="utf-8"))
        if app and rec.get("applicants") != [app]:
            rec["applicants"] = [app]
            rec["applicants_source"] = "用户提供/案件表"
            json.dump(rec, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return rec

    import docx
    doc = docx.Document(path)
    lines = _doc_lines(doc)
    full = "\n".join(lines)

    # 定位大区块
    idx = {}
    for i, ln in enumerate(lines):
        for name, pat in HEAD_PATTERNS:
            if pat.match(ln) and name not in idx:
                idx[name] = i
    a0 = idx.get("abstract_head"); c0 = idx.get("claims_head"); s0 = idx.get("spec_head")
    d0 = idx.get("draw_head", idx.get("abstract_fig"))

    # 兜底：有文件缺"说明书摘要/权利要求书/说明书"大标题，靠"1.一种…"与技术领域标题定位
    tf_idx = None
    for i, ln in enumerate(lines):
        for name, pat in SEC.items():
            if pat.match(ln):
                tf_idx = i
                break
        if tf_idx is not None:
            break
    claim_idx = None
    if c0 is None:
        for i, ln in enumerate(lines):
            m = CLAIM_NO.match(ln)
            if m and m.group(1) == "1":
                c0 = i - 1 if i > 0 else i
                claim_idx = i
                break
    if s0 is None and tf_idx is not None:
        s0 = tf_idx - 1 if tf_idx > 0 and len(lines[tf_idx - 1]) <= 40 else tf_idx

    def block(start, end):
        if start is None:
            return []
        end = len(lines) if end is None else end
        return lines[start + 1:end]

    abstract_lines = block(a0, c0 if c0 is not None else (s0 if s0 is not None else len(lines)))
    if a0 is None and claim_idx is not None:   # 兜底：无"说明书摘要"大标题，摘要即首段
        abstract_lines = lines[:claim_idx]
    # 摘要块里去掉"摘要附图"及其后的图号行
    cut = len(abstract_lines)
    for i, ln in enumerate(abstract_lines):
        if HEAD_PATTERNS[1][1].match(ln):
            cut = i
            break
    abstract_lines = [x for x in abstract_lines[:cut] if not FIG_LINE.match(x)]

    claims_lines = block(c0, s0 if s0 is not None else len(lines))
    claims_lines = [x for x in claims_lines if not HEAD_PATTERNS[4][1].match(x)]

    spec_lines = block(s0, d0) if s0 is not None else []
    spec_lines = [x for x in spec_lines if not FIG_LINE.match(x)]

    # 发明名称：说明书标题区第一段（=文件名里的名称）
    title = title_hint or ""
    if not title and s0 is not None:
        cand = lines[s0]
        if not any(pt.match(cand) for pt in SEC.values()) and len(cand) <= 40:
            title = cand
    if not title:
        for ln in spec_lines:
            if not any(pt.match(ln) for pt in SEC.values()) and len(ln) <= 40:
                title = ln
                break

    # 分节
    sections = {}
    cur = None
    for ln in spec_lines:
        for name, pat in SEC.items():
            if pat.match(ln):
                cur = name
                sections.setdefault(name, [])
                break
        else:
            if cur:
                sections[cur].append(ln)

    tech_field = "\n".join(sections.get("技术领域", []))
    bg = "\n".join(sections.get("背景技术", []))
    summary = "\n".join(sections.get("发明内容", []))

    claim1 = ""
    claims_text = "\n".join(claims_lines)
    # 权1：从"1."到下一个"2."
    start = None
    for i, ln in enumerate(claims_lines):
        if CLAIM_NO.match(ln) and CLAIM_NO.match(ln).group(1) == "1":
            start = i
            break
    if start is not None:
        end = len(claims_lines)
        for j in range(start + 1, len(claims_lines)):
            m = CLAIM_NO.match(claims_lines[j])
            if m and m.group(1) != "1":
                end = j
                break
        claim1 = "\n".join(claims_lines[start:end])

    claim_count = sum(1 for ln in claims_lines if CLAIM_NO.match(ln))

    rec = {
        "pub": "", "app_no": "", "app_date": "",
        "applicants": [app] if app else [], "inventors": [], "agency": "", "agent": "",
        "ipc": "", "title": title, "abstract": "\n".join(abstract_lines),
        "claim1": claim1, "claims": claims_text, "claim_count": claim_count,
        "tech_field": tech_field, "background": bg, "summary": summary,
        "sections": sorted(sections.keys()),
        "pages": None, "text_file": txt_path, "source_file": os.path.basename(path),
        "source_type": "docx",
    }
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(full)
    json.dump(rec, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return rec


def main(argv):
    skip = set()
    for i, a in enumerate(argv):
        if a in ("--app", "--title") and i + 1 < len(argv):
            skip.add(argv[i + 1])
    files = [a for a in argv[1:] if not a.startswith("--") and a not in skip]
    force = "--force" in argv
    app = None
    title = None
    for i, a in enumerate(argv):
        if a == "--app" and i + 1 < len(argv):
            app = argv[i + 1]
        if a == "--title" and i + 1 < len(argv):
            title = argv[i + 1]
    if not files:
        print(__doc__)
        return 2
    ok = True
    for p in files:
        try:
            print(json.dumps(parse_docx(p, force, app, title), ensure_ascii=False))
        except Exception as e:
            ok = False
            print(json.dumps({"source_file": p, "error": str(e)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

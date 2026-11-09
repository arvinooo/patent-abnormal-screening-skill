#!/usr/bin/env python3
"""专利公开文本 PDF -> 结构化字段（带缓存，避免重复解析）。

用法:
    python3 scripts/extract_pdf.py <file.pdf> [--force]
    python3 scripts/extract_pdf.py <file.pdf> ... # 逐个输出 JSON 行

输出: 每个 PDF 一个 JSON 对象（stdout，一行一个）。
全文落盘 cache/pdf/<key>.txt，字段落盘 cache/pdf/<key>.json。
命中缓存时不重新解析（--force 强制重解析）。

字段: pub/app_no/app_date/applicants/inventors/agency/agent/ipc/title/abstract/
      claim1/claims/tech_field/pages/text_file
依赖: pdfplumber
"""
import hashlib
import json
import os
import re
import sys

CACHE = os.environ.get("PATENT_CACHE_DIR", "cache")
HEAD = {  # 著录项标签 -> 该块的标题文字（解析时剥掉）
    "10": "申请公布号", "43": "申请公布日", "21": "申请号", "22": "申请日",
    "71": "申请人", "73": "专利权人", "72": "发明人", "74": "专利代理机构",
    "51": "Int.Cl.", "54": "发明名称", "57": "摘要",
}
LBL = re.compile(r"\((\d{2})\)")
# 权利要求书/说明书页眉：权 利 要 求 书 / 说 明 书；页脚：CN 122828780 A 1/2 页
PAGE_HEAD = re.compile(r"^\s*(权\s*利\s*要\s*求\s*书|说\s*明\s*书|说明书附图)\s*$")
PAGE_MARK = re.compile(r"^\s*CN\s*\d+\s*[A-Z]\s*\d+\s*/\s*\d+\s*页\s*$")
JUNK = re.compile(r"^\s*[A-Z0-9]{1,3}\s*$")


def _blocks(page1):
    """按 (NN) 标签切第一页，返回 {label: [block, ...]}"""
    marks = [(m.start(), m.group(1)) for m in LBL.finditer(page1)]
    out = {}
    for i, (pos, lab) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(page1)
        out.setdefault(lab, []).append(page1[pos:end])
    return out


def _strip_label(block, label):
    text = re.sub(r"^\(\d{2}\)", "", block)
    head = HEAD.get(label, "")
    if head and text.startswith(head):
        text = text[len(head):]
    return text.strip()


def _lines(block, label):
    return [ln.strip() for ln in _strip_label(block, label).splitlines() if ln.strip()]


def _clean_body(text):
    keep = []
    for ln in text.splitlines():
        if PAGE_HEAD.match(ln) or PAGE_MARK.match(ln):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _cjk_join(text):
    """PDF 换行会插空格/断行：CJK 之间直接拼接，保留西文词间空格。"""
    text = re.sub(r"\s*\n\s*", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])[ \t]+"
                  r"(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])", "", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def parse_pdf(path, force=False):
    import pdfplumber

    key = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(os.path.join(CACHE, "pdf"), exist_ok=True)
    tfile = os.path.join(CACHE, "pdf", key + ".txt")
    jfile = os.path.join(CACHE, "pdf", key + ".json")

    if not force and os.path.exists(jfile) and os.path.exists(tfile):
        with open(jfile, encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("src_size") == os.path.getsize(path):
            rec["cached"] = True
            return rec

    with pdfplumber.open(path) as pdf:
        pages = [pg.extract_text() or "" for pg in pdf.pages]
    text = "\n".join(pages)
    with open(tfile, "w", encoding="utf-8") as f:
        f.write(text)

    p1 = pages[0] if pages else ""
    b = _blocks(p1)

    pub = ""
    for blk in b.get("10", []):
        m = re.search(r"CN\s*([\d\.]+)\s*([A-Z])", blk)
        if m:
            pub = "CN" + m.group(1).replace(".", "") + m.group(2)
            break

    def first(label, pat):
        for blk in b.get(label, []):
            m = re.search(pat, _strip_label(blk, label))
            if m:
                return m.group(1).strip()
        return ""

    app_no = first("21", r"([\d\.]{8,})")
    app_date = first("22", r"(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})").replace(".", "-").replace("/", "-")
    app_date = "-".join(p.zfill(2) for p in app_date.split("-")) if app_date else ""

    applicants = []
    ENT = re.compile(r"(公司|大学|学院|医院|研究院|研究所|中心|集团|厂|事务所|学校|"
                     r"局|委员会|协会|合作社|医院|大学|实验室)$")
    for blk in b.get("71", []) + b.get("73", []):
        seg, addr, cur = [], False, ""
        for ln in _lines(blk, "71" if blk.startswith("(71)") else "73"):
            if re.match(r"^[A-H]\d{2}[A-Z]\s*\d+/\d+", ln):
                continue  # 首页会把 IPC 挤进 (71) 块
            if ln.startswith("地址") or re.match(r"^\d{6}\s", ln):
                addr = True
                continue
            if ln.startswith("申请人") or ln.startswith("专利权人"):
                for pref in ("申请人", "专利权人"):
                    if ln.startswith(pref):
                        ln = ln[len(pref):].strip()
                        break
                addr = False
            elif addr:
                if not ENT.search(ln) and not re.search(r"(公司|大学|学院|医院|研究院|研究所)", ln):
                    continue  # 地址续行
                addr = False
            if cur and ENT.search(cur):
                seg.append(cur)
                cur = ln
            elif ln:
                cur += ln
        if cur:
            seg.append(cur)
        joined = ";".join(seg).replace("；", ";").replace("、", ";")
        applicants += [a.strip() for a in joined.split(";") if a.strip()]

    inventors = []
    for blk in b.get("72", []):
        joined = " ".join(_lines(blk, "72"))
        for n in re.split(r"[\s,，、;；]+", joined):
            # 首页会把 IPC 挤进 (72) 块：丢掉纯数字/斜杠/括号碎片
            if not n or re.match(r"^[\d/.()（）]+$", n) or re.match(r"^[A-H]\d{2}[A-Z]\d*$", n):
                continue
            inventors.append(n)

    agency, agent = "", ""
    for blk in b.get("74", []):
        raw = "\n".join(l for l in _strip_label(blk, "74").splitlines()
                         if not re.match(r"^[A-H]\d{2}[A-Z]\s*\d+/\d+", l))
        m = re.search(r"(.*?)\s*(\d{4,6})", _cjk_join(raw))
        if m:
            agency = m.group(1).strip()
        # 2021 前叫「代理人」，之后叫「专利代理师」；可多人，空格分隔
        m = re.search(r"(?:专利代理师|代理人)\s*([^\n]*)", raw)
        if m:
            agent = " ".join(x for x in re.split(r"[\s,，、;；]+", m.group(1).strip()) if x)

    ipc = []
    for label in ("21", "51", "10"):  # 首页布局会把部分 IPC 挤进 (21)
        for blk in b.get(label, []):
            ipc += re.findall(r"([A-H]\d{2}[A-Z])\s*(\d+/\d+)", _strip_label(blk, label))
    seen, uniq = set(), []
    for a, c in ipc:
        code = "%s %s" % (a, c)
        if code not in seen:
            seen.add(code)
            uniq.append(code)
    ipc = uniq

    title = " ".join(_lines(b["54"][0], "54")) if b.get("54") else ""
    title = re.sub(r"^\s*(发明|实用新型|外观设计)名称", "", title)
    title = re.sub(r"\s+", "", title)

    abstract = ""
    if b.get("57"):
        lines = [ln for ln in _lines(b["57"][0], "57") if not JUNK.match(ln)]
        abstract = "".join(lines)

    body = _clean_body(text)
    # 权利要求书整段与权1
    claims = body
    m = re.search(r"权\s*利\s*要\s*求\s*书", body)
    if m:
        claims = body[m.end():]
        m2 = re.search(r"(?m)^\s*说\s*明\s*书\s*$", claims)
        if m2:
            claims = claims[:m2.start()]
    claim1 = ""
    m = re.search(r"(?m)^\s*1\s*[.．、]\s*", claims)
    if m:
        rest = claims[m.end():]
        m2 = re.search(r"(?m)^\s*2\s*[.．、]\s*", rest)
        claim1 = (rest[:m2.start()] if m2 else rest).strip()
    claim1 = _cjk_join(claim1)

    tech_field = ""
    m = re.search(r"\[0001\]\s*(.*?)(?=\[0002\]|背景技术|技术背景|\Z)", body, re.S)
    if m:
        tech_field = _cjk_join(m.group(1)).strip()

    rec = {
        "file": os.path.abspath(path),
        "pub": pub, "app_no": app_no, "app_date": app_date,
        "applicants": applicants, "inventors": inventors,
        "agency": agency, "agent": agent, "ipc": ipc,
        "title": title, "abstract": abstract,
        "claim1": claim1, "claims": claims,
        "tech_field": tech_field,
        "pages": len(pages), "text_file": tfile,
        "src_size": os.path.getsize(path),
    }
    with open(jfile, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return rec


def main(argv):
    files = [a for a in argv[1:] if not a.startswith("-")]
    force = "--force" in argv
    if not files:
        print(__doc__)
        return 2
    ok = 0
    for f in files:
        try:
            rec = parse_pdf(f, force)
            out = {k: v for k, v in rec.items() if k not in ("claims", "abstract")}
            out["claim_count"] = len(re.findall(r"(?m)^\s*\d+\s*[.．、]\s*", rec["claims"]))
            print(json.dumps(out, ensure_ascii=False))
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"file": f, "error": "%s: %s" % (type(e).__name__, e)},
                             ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

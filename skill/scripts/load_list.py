#!/usr/bin/env python3
"""案件清单（xlsx/xls）读取与字段补齐。

用法:
    python3 scripts/load_list.py <清单.xlsx|.xls> [-o list.json]
    python3 scripts/load_list.py <清单> --merge 筛查结果.json -o 合并.json

用途:
    送审批次常带一份清单（incoPat 导出，或所内案件明细）。PDF/Word 里取不到的
    申请人、发明人、代理机构、申请日，从清单补；PDF 已有的字段以 PDF 为准。
    补完仍缺的字段才记待核，报告写清缺哪个字段。

兼容两种表头（按列名识别，不要求列序）:
    incoPat 导出：序号 / 标题(中文) / PDF链接 / 申请人 / 公开（公告）号 / 申请号 / 申请日 / 专利类型
    所内明细：  专利权人 / 专利名称 / 专利类型 / 发明人 / 代理机构 / 代理人

依赖: openpyxl（xlsx）；xlrd（旧版 xls）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ALIAS = {
    "title": ("标题(中文)", "标题", "专利名称", "发明名称", "案件名称"),
    "file": ("pdf链接", "文件名", "pdf文件名"),
    "applicant": ("申请人", "专利权人", "申请单位"),
    "pub": ("公开(公告)号", "公开号", "公告号", "公开(公告)号"),
    "app_no": ("申请号", "专利申请号"),
    "app_date": ("申请日",),
    "pub_date": ("公开(公告)日", "公开日", "公告日"),
    "ptype": ("专利类型",),
    "inventors": ("发明人", "发明(设计)人"),
    "agency": ("代理机构", "专利代理机构"),
    "agent": ("代理人", "代理师", "专利代理师"),
    "fee_reduce": ("是否费减",),
}
ID18 = re.compile(r"\d{17}[\dXx]")


def _norm(s) -> str:
    """表头/匹配键用：去空白、全角括号转半角、小写。"""
    if s is None:
        return ""
    s = re.sub(r"[\s\u3000]+", "", str(s))
    return s.replace("（", "(").replace("）", ")").lower()


def _clean(v) -> str:
    """单元格值用：去首尾空白、压空格、保留中文全角括号、去掉时间尾巴。"""
    if v is None:
        return ""
    s = str(v).strip()
    s = re.sub(r"\s+00:00:00$", "", s)
    return re.sub(r"[\s\u3000]+", "", s)


def _split_names(raw: str) -> list[str]:
    """发明人栏去掉身份证号，按常见中文分隔符切开。"""
    s = ID18.sub("", raw or "")
    parts = [x.strip(" ，,、;；") for x in re.split(r"[，,、;；\s]+", s)]
    return [x for x in parts if 1 < len(x) <= 6 and not ID18.search(x)]


def _read_rows(path: str) -> list[dict]:
    """读第一张工作表，返回以原表头为键的行。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    elif ext == ".xls":
        import xlrd
        sh = xlrd.open_workbook(path).sheets()[0]
        rows = [sh.row_values(i) for i in range(sh.nrows)]
    else:
        raise SystemExit("只支持 xlsx / xls：%s" % path)
    if not rows:
        return []
    header = [_norm(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(_norm(c) for c in r):
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
    return out


def _pick(row: dict, field: str) -> str:
    for alias in ALIAS[field]:
        for k, v in row.items():
            if _norm(k) == _norm(alias) and _clean(v):
                return _clean(v)
    return ""


LABEL = re.compile(r"^(?:申请人|专利权人|申请单位)[:：]?|;(?:申请人|专利权人|申请单位)[:：]?")


def _clean_applicants(raw: str) -> str:
    """incoPat 导出会把「申请人」字样拼进单元格，去掉标签、多主体用分号连。"""
    parts = [LABEL.sub("", x).strip(" ;；") for x in re.split(r"[;；]", raw or "")]
    return ";".join(x for x in parts if x)


def parse_list(path: str) -> list[dict]:
    rows = []
    for raw in _read_rows(path):
        rec = {
            "title": _pick(raw, "title"),
            "file": _pick(raw, "file"),
            "applicant": _clean_applicants(_pick(raw, "applicant")),
            "pub": _pick(raw, "pub").upper(),
            "app_no": _pick(raw, "app_no"),
            "app_date": _pick(raw, "app_date"),
            "ptype": _pick(raw, "ptype"),
            "agency": _pick(raw, "agency"),
            "agent": _pick(raw, "agent"),
            "inventors": _split_names(_pick(raw, "inventors")),
            "fee_reduce": _pick(raw, "fee_reduce"),
        }
        if any(rec.values()):
            rows.append(rec)
    return rows


def _key(rec: dict) -> str:
    """匹配键优先级：公开号 > 申请号 > 标题 > 文件名去扩展名。"""
    pub = re.sub(r"[^0-9A-Z]", "", (rec.get("pub") or "").upper())
    if pub:
        return "pub:" + pub
    app_no = re.sub(r"[^0-9.]", "", rec.get("app_no") or "")
    if app_no:
        return "app:" + app_no
    title = _norm(rec.get("title"))
    if title:
        return "title:" + title
    f = os.path.basename(rec.get("file") or "")
    return "file:" + _norm(os.path.splitext(f)[0])


FILL_FIELDS = ("applicant", "title", "app_date", "pub", "app_no", "agency", "agent", "inventors")


def merge(rows: list[dict], listed: list[dict]) -> tuple[list[dict], dict]:
    """PDF/Word 已有字段优先；只补空字段。返回合并结果与统计。"""
    idx = {}
    for rec in listed:
        idx.setdefault(_key(rec), rec)
        if rec.get("pub"):
            idx.setdefault("file:" + _norm(os.path.splitext(os.path.basename(rec.get("file") or ""))[0]),
                           rec)
    filled = {f: 0 for f in FILL_FIELDS}
    hit = 0
    for row in rows:
        rec = idx.get(_key({"pub": row.get("pub"), "app_no": row.get("app_no"),
                            "title": row.get("title"), "file": row.get("file")}))
        if not rec:
            continue
        hit += 1
        for f in FILL_FIELDS:
            cur = row.get(f)
            new = rec.get(f)
            if (not cur) and new:
                row[f] = new
                filled[f] += 1
        row.setdefault("list_source", "清单")
    return rows, {"matched": hit, "filled": filled,
                  "unmatched_list": max(0, len(listed) - len({_key(r) for r in listed}) + 0)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="案件清单读取与字段补齐")
    ap.add_argument("list", help="xlsx / xls 清单")
    ap.add_argument("--merge", help="筛查结果 JSON（text_screen --json 的输出），补空字段")
    ap.add_argument("-o", "--out", help="输出 JSON")
    a = ap.parse_args(argv)

    listed = parse_list(a.list)
    if a.merge:
        rows = [x for x in json.load(open(a.merge, encoding="utf-8")) if "file" in x]
        rows, stat = merge(rows, listed)
        payload = rows                      # 与 text_screen --json 同形，可直接喂 build_report
        msg = f"已写入 {a.out}：匹配 {stat['matched']} 件，补字段 {stat['filled']}"
    else:
        payload = {"list": listed}
        stat = {"rows": len(listed)}
        msg = f"已写入 {a.out}：清单 {stat['rows']} 行"

    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(msg, file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

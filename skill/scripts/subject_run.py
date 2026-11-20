#!/usr/bin/env python3
"""第二层一键跑：工商 → T23 → T20 → 定案，输出逐案结论 JSON。

用法:
    python3 scripts/subject_run.py --cases 合并.json -o 终版.json
    python3 scripts/subject_run.py --cases 合并.json -o 终版.json --no-fetch   # 只吃缓存重算结论

流程（只处理 verdict=存疑 的行）:
    1. company_lookup  ：工商四项 + 专精特新（院校/自然人跳过，缓存优先，默认天眼查）
    2. inco_granted    ：T23 授权发明（企业，院校不适用；缓存 90 天）
    3. subject_inventor：T20 发明人件数（IPC 写进检索式；缓存 facts）
    4. 定案            ：八项一票否决；缺字段/额度不足/查询超时记待核并写明原因

判据与阈值见 thresholds.md；本脚本只负责取数与拼装，不改口径。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import datetime as dt
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHOOL = re.compile(r"大学|学院|学校|研究所|研究院|科学院|医院|疾病预防控制|检测中心|管理局|"
                    r"管理委员会|管理中心|推广服务中心|事业单位")
BAD = re.compile(r"文化|传媒|茶|贸易|餐饮|咨询|投资|房地产|物流")
RD = re.compile(r"研发|技术开发|技术咨询|软件开发|系统集成|设计|制造|生产|加工|检测|试验|技术推广|科学研究")
COMPANY = re.compile(r"公司|集团|厂|研究院|大学|事务所")


def _run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _num(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _dims(case: dict, company: dict, granted: dict) -> tuple[list[str], bool]:
    """企业类维度（T23–T27）命中项；返回 (命中理由, 是否专精特新豁免)。"""
    ap = case.get("applicant") or ""
    reg = (company.get(ap) or {}).get("reg") or {}
    hon = (company.get(ap) or {}).get("honor") or {}
    if hon.get("waived"):
        return [], True
    if SCHOOL.search(ap):
        return [], False
    hits = []
    g = granted.get(ap) or {}
    if g.get("granted_invention") == 0:
        hits.append("授权发明专利为 0")
    insured = _num(reg.get("参保人数"))
    if insured is not None and insured < 3:
        hits.append(f"参保 {insured} 人")
    if BAD.search(ap + " " + str(reg.get("国标行业") or "")) and not RD.search(str(reg.get("经营范围") or "")):
        hits.append("主业与研发无关")
    return hits, False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="第二层一键跑")
    ap.add_argument("--cases", required=True, help="第一层 + 清单合并后的逐案 JSON")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--no-fetch", action="store_true", help="只吃缓存重算，不开浏览器/不调平台")
    a = ap.parse_args(argv)

    cases = json.load(open(a.cases, encoding="utf-8"))
    suspect = [x for x in cases if x.get("verdict") == "存疑"]
    work = tempfile.mkdtemp(prefix="subject-")
    apps = sorted({x["applicant"] for x in suspect if x.get("applicant")})

    # 1. 工商 + 专精特新
    company = {}
    if apps:
        apps_file = os.path.join(work, "apps.json")
        json.dump(apps, open(apps_file, "w", encoding="utf-8"), ensure_ascii=False)
        cmd = [sys.executable, os.path.join(HERE, "company_lookup.py"), "--apps-file", apps_file]
        if a.no_fetch:
            cmd.append("--source")
            cmd.append("tyc")
        r = _run(cmd)
        try:
            company = {x["applicant"]: x for x in json.loads(r.stdout).get("results", [])}
        except ValueError:
            print("company_lookup 未返回 JSON：" + (r.stdout + r.stderr)[:200], file=sys.stderr)

    # 2. T23
    granted = {}
    t23 = []
    for x in suspect:
        apn = x.get("applicant") or ""
        if not apn or SCHOOL.search(apn):
            continue
        d = (x.get("app_date") or "").replace(".", "-")[:10]
        t23.append({"applicant": apn, "base_date": d if len(d) == 10 else ""})
    # 同一申请人取最晚基准日
    latest: dict[str, str] = {}
    for it in t23:
        latest[it["applicant"]] = max(latest.get(it["applicant"], ""), it["base_date"] or "")
    if latest:
        f = os.path.join(work, "t23.json")
        json.dump([{"applicant": k, "base_date": v or "2026-01-01"} for k, v in latest.items()],
                  open(f, "w", encoding="utf-8"), ensure_ascii=False)
        cmd = [sys.executable, os.path.join(HERE, "inco_granted.py"), "--json", f]
        if a.no_fetch:
            cmd.append("--cache-only")
        r = _run(cmd)
        try:
            granted = {x["applicant"]: x for x in json.loads(r.stdout).get("results", [])}
        except ValueError:
            print("inco_granted 未返回 JSON：" + (r.stdout + r.stderr)[:200], file=sys.stderr)

    # 3. 已由企业类维度定案的，不再跑 T20
    convicted = {}
    for x in suspect:
        hits, waived = _dims(x, company, granted)
        if hits and not waived:
            convicted[x["file"]] = "；".join(hits)
    remain = [x for x in suspect if x["file"] not in convicted]
    inventor = {}
    if remain and a.no_fetch:
        import cache as C
        facts = C._load("facts")
        for x in remain:
            name = (x.get("inventors") or [None])[0]
            if not name:
                continue
            d = (x.get("app_date") or "").replace(".", "-")[:10]
            if len(d) != 10:
                d = dt.date.today().isoformat()
            start = (dt.date.fromisoformat(d) - dt.timedelta(days=365)).strftime("%Y%m%d")
            rec = facts.get(f"T20|{name}|{start}-{d.replace('-', '')}")
            if rec:
                inventor[x["file"]] = {"n": rec.get("n"), "inventor": name, "orgs": []}
    elif remain:
        f = os.path.join(work, "remain.json")
        json.dump(remain, open(f, "w", encoding="utf-8"), ensure_ascii=False)
        out = os.path.join(work, "inventor.json")
        r = _run([sys.executable, os.path.join(HERE, "subject_inventor.py"),
                  "--cases", f, "-o", out])
        if os.path.exists(out):
            for rec in json.load(open(out, encoding="utf-8")).get("results", []):
                for fn in rec.get("files") or []:
                    inventor[fn] = rec
        else:
            print("subject_inventor 未产出：" + (r.stdout + r.stderr)[:200], file=sys.stderr)

    # 4. 定案
    out_rows = []
    for x in cases:
        x = dict(x)
        if x.get("verdict") != "存疑":
            out_rows.append(x)
            continue
        if x["file"] in convicted:
            x["verdict"], x["reason"] = "非正常", convicted[x["file"]]
            out_rows.append(x)
            continue
        rec = inventor.get(x["file"]) or {}
        n = rec.get("n")
        name = (x.get("inventors") or [None])[0]
        if n is not None and n >= 5:
            x["verdict"] = "非正常"
            x["reason"] = f"发明人{name}在本案申请日前 12 个月内有 {n} 件申请"
            out_rows.append(x)
            continue
        hits, waived = _dims(x, company, granted)
        if hits:
            x["verdict"], x["reason"] = "非正常", "；".join(hits[:2])
            out_rows.append(x)
            continue
        pending = []
        appn = x.get("applicant") or ""
        if not name:
            pending.append("PDF 与清单都没有发明人")
        elif n is None:
            pending.append("发明人件数与跨机构未核")
        elif len([o for o in (rec.get("orgs") or []) if COMPANY.search(str(o))]) >= 3:
            pending.append(f"发明人{name}涉及 3 家以上机构，关联关系未核")
        if not SCHOOL.search(appn) and (company.get(appn) or {}).get("status") != "ok":
            pending.append("工商数据没取到")
        if not waived and appn and not SCHOOL.search(appn) and appn not in granted:
            pending.append("授权发明专利没查")
        if pending:
            x["verdict"], x["reason"] = "待核", "；".join(pending)
            out_rows.append(x)
            continue
        x["verdict"] = "正常"
        out_rows.append(x)

    json.dump(out_rows, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    stat = collections.Counter(x["verdict"] for x in out_rows)
    print(json.dumps({"total": len(out_rows), "verdicts": dict(stat),
                      "companies": len(company), "granted": len(granted),
                      "inventors": len(inventor)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

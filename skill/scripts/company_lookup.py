#!/usr/bin/env python3
"""第二层企业资质取数：去重、跳过不适用主体、缓存优先、积分耗尽即停。

用法:
    python3 scripts/company_lookup.py --apps-file apps.json      # ["A公司", …]
    python3 scripts/company_lookup.py --apps "A公司,B公司"

为什么要有这一层:
  - 企查查按次扣积分，一批 99 家会在 30 家左右耗尽（实测 402）。
  - 院校、医院、研究所不适用 T23–T27，也不可能是专精特新，查了纯浪费。
  - 自然人没有工商主体，T24 直接按申请人栏判，不用查。
  - 同一主体多案只查一次；命中缓存不重复花钱。

输出: 一行 JSON
  results[]  每家的 status（ok / skipped_school / skipped_person / missing / credit_exhausted）
              + reg（参保人数、国标行业、经营范围等）+ honor（专精特新档位）
  summary    花了多少积分调用、命中多少缓存、因积分耗尽剩多少
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cache

SCHOOL = re.compile(r"大学|学院|学校|研究所|研究院|科学院|医院|疾病预防控制中心|检测中心|"
                    r"事业单位|管理局|委员会|协会|学会|中心$")
PERSON = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")
CREDIT = re.compile(r"积分余额不足|积分不足|402")


def _qcc(args: list[str], timeout: int = 60) -> dict:
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"_raw": (r.stdout + r.stderr)[:200]}


TYC = ["npx", "-y", "tyc-cli@latest", "company", "registration-info"]


def _tyc_reg(name: str, timeout: int = 90) -> dict:
    """天眼查工商登记（免费/会员共用同一额度）。字段名与企查查同义，共用缓存与报告。"""
    r = subprocess.run(TYC + [name, "--compact"], capture_output=True, text=True,
                       timeout=timeout)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return {"_error": "天眼查调用失败", "_raw": (r.stdout + r.stderr)[:200]}
    base = (d.get("sources") or {}).get("base") or d.get("base") or {}
    if not base:
        return {"_error": "天眼查无此主体", "_raw": (r.stdout + r.stderr)[:200]}
    tags = base.get("tags") or ""
    if isinstance(tags, list):
        tags = ";".join(map(str, tags))
    return {
        "企业名称": base.get("name") or name,
        "参保人数": base.get("socialStaffNum"),
        "国标行业": base.get("industry"),
        "经营范围": base.get("businessScope"),
        "成立日期": (base.get("estiblishTime") or "")[:10],
        "企业类型": base.get("companyOrgType"),
        "法定代表人": base.get("legalPersonName"),
        "登记状态": base.get("regStatus"),
        "tags": tags,
        "_source": "天眼查",
    }


def _honor_level(honor: dict) -> dict:
    """只认国家级小巨人、省级专精特新；其他荣誉不算豁免。"""
    txt = json.dumps(honor, ensure_ascii=False)
    if "积分余额不足" in txt or "402" in txt[:80]:
        return {"error": "credit_exhausted"}
    items = honor.get("honors") if isinstance(honor, dict) else None
    if not isinstance(items, list):
        # 兼容直接返回荣誉数组/其它形状
        items = honor if isinstance(honor, list) else []
    names, levels = [], []
    for it in items:
        if not isinstance(it, dict):
            continue
        n, lv = str(it.get("名称") or ""), str(it.get("级别") or "")
        names.append(n)
        levels.append(lv)
    level = ""
    for n, lv in zip(names, levels):
        if "小巨人" in n and "国家级" in lv:
            level = "国家级小巨人"
            break
        if "专精特新中小企业" in n and "省级" in lv:
            level = "省级专精特新"
    return {"waived": bool(level), "level": level, "names": names, "levels": levels}


def _reg_brief(reg: dict) -> dict:
    keys = ("企业名称", "参保人数", "国标行业", "经营范围", "成立日期", "企业类型",
            "法定代表人", "登记状态", "注册资本")
    out = {k: reg.get(k) for k in keys if reg.get(k) not in (None, "")}
    if "无匹配项" in reg:
        out["missing"] = True
    if "积分余额不足" in json.dumps(reg, ensure_ascii=False):
        out["error"] = "credit_exhausted"
    return out


def _cached(ns: str, key: str, days: int) -> dict | None:
    rec = cache._load(ns).get(key)
    if not rec or not cache._schema_ok(ns, rec) or cache._age_days(rec) > days:
        return None
    return rec


def _store(ns: str, key: str, payload: dict) -> None:
    if not payload:
        return
    store = cache._load(ns)
    rec = dict(payload)
    rec.setdefault("_schema", cache.SCHEMA.get(ns, 1))
    rec.setdefault("_fetched", __import__("time").strftime("%Y-%m-%d"))
    store[key] = rec
    cache._save(ns, store)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="企业资质批量取数（缓存优先）")
    ap.add_argument("--apps-file", help="JSON 文件：申请人字符串数组")
    ap.add_argument("--apps", help="逗号分隔的申请人")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--source", choices=("qcc", "tyc", "auto"), default="auto",
                    help="qcc=企查查；tyc=天眼查；auto=企查查积分不足时自动切天眼查")
    ap.add_argument("--max-age", type=int, default=90)
    a = ap.parse_args(argv)

    if a.apps_file:
        apps = json.load(open(a.apps_file, encoding="utf-8"))
    elif a.apps:
        apps = [x.strip() for x in a.apps.split(",") if x.strip()]
    else:
        print(json.dumps({"error": "需要 --apps-file 或 --apps"}, ensure_ascii=False))
        return 2

    seen, results = set(), []
    live = cached = 0
    dead = a.source == "tyc"          # 直接指定天眼查：跳过企查查
    tyc_dead = False
    for name in apps:
        if not name or name in seen:
            continue
        seen.add(name)
        if SCHOOL.search(name):
            results.append({"applicant": name, "status": "skipped_school",
                            "note": "院校/医院/研究所：T23–T27 不适用，不可能是专精特新"})
            continue
        if PERSON.fullmatch(name):
            results.append({"applicant": name, "status": "skipped_person",
                            "note": "自然人：无工商主体，T24 按申请人栏判"})
            continue
        reg = None if a.no_cache else _cached("company", name, a.max_age)
        hon = None if a.no_cache else _cached("company_honor", name, a.max_age)
        if reg and hon:
            cached += 1
            results.append({"applicant": name, "status": "ok", "source": "cache",
                            "reg": _reg_brief(reg), "honor": _honor_level(hon)})
            continue
        def via_tyc():
            """天眼查取数；额度不足返回 credit_exhausted 记录。"""
            nonlocal live, tyc_dead
            if tyc_dead:
                return {"applicant": name, "status": "credit_exhausted",
                        "note": "本批额度已耗尽，未调用"}
            reg = _tyc_reg(name)
            live += 1
            if reg.get("_error"):
                tyc_dead = True
                return {"applicant": name, "status": "credit_exhausted",
                        "note": "天眼查额度不足或调用失败，未取到工商数据", "reg": reg}
            _store("company", name, reg)
            hc = None if a.no_cache else _cached("company_honor", name, a.max_age)
            if hc:
                honor = _honor_level(hc)
                honor["note"] = "荣誉取自企查查缓存"
            else:
                honor = {"waived": "专精特新" in (reg.get("tags") or ""),
                         "level": "", "names": [], "levels": [],
                         "note": "天眼查 tags 未含专精特新认定，按未豁免处理"}
            return {"applicant": name, "status": "ok", "source": "tyc",
                    "reg": _reg_brief(reg), "honor": honor}

        if a.source == "tyc" or dead:
            results.append(via_tyc())
            continue

        if reg is None:
            reg = _qcc(["qcc", "company", "get_company_registration_info", name, "--json"])
            live += 1
            if "积分余额不足" in json.dumps(reg, ensure_ascii=False):
                dead = True
        if hon is None and not dead:
            hon = _qcc(["qcc", "operation", "get_honor_info", name, "--json"])
            live += 1
            if "积分余额不足" in json.dumps(hon, ensure_ascii=False):
                dead = True
        if dead:                       # 本次刚发现积分不足 → 立即切天眼查，不留空转
            results.append(via_tyc())
            continue
        _store("company", name, reg or {})
        if hon:
            _store("company_honor", name, hon)
        results.append({"applicant": name,
                        "status": "missing" if "无匹配项" in (reg or {}) else "ok",
                        "source": "live", "reg": _reg_brief(reg or {}),
                        "honor": _honor_level(hon or {})})

    print(json.dumps({"results": results,
                      "summary": {"applicants": len(results), "live_calls": live,
                                  "cache": cached,
                                  "credit_exhausted": sum(1 for r in results
                                                          if r["status"] == "credit_exhausted"),
                                  "skipped": sum(1 for r in results
                                                 if r["status"].startswith("skipped"))}},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

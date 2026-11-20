#!/usr/bin/env python3
"""T23 授权发明专利：多申请人批量计数（比逐家单查快约 2 倍，口径不变）。

用法:
    python3 scripts/inco_granted.py --json apps.json        # [{"applicant":"…","base_date":"2025-08-14"}, …]
    python3 scripts/inco_granted.py --apps "A公司,B公司" --base-date 2025-08-14

输出: 一行 JSON，results 逐申请人给出 granted_invention / mode / source。
缓存: 命名空间 granted，键 = 申请人|基准日，90 天；命中不重复取数。
口径与 `inco_client.granted_invention()` 一致：AP=(申请人) AND AD=[19850101 TO 基准日]，
只数公开号尾码 B（发明授权公告）的行；超翻页上限自动回退单查。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cache
import inco_client as INCO

NS = "granted"
MAX_AGE = 90


def _get(applicant: str, base: str) -> dict | None:
    rec = cache._load(NS).get(f"{applicant}|{base}")
    if not rec or not cache._schema_ok(NS, rec) or cache._age_days(rec) > MAX_AGE:
        return None
    return rec


def _set(applicant: str, base: str, payload: dict) -> None:
    store = cache._load(NS)
    rec = dict(payload)
    rec.setdefault("_schema", cache.SCHEMA.get(NS, 1))
    rec["_fetched"] = payload.get("_fetched") or __import__("time").strftime("%Y-%m-%d")
    store[f"{applicant}|{base}"] = rec
    cache._save(NS, store)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="批量查授权发明专利（T23）")
    ap.add_argument("--json", help="JSON 文件：[{applicant, base_date}, …]")
    ap.add_argument("--apps", help="逗号分隔的申请人")
    ap.add_argument("--base-date", help="统一基准日 YYYY-MM-DD（与 --apps 搭配）")
    ap.add_argument("--chunk", type=int, default=10, help="每批申请人数量，默认 10")
    ap.add_argument("--no-cache", action="store_true", help="不吃缓存，强制重查")
    ap.add_argument("--cache-only", action="store_true", help="只读缓存，未命中记 null 不查")
    a = ap.parse_args(argv)

    items: list[dict] = []
    if a.json:
        items = json.load(open(a.json, encoding="utf-8"))
    elif a.apps:
        items = [{"applicant": x.strip(), "base_date": a.base_date or ""}
                 for x in a.apps.split(",") if x.strip()]
    if not items:
        print(json.dumps({"error": "需要 --json 或 --apps"}, ensure_ascii=False))
        return 2

    results: list[dict] = []
    todo: list[tuple[str, str]] = []
    seen = set()
    for it in items:
        ap_name = it.get("applicant") or ""
        base = (it.get("base_date") or "").replace("-", "")[:8]
        key = (ap_name, base)
        if not ap_name or key in seen:
            continue
        seen.add(key)
        hit = None if a.no_cache else _get(ap_name, base)
        if hit:
            rec = dict(hit)
            rec.setdefault("applicant", ap_name)
            rec.setdefault("base_date", base)
            rec["source"] = "cache"
            results.append(rec)
        else:
            todo.append(key)

    if todo and a.cache_only:
        for key in todo:
            results.append({"applicant": key[0], "base_date": key[1],
                            "granted_invention": None, "source": "cache-miss"})
        todo = []
    if todo:
        batch = INCO.granted_invention_batch(todo, chunk=a.chunk)
        for key in todo:
            rec = batch.get(key[0]) or {"applicant": key[0], "base_date": key[1],
                                        "granted_invention": None, "error": "批量结果缺失"}
            rec["source"] = "live"
            _set(key[0], key[1], rec)
            results.append(rec)

    print(json.dumps({"results": results,
                      "summary": {"total": len(results),
                                  "live": sum(1 for r in results if r.get("source") == "live"),
                                  "cache": sum(1 for r in results if r.get("source") == "cache")}},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

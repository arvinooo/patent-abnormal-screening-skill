#!/usr/bin/env python3
"""外部取数缓存：查一次落盘，下个会话先读缓存，不再花额度。

用法:
    python3 scripts/cache.py get company  "深圳XX有限公司" [--max-age 90]
    python3 scripts/cache.py set company  "深圳XX有限公司" --json '{"insured":9,...}'
    python3 scripts/cache.py get agent    "张亮" [--max-age 1]
    python3 scripts/cache.py get inventor "金锐博" [--max-age 7]
    python3 scripts/cache.py list company
    python3 scripts/cache.py prune --max-age 365

规则:
    - 键是实体名（公司全名 / 代理师姓名 / 发明人姓名），值是**原始取数结果**，
      派生结论（是否>阈值）每次现算，不缓存——阈值会变，原始数据不会。
    - get 命中: 输出 JSON（含 _fetched 取数日、_age_days 龄期），exit 0
      get 未命中/过期: 输出 null，exit 1（agent 据此决定要不要真去查）
    - 写入是原子的（临时文件 + rename），并发跑批不会写坏。
"""
import json
import os
import sys
import tempfile
import time

CACHE = os.environ.get("PATENT_CACHE_DIR", "cache")
NS = ("company", "agent_month", "inventor", "other")

# 每个命名空间的当前数据口径（schema 版本）。
# 缓存命中必须同时满足：口径一致（_schema 相同且 _valid 不为 False）+ 在有效期内。
# 口径对不上 = 未命中，一律重查，查到再写回。
SCHEMA = {"company": 1, "agent_month": 2, "inventor": 1, "other": 1}


def _path(ns):
    if ns not in NS:
        raise SystemExit("unknown namespace %r (可选: %s)" % (ns, ", ".join(NS)))
    return os.path.join(CACHE, ns + ".json")


def _load(ns):
    p = _path(ns)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def _save(ns, data):
    os.makedirs(CACHE, exist_ok=True)
    p = _path(ns)
    fd, tmp = tempfile.mkstemp(dir=CACHE, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _age_days(rec):
    stamp = rec.get("_fetched") or ""
    try:
        fetched = time.mktime(time.strptime(stamp, "%Y-%m-%d"))
    except (ValueError, TypeError):
        return 10 ** 6
    return max(0.0, (time.time() - fetched) / 86400.0)


def _schema_ok(ns, rec):
    """口径校验：旧 schema 或显式标记无效的记录一律当未命中。"""
    want = SCHEMA.get(ns)
    if rec.get("_valid") is False:
        return False
    if want is not None and int(rec.get("_schema", 1)) != want:
        return False
    return True


def cmd_get(ns, key, max_age):
    rec = _load(ns).get(key)
    if not rec:
        print("null")
        return 1
    if not _schema_ok(ns, rec):
        print(json.dumps({"schema_mismatch": True, "_schema": rec.get("_schema", 1),
                          "want": SCHEMA.get(ns), "value": rec}, ensure_ascii=False))
        return 1
    age = _age_days(rec)
    if age > max_age:
        print(json.dumps({"stale": True, "_fetched": rec.get("_fetched"),
                          "_age_days": round(age, 1), "value": rec}, ensure_ascii=False))
        return 1
    out = dict(rec)
    out["_age_days"] = round(age, 1)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_set(ns, key, payload):
    data = dict(payload)
    data.setdefault("_schema", SCHEMA.get(ns, 1))
    data["_fetched"] = payload.get("_fetched") or time.strftime("%Y-%m-%d")
    data["_set_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    store = _load(ns)
    store[key] = data
    _save(ns, store)
    print(json.dumps({"ok": True, "namespace": ns, "key": key,
                      "total_keys": len(store)}, ensure_ascii=False))
    return 0


def cmd_list(ns):
    store = _load(ns)
    rows = [{"key": k, "_fetched": v.get("_fetched"), "_age_days": round(_age_days(v), 1)}
            for k, v in sorted(store.items())]
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


def cmd_prune(max_age):
    removed = 0
    for ns in NS:
        store = _load(ns)
        keep = {k: v for k, v in store.items() if _age_days(v) <= max_age}
        removed += len(store) - len(keep)
        if keep != store:
            _save(ns, keep)
    print(json.dumps({"removed": removed}, ensure_ascii=False))
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    rest = argv[2:]
    opts = {a.split("=", 1)[0].lstrip("-"): a.split("=", 1)[1]
            for a in rest if a.startswith("--") and "=" in a}
    if "--max-age" in rest:
        i = rest.index("--max-age")
        opts["max-age"] = rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    if "--json" in rest:
        i = rest.index("--json")
        opts["json"] = rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    args = [a for a in rest if not a.startswith("--")]

    if cmd == "get":
        if len(args) != 2:
            print(__doc__)
            return 2
        return cmd_get(args[0], args[1], float(opts.get("max-age", 90)))
    if cmd == "set":
        if len(args) != 2 or "json" not in opts:
            print(__doc__)
            return 2
        return cmd_set(args[0], args[1], json.loads(opts["json"]))
    if cmd == "list":
        return cmd_list(args[0] if args else "company")
    if cmd == "prune":
        return cmd_prune(float(opts.get("max-age", 365)))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

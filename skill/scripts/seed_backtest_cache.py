#!/usr/bin/env python3
"""回测数据快照（2026-11-09 实测值）。

为什么要有这个文件：
    回测要可复现、不能每次花额度重取。这里把本次**已经实测并核对过**的外部数据
    写成缓存快照，供 backtest_v2.py 离线跑。每条都标了来源。

数据来源：
    发明人 12 个月记录 + IPC     incoPat（浏览器，2026-11-09 逐条核对申请人/IPC）
    授权发明专利件数              incoPat（4 家均为 0）
    荣誉（专精特新）             企查查 get_honor_info（2026-11-09）
    代理师月度代理量              香橼树月度统计（2026-11-09，汝涛逐月实测）

注意：本文件是标定快照，非实时取数，仅用于回测；新批次取数不使用本文件。
"""
from __future__ import annotations

import datetime as dt
import os

import v2_fetch as F

TODAY = dt.date.today().isoformat()


def inv(name, records, total):
    return {"inventor": name, "records": records, "total_raw": total,
            "window_start": "20251109", "window_end": "20261109",
            "_fetched": TODAY, "_source": "incoPat（回测快照）"}


def rec(app_no, title, ipc, applicants, inventors=None, is_design=False):
    return {"application_number": app_no, "application_date": "", "title": title,
            "inventors": inventors or [], "applicants": applicants, "ipc": ipc,
            "is_design": is_design}


# ---- 谢建政：12 个月 0 条（名下共 9 条，全为 2020-2022 及 2007-2008）
XIE_JIANZHENG = inv("谢建政", [], 9)

# ---- 谢章龙：12 个月 3 条，全部南京钢铁（同名他人，钢铁冶金）
XIE_ZHANGLONG = inv("谢章龙", [
    rec("CN202610641437.6", "一种高纯净钢水的制备方法", "C21C5/28", ["南京钢铁股份有限公司"]),
    rec("CN202610526109.1", "一种薄规格低屈服高韧性5Ni钢及其制造方法", "C22C38/02", ["南京钢铁股份有限公司"]),
    rec("CN202610502036.2", "一种高速磁悬浮列车导轨用软磁钢板及其生产方法", "C22C38/02", ["南京钢铁股份有限公司"]),
], 92)

# ---- 陈海佳：12 个月 6 条，全部别家（电池检测/能耗计量/厨具/干细胞医学）
#      注：后 3 条在结果表里申请号被截断，留 None（不影响剔重名，只影响展示）
CHEN_HAIJIA = inv("陈海佳", [
    rec("CN202611241454.7", "电池的寿命预测方法及系统", "G01R31/392", ["浙江正泰水务科技有限公司"]),
    rec("CN202611241480.X", "通信设备的能耗计量方法及系统", "G01R22/06", ["浙江正泰水务科技有限公司"]),
    rec("CN202630096316.9", "铝锅（外观）", "", ["武义天聪厨具有限公司"], is_design=True),
    rec(None, "人支气管基底层细胞培养基在制备治疗慢阻肺药物中的应用", "A61K35/42",
        ["广州赛莱拉干细胞科技股份有限公司", "广东国科细胞科技有限公司"]),
    rec(None, "一种评价间充质干细胞促Treg细胞增殖能力的检测方法", "C12Q1/02",
        ["广州赛莱拉干细胞科技股份有限公司", "广东国科细胞科技有限公司"]),
    rec(None, "适用于间充质干细胞三维培养的化学成分限定型无血清培养基", "C12N5/0775",
        ["广州赛莱拉干细胞科技股份有限公司", "广东国科细胞科技有限公司"]),
], 1103)

# ---- 林美华：12 个月 7 条，全部别家（职院外观/国网/开关柜）
LIN_MEIHUA = inv("林美华", [
    rec("CN202630152631.9", "手办模型（2）", "", ["福州软件职业技术学院"], is_design=True),
    rec("CN202630144861.0", "围炉煮茶设备", "", ["福州软件职业技术学院"], is_design=True),
    rec("CN202610670019.X", "变电站多套别设备免配置自动测试方法及系统", "H04L43/18", ["国网福建省电力有限公司"]),
    rec("CN202610671333.X", "一种基于网络报文的录波器录波通道自配置方法及系统", "H02J13/12", ["国网福建省电力有限公司"]),
    rec("CN202610767198.9", "一种高低压开关柜的转运车", "B65G35/00", ["福州万升电器有限公司"]),
    rec("CN202610676097.0", "变电站二次设备IP地址标准化自动映射方法", "H04L61/5007", ["国网福建省电力有限公司"]),
    rec("CN202610284000.1", "基于在线迁移学习的综合能源实时能量管理方法", "G06Q10/0631",
        ["国网山东省电力公司枣庄供电公司"]),
], 175)

# ---- 李尚发：12 个月 0 条（名下 16 条全在德吾安）
LI_SHANGFA = inv("李尚发", [], 16)

# ---- 王猛：常见名，总量上万，不逐条核对
WANG_MENG = inv("王猛", [], 15870)
WANG_MENG["common_name"] = True
WANG_MENG["note"] = "高频同名（1.5 万组），按规则标常见名、计数不可采信"

INVENTORS = {x["inventor"]: x for x in
             (XIE_JIANZHENG, XIE_ZHANGLONG, CHEN_HAIJIA, LIN_MEIHUA, LI_SHANGFA, WANG_MENG)}

# ---- 授权发明专利（4 家均为 0，2026-11-09 实测）
RD = {
    "天津德吾安环境修复有限公司": 0,
    "天津市嵘通视觉文化发展有限公司": 0,
    "中科东南（陕西）环保科技有限公司": 0,
    "埃里克阀业（江苏）有限公司": 0,
}

# ---- 荣誉：德吾安、嵘通均无专精特新（嵘通有"创新型中小企业"，不算）
HONOR = {
    "天津德吾安环境修复有限公司": [{"name": "科技型中小企业", "level": "国家级", "year": "2026"},
                                    {"name": "高新技术企业", "level": "国家级", "year": "2024"}],
    "天津市嵘通视觉文化发展有限公司": [{"name": "科技型中小企业", "level": "国家级", "year": "2026"},
                                       {"name": "创新型中小企业", "level": "省级", "year": "2025"},
                                       {"name": "高新技术企业", "level": "国家级", "year": "2023"}],
}

# ---- 代理师：汝涛 = 2025-11(4)+2025-12(9)+2026(22) = 35；刘亮 上界 97；童淑霞 0
AGENT = {
    "汝涛": {"value": 35, "detail": [{"ym": "2025-11", "n": 4}, {"ym": "2025-12", "n": 9},
                                     {"ym": "2026-01~10", "n": 22}]},
    "刘亮": {"value": 97, "detail": [{"ym": "上界", "n": 97}], "method": "upper_bound",
             "note": "2025 全年 95 + 2026 全年 2 的上界，滚动必然 <300"},
    "童淑霞": {"value": 0, "detail": []},
}


def seed():
    # 回测批的案子都没有申请日 → 锚点=查询日，窗口 2025-11-09 ~ 2026-11-09
    for name, data in INVENTORS.items():
        F.cache_set("inventor", f"{name}|{data['window_start']}|{data['window_end']}", data)
    for app, n in RD.items():
        F.cache_set("company_rd", f"{app}|{TODAY.replace('-', '')}",
                    {"applicant": app, "granted_invention": n, "has": n >= 1,
                     "base_date": TODAY.replace("-", ""), "_fetched": TODAY})
    for app, hs in HONOR.items():
        F.cache_set("company_honor", app,
                    {"applicant": app, "honors": hs, "_ok": True,
                     "zhuanjingtexin": [h for h in hs if "专精特新" in h["name"]],
                     "_fetched": TODAY})
    for ag, d in AGENT.items():
        F.cache_set("agent_month", f"{ag}|{TODAY}", {"agent": ag, "status": "ok", "value": d["value"],
                                        "detail": d["detail"], "window": ["2025-11-10", TODAY],
                                        "as_of": TODAY, "_fetched": TODAY,
                                        "_source": "香橼树月度统计（回测快照）",
                                        **({"method": d["method"], "note": d["note"]} if d.get("method") else {})})
    print("seeded:", len(INVENTORS), "inventors /", len(RD), "rd /", len(HONOR), "honor /", len(AGENT), "agents")


if __name__ == "__main__":
    seed()

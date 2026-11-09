# -*- coding: utf-8 -*-
"""探测：非「天津/石家庄/保定」城市，单次调用能否产出合法、无重复的行程 JSON"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import requests

# 读 .env
for line in open(".env", "r", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

BASE = os.environ["LLM_API_BASE"].rstrip("/")
KEY = os.environ["LLM_API_KEY"]
MODEL = os.environ.get("LLM_MODEL", "glm-4-flash")

SYS = json.load(open("agent_config.json", "r", encoding="utf-8"))["system_prompt"]

city = sys.argv[1] if len(sys.argv) > 1 else "北京"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 3

user = (
    f"请为我规划行程。目标城市：{city}；出行天数：{days} 天；"
    f"总预算：1200 元；偏好标签：古迹、美食。"
    f"要求：①每个景点之间必须给出交通方式、乘车站数/线路、耗时与费用，写入 transport 字段；"
    f"②为每个景点选择一个配图类别，写入 img 字段，取值只能是 "
    f"heritage / landmark / museum / nature / food / night / street 之一；"
    f"③每天安排 3—4 个景点／美食条目（不少于 3 个），desc 不超过 22 字、guide 不超过 26 字、"
    f"xhs 不超过 16 字，只输出 JSON，不要任何解释性文字。"
    f"④【景点绝不重复】同一个景点、同一家店、同一个地点全程只出现一次；"
    f"每天的主题片区必须错开。"
)

t0 = time.time()
r = requests.post(
    BASE + "/chat/completions",
    headers={"Authorization": f"Bearer {KEY}"},
    json={"model": MODEL, "messages": [{"role": "system", "content": SYS},
                                      {"role": "user", "content": user}],
          "temperature": 0.4, "max_tokens": 4096},
    timeout=180,
)
dt = time.time() - t0
txt = r.json()["choices"][0]["message"]["content"]
print(f"[{city} {days}天] 耗时 {dt:.1f}s  长度 {len(txt)}", flush=True)

m = txt[txt.find("{"): txt.rfind("}") + 1]
try:
    plan = json.loads(m)
except Exception as e:
    print("JSON 解析失败：", e)
    print(txt[:600])
    sys.exit(1)

names, dups = [], []
for d in plan.get("days") or []:
    dn = [it.get("name") for it in (d.get("items") or []) if it.get("type") != "交通"]
    print(f"  D{d.get('day')}: {dn}", flush=True)
    for n in dn:
        if n in names:
            dups.append(n)
        names.append(n)
print(f"天数={len(plan.get('days') or [])} 条目={len(names)} 重复={sorted(set(dups))} "
      f"总花费={plan.get('total_cost')}", flush=True)

json.dump(plan, open(f"output/_probe_{city}.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

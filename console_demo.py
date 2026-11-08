# -*- coding: utf-8 -*-
"""极简版：控制台文字交互（教程 2.3 方案1，用于快速验证 Agent 链路）
用法：python console_demo.py
"""
import json
import os

from main import call_agent, build_user_input, parse_plan, CONFIG, SYSTEM_PROMPT

def main():
    print("=" * 50)
    print("城墙漫游记 · 城市微旅行规划 Agent（控制台版）")
    print("=" * 50)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    city = input("目标城市（如 天津）：").strip() or "天津"
    days = input("出行天数（如 2）：").strip() or "2"
    budget = input("总预算（如 800）：").strip() or "800"
    prefs = input("偏好，逗号分隔（如 古迹,美食）：").strip() or "古迹,美食"

    messages.append({"role": "user", "content": build_user_input(city, int(days), float(budget), prefs.split(","))})
    print("\n🤖 Agent 规划中，请稍候…\n")
    reply = call_agent(messages)
    messages.append({"role": "assistant", "content": reply})

    plan = parse_plan(reply)
    if plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(reply)

    while True:
        d = input("\n【扰动测试】输入突发事件（如 古文化街今日闭园，明天有大雨），直接回车退出：").strip()
        if not d:
            break
        messages.append({"role": "user", "content": f"【突发扰动】{d}。请自动重规划并说明调整理由，仍按 JSON 输出。"})
        print("\n🤖 Agent 重新规划中…\n")
        reply = call_agent(messages)
        messages.append({"role": "assistant", "content": reply})
        plan = parse_plan(reply)
        print(json.dumps(plan, ensure_ascii=False, indent=2) if plan else reply)

if __name__ == "__main__":
    main()

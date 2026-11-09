# -*- coding: utf-8 -*-
"""用本机 Edge 打开本地 Streamlit 应用，操作并截图（供 PPT / 答辩素材使用）

用法：python demo/capture_ui.py [输出目录]
"""
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "demo/screenshots"
URL = "http://127.0.0.1:8000"

EXTRA_TAGS = ["打卡热点", "自然风光", "文博展馆", "休闲慢逛（citywalk）"]


def slider_value(page, idx: int) -> str:
    return page.locator('[data-testid="stSliderThumbValue"]').nth(idx).inner_text().strip()


def set_slider(page, idx: int, ratio: float, key: str = None):
    """点击轨道后按键精确设置（End=最大，方向键微调）"""
    box = page.locator('[data-testid="stSlider"]').nth(idx).bounding_box()
    page.mouse.click(box["x"] + box["width"] * ratio, box["y"] + box["height"] * 0.78)
    page.wait_for_timeout(500)
    if key:
        for _ in range(len(key)):
            page.keyboard.press(key)
            page.wait_for_timeout(250)
    page.wait_for_timeout(500)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("text=智漫游", timeout=60000)
    page.wait_for_timeout(2500)

    # 1) 偏好标签下拉展开（展示 7 类标签）
    page.locator('[data-testid="stMultiSelect"]').click()
    page.wait_for_timeout(1200)
    page.screenshot(path=f"{OUT}/ui_01_偏好标签7类.png")
    print("已截：偏好标签下拉")

    # 2) 选中四个新标签
    for tag in EXTRA_TAGS:
        try:
            if not page.get_by_role("option", name=tag, exact=True).is_visible():
                page.locator('[data-testid="stMultiSelect"]').click()
                page.wait_for_timeout(700)
            page.get_by_role("option", name=tag, exact=True).click(timeout=6000)
            page.wait_for_timeout(500)
        except Exception as e:
            print("选择标签失败:", tag, e)
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)

    # 3) 天数拉到 5 天、预算拉到约 1500
    set_slider(page, 0, 0.5, key="End")
    set_slider(page, 1, 0.28)
    page.wait_for_timeout(800)
    print("天数=", slider_value(page, 0), "预算=", slider_value(page, 1))
    assert slider_value(page, 0) == "5", "天数未设置为 5"
    page.screenshot(path=f"{OUT}/ui_02_参数设置_5天7偏好.png")

    # 4) 生成行程
    page.get_by_role("button", name="生成行程规划").click()
    print("已点击生成，等待 Agent 返回…")
    page.wait_for_selector("text=行程方案", timeout=240000)
    page.wait_for_timeout(8000)
    page.screenshot(path=f"{OUT}/ui_03_5天行程_配图与交通.png")
    page.screenshot(path=f"{OUT}/ui_04_整页长图.png", full_page=True)
    print("截图完成")

    browser.close()

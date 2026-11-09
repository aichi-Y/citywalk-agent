# -*- coding: utf-8 -*-
"""
从 Wikimedia Commons 抓取景点的真实照片（自由许可），下载到 static/images/spots/
- 只取 jpg/png、宽度 >= 900 的实拍图，排除地图/logo/示意图
- 记录每张图的来源页与许可，写入 static/images/photos_credit.json（页面可标注来源）
用法：python tools/fetch_real_photos.py
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "static", "images", "spots")
CREDIT = os.path.join(HERE, "static", "images", "photos_credit.json")
UA = "ZhiManyou-EduDemo/1.0 (student competition project; contact: aichi-Y)"

# key: 文件名；q: Commons 搜索词（英文/中文都可）
SPOTS = {
    # ---------- 天津 ----------
    "天津之眼": ("tianjin_eye", "Tianjin Eye ferris wheel"),
    "古文化街": ("guculture_street", "Ancient Culture Street Tianjin"),
    "五大道": ("wudadao", "Wudadao Tianjin historic buildings"),
    "瓷房子": ("porcelain_house", "Porcelain House Tianjin"),
    "西开教堂": ("xikai_church", "Xikai Cathedral Tianjin"),
    "望海楼教堂": ("wanghailou", "Wanghailou Church Tianjin"),
    "意式风情区": ("italian_town", "Italian Style Town Tianjin"),
    "静园": ("jingyuan", "Jingyuan Garden Tianjin Puyi"),
    "梁启超饮冰室": ("liang_qichao", "Liang Qichao residence Tianjin"),
    "广东会馆": ("guangdong_hall", "Guangdong Guild Hall Tianjin"),
    "天津博物馆": ("tianjin_museum", "Tianjin Museum building"),
    "文庙": ("tianjin_confucius", "Tianjin Confucian Temple"),
    "鼓楼": ("tianjin_drumtower", "Tianjin Drum Tower"),
    "南市食品街": ("nanshi_food", "Nanshi Food Street Tianjin"),
    "劝业场": ("quanyechang", "Quanyechang Tianjin"),
    "海河夜景": ("haihe_night", "Haihe River Tianjin night"),
    # 天津美食
    "狗不理包子": ("goubuli", "Goubuli baozi"),
    "耳朵眼炸糕": ("erduoyan", "fried rice cake Chinese dessert"),
    "煎饼果子": ("jianbing", "Jianbing guozi"),
    "锅巴菜": ("guobacai", "Guobacai Tianjin"),
    "相声": ("xiangsheng", "Xiangsheng crosstalk performance"),
    # ---------- 石家庄 ----------
    "河北博物院": ("hebei_museum", "Hebei Museum Shijiazhuang"),
    "正定古城墙": ("zhengding_wall", "Zhengding city wall"),
    "隆兴寺": ("longxing_temple", "Longxing Temple Zhengding"),
    "阳和楼": ("yanghe_tower", "Zhengding ancient city gate tower"),
    "赵州桥": ("zhaozhou_bridge", "Zhaozhou Bridge Anji"),
    "西柏坡": ("xibaipo", "Xibaipo memorial"),
    # ---------- 保定 ----------
    "直隶总督署": ("zhili_office", "Zhili Governor Office Baoding"),
    "古莲花池": ("lotus_pond", "Lotus Pond Baoding garden"),
    "大慈阁": ("daci_pavilion", "Daci Pavilion Baoding"),
    "保定军校纪念馆": ("baoding_academy", "Baoding Military Academy"),
    "保定市博物馆": ("baoding_museum", "Baoding Museum"),
    "竞秀公园": ("jingxiu_park", "Chinese park lake pavilion Hebei"),
    "西大街": ("baoding_street", "Baoding old street"),
    # 河北美食
    "驴肉火烧": ("lvrou_huoshao", "donkey burger Hebei"),
    "牛肉板面": ("banmian", "Chinese beef noodle soup bowl"),
    "包子": ("baozi", "baozi steamed bun"),
}

BAD_WORDS = (
    "map", "logo", "plan", "diagram", "banner", "poster", "stamp", "coin",
    "seal", "chart", "svg", "icon", "sign", "ticket", "banknote", "flag",
)


def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def search_image(query):
    """返回 (thumb_url, page_url, license, artist) 或 None"""
    try:
        data = api({
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6, "gsrlimit": 12,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 720,
        })
    except Exception as e:
        print("   API 失败", e)
        return None
    pages = (data.get("query") or {}).get("pages") or {}
    cands = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        title = p.get("title", "")
        low = title.lower()
        if any(w in low for w in BAD_WORDS):
            continue
        if ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if (ii.get("width") or 0) < 900:
            continue
        # 越靠前的搜索结果越相关
        idx = p.get("index", 99)
        # 横图优先
        ratio = (ii.get("width") or 1) / max(1, (ii.get("height") or 1))
        score = idx * 2 + (0 if ratio >= 1.2 else 3)
        meta = ii.get("extmetadata") or {}
        cands.append((score, ii.get("thumburl") or ii.get("url"),
                      ii.get("descriptionurl"), (meta.get("LicenseShortName") or {}).get("value", ""),
                      re.sub("<[^>]+>", "", (meta.get("Artist") or {}).get("value", ""))[:60]))
    cands.sort(key=lambda x: x[0])
    return cands[0][1:] if cands else None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    credit = {}
    if os.path.exists(CREDIT):
        credit = json.load(open(CREDIT, encoding="utf-8"))
    ok = fail = 0
    for name, (key, query) in SPOTS.items():
        dest = os.path.join(OUT_DIR, f"{key}.jpg")
        if os.path.exists(dest) and os.path.getsize(dest) > 8000:
            print(f"跳过（已存在） {name}")
            ok += 1
            continue
        print(f"抓取 {name} <- {query}")
        got = search_image(query)
        if not got:
            print("   未找到合适图片")
            fail += 1
            continue
        thumb, page, lic, artist = got
        try:
            req = urllib.request.Request(thumb, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                blob = r.read()
            if len(blob) < 8000:
                print("   图片过小，跳过")
                fail += 1
                continue
            open(dest, "wb").write(blob)
            credit[key] = {"name": name, "source": page, "license": lic, "artist": artist}
            print(f"   ok {len(blob)//1024}KB lic={lic}")
            ok += 1
        except Exception as e:
            print("   下载失败", e)
            fail += 1
        time.sleep(0.6)
    json.dump(credit, open(CREDIT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n完成：成功 {ok}，失败 {fail}，输出目录 {OUT_DIR}")


if __name__ == "__main__":
    main()

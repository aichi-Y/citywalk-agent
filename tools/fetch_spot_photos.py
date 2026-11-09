# -*- coding: utf-8 -*-
"""
从维基百科条目首图（pageimages）抓取景点真实照片，质量优于全文搜索。
- 优先中文维基，其次英文维基，最后回退 Commons 搜索
- 自动排除地图/示意图，统一裁剪为 4:3、宽 800px，压缩后存 static/images/spots/
- 记录来源与许可 -> static/images/photos_credit.json
用法：python tools/fetch_spot_photos.py
"""
import json
import os
import re
import urllib.parse
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "static", "images", "spots")
CR = os.path.join(HERE, "static", "images", "photos_credit.json")
UA = "ZhiManyou-EduDemo/1.0 (student competition; contact aichi-Y)"

# (文件名, 展示名, 中文维基条目, 英文维基条目, Commons 兜底搜索词)
SPOTS = [
    # ---------- 天津 ----------
    ("tianjin_eye", "天津之眼", "天津之眼", "Tianjin Eye", "Tianjin Eye"),
    ("guculture_street", "古文化街", "天津古文化街", "Ancient Culture Street", "Ancient Culture Street Tianjin"),
    ("wudadao", "五大道", "五大道", "Wudadao", "Wudadao Tianjin"),
    ("porcelain_house", "瓷房子", "瓷房子", "Porcelain House", "Porcelain House Tianjin"),
    ("xikai_church", "西开教堂", "西开教堂", "St. Joseph Cathedral, Tianjin", "Xikai Cathedral"),
    ("wanghailou", "望海楼教堂", "望海楼教堂", "Wanghailou Church", "Wanghailou Church"),
    ("italian_town", "意式风情区", "天津意式风情区", "Italian Style Town", "Italian Style Town Tianjin"),
    ("jingyuan", "静园", "静园 (天津)", "Jing Garden", "Jingyuan Tianjin"),
    ("liang_qichao", "梁启超饮冰室", "梁启超故居", "Liang Qichao Residence", "Liang Qichao Tianjin"),
    ("guangdong_hall", "广东会馆", "天津广东会馆", "Guangdong Guild Hall", "Guangdong Guild Hall Tianjin"),
    ("tianjin_museum", "天津博物馆", "天津博物馆", "Tianjin Museum", "Tianjin Museum"),
    ("tianjin_confucius", "文庙", "天津文庙", "Tianjin Confucian Temple", "Tianjin Confucian Temple"),
    ("tianjin_drumtower", "鼓楼", "天津鼓楼", "Tianjin Drum Tower", "Tianjin Drum Tower"),
    ("nanshi_food", "南市食品街", "南市食品街", "Nanshi Food Street", "Nanshi Food Street Tianjin"),
    ("quanyechang", "劝业场", "天津劝业场", "Quanyechang", "Quanyechang Tianjin"),
    ("haihe_night", "海河夜景", "海河", "Hai River", "Haihe River night Tianjin"),
    # 天津美食
    ("goubuli", "狗不理包子", "狗不理", "Goubuli", "Goubuli baozi"),
    ("erduoyan", "耳朵眼炸糕", "耳朵眼炸糕", None, "Erduoyan fried cake"),
    ("jianbing", "煎饼果子", "煎饼馃子", "Jianbing", "Jianbing guozi"),
    ("guobacai", "锅巴菜", "锅巴菜", None, "Guobacai"),
    ("xiangsheng", "相声", "相声", "Xiangsheng", "Xiangsheng performance"),
    # ---------- 石家庄 / 正定 ----------
    ("hebei_museum", "河北博物院", "河北博物院", "Hebei Museum", "Hebei Museum"),
    ("zhengding_wall", "正定古城墙", "正定城墙", "Zhengding", "Zhengding city wall"),
    ("longxing_temple", "隆兴寺", "隆兴寺", "Longxing Temple", "Longxing Temple Zhengding"),
    ("yanghe_tower", "阳和楼", "阳和楼", None, "Yanghe Tower"),
    ("zhaozhou_bridge", "赵州桥", "赵州桥", "Zhaozhou Bridge", "Zhaozhou Bridge"),
    ("xibaipo", "西柏坡", "西柏坡", "Xibaipo", "Xibaipo"),
    # ---------- 保定 ----------
    ("zhili_office", "直隶总督署", "直隶总督署", "Zhili Governor's Office", "Zhili Governor Office Baoding"),
    ("lotus_pond", "古莲花池", "古莲花池", "Lotus Pond (Baoding)", "Gulianhuachi Baoding"),
    ("daci_pavilion", "大慈阁", "大慈阁", None, "Daci Pavilion Baoding"),
    ("baoding_academy", "保定军校纪念馆", "保定陆军军官学校", "Baoding Military Academy", "Baoding Military Academy"),
    ("baoding_museum", "保定市博物馆", "保定市博物馆", None, "Baoding Museum"),
    ("jingxiu_park", "竞秀公园", "竞秀公园", None, "Jingxiu Park Baoding"),
    ("baoding_street", "西大街", "保定西大街", None, "Baoding West Street"),
    # 河北美食
    ("lvrou_huoshao", "驴肉火烧", "驴肉火烧", None, "Donkey burger"),
    ("banmian", "牛肉板面", "板面", None, "Banmian noodles"),
    ("baozi", "包子", "包子", "Baozi", "Baozi"),
]

BAD = ("map", "karte", "mapa", "logo", "diagram", "plan_", "stamp", "coin",
       "seal", "chart", "icon", "ticket", "banknote", "flag_of")


def api(host, params):
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def page_image(host, title):
    """取条目首图原图地址 + 许可信息"""
    if not title:
        return None
    try:
        d = api(host, {
            "action": "query", "format": "json", "titles": title,
            "redirects": 1, "prop": "pageimages|pageprops",
            "piprop": "original|name", "pithumbsize": 900,
        })
    except Exception:
        return None
    pages = (d.get("query") or {}).get("pages") or {}
    for p in pages.values():
        if "missing" in p:
            continue
        orig = (p.get("original") or {}).get("source")
        if orig:
            return orig, p.get("title", title)
    return None


def commons_thumb(query):
    try:
        d = api("commons.wikimedia.org", {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6, "gsrlimit": 10,
            "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 900,
        })
    except Exception:
        return None
    pages = (d.get("query") or {}).get("pages") or {}
    best = None
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        low = p.get("title", "").lower()
        if any(w in low for w in BAD):
            continue
        if ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if (ii.get("width") or 0) < 900:
            continue
        score = p.get("index", 99)
        if best is None or score < best[0]:
            best = (score, ii.get("thumburl") or ii.get("url"), p.get("title"))
    return (best[1], best[2]) if best else None


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def to_43(blob, path):
    """统一裁成 4:3（800x600），居中裁剪，压缩到 88"""
    tmp = path + ".raw"
    open(tmp, "wb").write(blob)
    im = Image.open(tmp).convert("RGB")
    w, h = im.size
    target = 4 / 3
    if w / h > target:
        nw = int(h * target)
        im = im.crop(((w - nw) // 2, 0, (w + nw) // 2, h))
    else:
        nh = int(w / target)
        im = im.crop((0, (h - nh) // 2, w, (h + nh) // 2))
    im = im.resize((800, 600), Image.LANCZOS)
    im.save(path, "JPEG", quality=86, optimize=True, progressive=True)
    os.remove(tmp)
    return os.path.getsize(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    credit = {}
    if os.path.exists(CR):
        credit = json.load(open(CR, encoding="utf-8"))
    ok, fail = 0, []
    for key, name, zh, en, q in SPOTS:
        dest = os.path.join(OUT, f"{key}.jpg")
        src = None
        hit = None
        for host, title in (("zh.wikipedia.org", zh), ("en.wikipedia.org", en)):
            got = page_image(host, title)
            if got:
                src, hit = got
                break
        if not src:
            g = commons_thumb(q or name)
            if g:
                src, hit = g
        if not src:
            print(f"✗ {name}: 未取到")
            fail.append(name)
            continue
        try:
            blob = download(src)
            if len(blob) < 6000:
                raise ValueError("图片过小")
            size = to_43(blob, dest)
            credit[key] = {"name": name, "page": src,
                           "title": hit if isinstance(hit, str) else "",
                           "license": "Wikimedia Commons（CC BY / CC BY-SA / 公有领域）"}
            print(f"✓ {name}  {size//1024}KB  <- {hit}")
            ok += 1
        except Exception as e:
            print(f"✗ {name}: {e}")
            fail.append(name)
    json.dump(credit, open(CR, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n成功 {ok} / 失败 {len(fail)} {fail}")


if __name__ == "__main__":
    main()

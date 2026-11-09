# -*- coding: utf-8 -*-
"""
按 Wikimedia 哈希路径规则，从 upload.wikimedia.org 直链下载已登记的历史照片。
（commons.wikimedia.org 的 API 域名在当前网络不可用，但图片 CDN 可用；
 直链规则：/wikipedia/commons/<md5[0]>/<md5[0:2]>/<文件名> ）
下载后统一裁 4:3 -> 800x600 存 static/images/spots/
"""
import time
import hashlib
import json
import os
import urllib.parse
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "static", "images", "spots")
CR = os.path.join(HERE, "static", "images", "photos_credit.json")
UA = "Mozilla/5.0 (compatible; ZhiManyou/1.0)"


def filename_of(url):
    u = urllib.parse.unquote(url)
    return u.split("/wiki/File:")[-1].replace(" ", "_")


def direct_urls(name):
    """维基当前只允许 120/500/960/1920 四档缩略图，原图路径会 429"""
    h = hashlib.md5(name.encode("utf-8")).hexdigest()
    q = urllib.parse.quote(name)
    thumb = f"https://upload.wikimedia.org/wikipedia/commons/thumb/{h[0]}/{h[:2]}/{q}"
    return [f"{thumb}/960px-{q}", f"{thumb}/500px-{q}", f"{thumb}/1920px-{q}", f"{thumb}/120px-{q}"]


def to_43(blob, path):
    tmp = path + ".raw"
    open(tmp, "wb").write(blob)
    im = Image.open(tmp).convert("RGB")
    w, h = im.size
    t = 4 / 3
    if w / h > t:
        nw = int(h * t)
        im = im.crop(((w - nw) // 2, 0, (w + nw) // 2, h))
    else:
        nh = int(w / t)
        im = im.crop((0, (h - nh) // 2, w, (h + nh) // 2))
    im.resize((800, 600), Image.LANCZOS).save(path, "JPEG", quality=86, optimize=True, progressive=True)
    os.remove(tmp)
    return os.path.getsize(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    credit = json.load(open(CR, encoding="utf-8"))
    ok, fail = 0, []
    for key, meta in credit.items():
        src = meta.get("source", "")
        if os.path.exists(os.path.join(OUT, f"{key}.jpg")):
            continue
        if "/wiki/File:" not in src:
            fail.append((key, "no filename"))
            continue
        name = filename_of(src.split("?")[0])
        dest = os.path.join(OUT, f"{key}.jpg")
        got = None
        for attempt in range(2):
            for u in direct_urls(name):
                try:
                    req = urllib.request.Request(u, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=45) as r:
                        blob = r.read()
                    if len(blob) > 6000:
                        got = blob
                        break
                except Exception:
                    continue
            if got:
                break
            time.sleep(6)
        if not got:
            fail.append((key, name))
            continue
        try:
            n = to_43(got, dest)
            meta["file"] = name
            print(f"✓ {key} {n//1024}KB")
            ok += 1
        except Exception as e:
            fail.append((key, str(e)[:40]))
    json.dump(credit, open(CR, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n成功 {ok}，失败 {len(fail)}：{fail}")


if __name__ == "__main__":
    main()

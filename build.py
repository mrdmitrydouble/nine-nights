#!/usr/bin/env python3
"""Собирает быстрый сайт из самодостаточной страницы (всё встроено base64).

    python3 -I build.py source.html .

Что делает:
  * фото из data:-URI -> img/<хеш>.jpg, у копий (data-same) сразу настоящий src;
  * фото дней грузятся лениво (по мере прокрутки), фото шапки — сразу;
  * тайлы 3D-карты -> tiles/sat.jpg и tiles/dem/z/x/y.png вместо 2,8 МБ JSON;
  * карта стартует сразу, а не после загрузки всех фото (событие load);
  * MapLibre и шрифты лежат на самом сайте (vendor/, fonts/), без cdnjs и Google;
  * повтор загрузки фото при сбое сети.
Каждая замена проверяется: если исходник изменился и замена не нашлась — ошибка.
"""
import base64, hashlib, json, os, re, shutil, sys, urllib.request

SRC, OUT = sys.argv[1], sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))
t = open(SRC, encoding="utf-8").read()
before = len(t.encode())


def once(old, new, what):
    global t
    n = t.count(old)
    if n != 1:
        sys.exit(f"СТОП: «{what}» найдено {n} раз вместо 1 — исходник изменился, проверьте build.py")
    t = t.replace(old, new)


def write(rel, data):
    p = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


for d in ("img", "tiles"):
    shutil.rmtree(os.path.join(OUT, d), ignore_errors=True)

# ---- 1. Фото ----
id2src = {}


def img_tag(m):
    tag = m.group(0)
    dm = re.search(r'src="data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)"', tag)
    if dm:
        raw = base64.b64decode(dm.group(2))
        ext = {"jpeg": "jpg"}.get(dm.group(1), dm.group(1))
        rel = f"img/{hashlib.sha1(raw).hexdigest()[:10]}.{ext}"
        write(rel, raw)
        tag = tag[:dm.start()] + f'src="{rel}"' + tag[dm.end():]
        im = re.search(r'\bid="([^"]+)"', tag)
        if im:
            id2src[im.group(1)] = rel
    return tag


t = re.sub(r"<img\b[^>]*>", img_tag, t)


def same_tag(m):
    tag = m.group(0)
    sid = re.search(r'data-same="([^"]+)"', tag).group(1)
    if sid not in id2src:
        sys.exit(f"СТОП: data-same={sid} без исходного фото")
    return re.sub(r'src="data:image/gif;base64,[^"]*"', f'src="{id2src[sid]}"', tag)


t = re.sub(r"<img\b[^>]*data-same=[^>]*>", same_tag, t)
left = re.findall(r'<img\b[^>]*src="data:', t)
if left:
    sys.exit(f"СТОП: осталось {len(left)} встроенных фото")

first_hero = [True]


def loading(m):
    tag = m.group(0)
    if "loading=" in tag:
        return tag
    if 'class="pimg"' in tag:  # коллаж в шапке — виден сразу
        extra = ' decoding="async"'
        if first_hero[0]:
            extra += ' fetchpriority="high"'
            first_hero[0] = False
        return tag[:-1] + extra + ">"
    return tag[:-1] + ' loading="lazy" decoding="async">'


t = re.sub(r"<img\b[^>]*>", loading, t)

# ---- 2. Тайлы карты ----
m = re.search(r'<script type="application/json" id="dmap-tiles">(.*?)</script>\n?', t, re.S)
if not m:
    sys.exit("СТОП: нет dmap-tiles")
TL = json.loads(m.group(1))
write("tiles/sat.jpg", base64.b64decode(TL["sat"]))
for k, v in TL["dem"].items():
    write(f"tiles/dem/{k}.png", base64.b64decode(v))
t = t[:m.start()] + t[m.end():]

once("""    var TL=JSON.parse(document.getElementById('dmap-tiles').textContent);
    try{
      maplibregl.addProtocol('emb',function(p){
        var k=p.url.replace('emb://','');
        if(k==='sat') return Promise.resolve({data:b64buf(TL.sat)});
        var t=TL.dem[k.replace('dem/','')];
        if(!t) return Promise.reject(new Error('no tile '+k));
        return Promise.resolve({data:b64buf(t)});
      });
    }catch(e){ return fallback('protocol'); }
""", """    var TB=new URL('tiles/',location.href).href;
""", "addProtocol emb")
t = t.replace("tiles:['emb://dem/{z}/{x}/{y}']", "tiles:[TB+'dem/{z}/{x}/{y}.png']")
once("url:'emb://sat'", "url:TB+'sat.jpg'", "sat url")
if "emb://" in t:
    sys.exit("СТОП: остались ссылки emb://")

# карта — сразу, не дожидаясь всех фото
once("if(document.readyState==='complete') init(); else window.addEventListener('load',init);",
     "if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init); else init();",
     "init on load")

# ---- 3. MapLibre и шрифты со своего сайта ----
LIB = "https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/5.24.0/maplibre-gl.js"
once(f'<script src="{LIB}"></script>', '<script src="vendor/maplibre-gl-5.24.0.js"></script>', "maplibre cdn")
vend = os.path.join(OUT, "vendor/maplibre-gl-5.24.0.js")
if not os.path.exists(vend):
    write("vendor/maplibre-gl-5.24.0.js", urllib.request.urlopen(LIB, timeout=60).read())

gf = re.search(r'<link rel="stylesheet" href="(https://fonts\.googleapis\.com/css2\?[^"]+)">', t)
if not gf:
    sys.exit("СТОП: нет ссылки на Google Fonts")
css_path = os.path.join(OUT, "fonts/fonts.css")
if not os.path.exists(css_path):
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    url = gf.group(1).replace("&amp;", "&")
    css = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua}), timeout=60).read().decode()

    def font(mm):
        u = mm.group(1)
        name = hashlib.sha1(u.encode()).hexdigest()[:12] + ".woff2"
        write("fonts/" + name, urllib.request.urlopen(u, timeout=60).read())
        return f"url({name})"

    css = re.sub(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", font, css)
    write("fonts/fonts.css", css.encode())
t = t.replace(gf.group(0), '<link rel="stylesheet" href="fonts/fonts.css">')
t = re.sub(r'<link rel="preconnect" href="https://fonts\.(googleapis|gstatic)\.com"( crossorigin)?>', "", t)

# ---- 4. Надёжность: повтор загрузки фото при сбое ----
once("</head>", """<script>
addEventListener('error',function(e){var i=e.target;if(!i||i.tagName!=='IMG')return;var n=+(i.dataset.retry||0);if(n>=3)return;i.dataset.retry=n+1;var s=i.getAttribute('src').split('?')[0];setTimeout(function(){i.src=s+'?r='+(n+1);},800*(n+1));},true);
</script>
</head>""", "head end")

# ---- 5. Фото каруселей: грузим всю листалку, когда она подъезжает к экрану,
#         а после загрузки страницы спокойно докачиваем остальное в фоне ----
once("</body>", """<script>
(function(){
  function eager(root){ [].slice.call(root.querySelectorAll('img[loading="lazy"]')).forEach(function(i){ i.loading='eager'; }); }
  var ws=[].slice.call(document.querySelectorAll('.carwrap'));
  if('IntersectionObserver' in window){
    var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ eager(e.target); io.unobserve(e.target); } }); },{rootMargin:'1200px 0px'});
    ws.forEach(function(w){ io.observe(w); });
  } else { ws.forEach(eager); }
  function rest(){ var i=0; (function step(){ if(i>=ws.length) return eager(document); eager(ws[i++]); setTimeout(step,400); })(); }
  if(document.readyState==='complete') setTimeout(rest,1500); else addEventListener('load',function(){ setTimeout(rest,1500); });
})();
</script>
</body>""", "body end")

write("index.html", t.encode())
after = os.path.getsize(os.path.join(OUT, "index.html"))
print(f"index.html: {before/1e6:.2f} МБ -> {after/1e3:.0f} КБ; фото: {len(set(id2src.values()))} файлов")

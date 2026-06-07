import os, time, sqlite3, requests, threading, json, random, queue
from datetime import datetime
from flask import Flask, jsonify, render_template_string, Response, stream_with_context, request

app = Flask(__name__)
DB = os.path.join("/tmp", "vinted_feed.db")

CATEGORIES = [
    {"nom": "Vetements femme",   "id": 1904},
    {"nom": "Vetements homme",   "id": 4},
    {"nom": "Chaussures femme",  "id": 16},
    {"nom": "Chaussures homme",  "id": 812},
    {"nom": "Sacs",              "id": 3},
    {"nom": "Accessoires",       "id": 2},
    {"nom": "Sport",             "id": 77},
    {"nom": "Electronique",      "id": 2225},
    {"nom": "Maison",            "id": 1560},
    {"nom": "Jeux video",        "id": 1194},
    {"nom": "Livres",            "id": 1193},
    {"nom": "Enfants",           "id": 1},
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]

feed_queue = queue.Queue(maxsize=1000)
_ids_vus = set()
_ids_lock = threading.Lock()

def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS articles (
        id TEXT PRIMARY KEY, titre TEXT, marque TEXT, prix REAL,
        categorie TEXT, taille TEXT, nb_favoris INTEGER,
        url TEXT, photo_url TEXT, date_scraping TEXT)""")
    c.commit(); c.close()

def load_proxies():
    raw = os.environ.get("PROXY_LIST", "")
    proxies = []
    for p in raw.split(","):
        parts = p.strip().split(":")
        if len(parts) == 4:
            host, port, user, pwd = parts
            proxies.append("http://" + user + ":" + pwd + "@" + host + ":" + port)
    return proxies

PROXIES = load_proxies()
_proxy_idx = 0
_proxy_lock = threading.Lock()

def get_proxy():
    global _proxy_idx
    if not PROXIES: return None
    with _proxy_lock:
        p = PROXIES[_proxy_idx % len(PROXIES)]
        _proxy_idx += 1
    return {"http": p, "https": p}

def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Origin": "https://www.vinted.fr",
        "Referer": "https://www.vinted.fr/",
    })
    proxy = get_proxy()
    if proxy: s.proxies.update(proxy)
    try: s.get("https://www.vinted.fr", timeout=8)
    except: pass
    return s

def sauvegarder(art):
    try:
        c = sqlite3.connect(DB)
        c.execute("""INSERT OR IGNORE INTO articles
            (id,titre,marque,prix,categorie,taille,nb_favoris,url,photo_url,date_scraping)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (art["id"],art["titre"],art["marque"],art["prix"],art["categorie"],
             art["taille"],art["nb_favoris"],art["url"],art["photo_url"],art["date_scraping"]))
        c.commit(); c.close()
    except: pass

def scanner_cat(cat):
    ids_cat = set()
    time.sleep(random.uniform(0.3, 2))
    session = get_session()
    while True:
        try:
            params = {"catalog_ids": cat["id"], "page": 1, "per_page": 96, "order": "newest_first"}
            r = session.get("https://www.vinted.fr/api/v2/catalog/items", params=params, timeout=10)
            if r.status_code == 429:
                time.sleep(random.uniform(15, 30))
                session = get_session()
                continue
            items = r.json().get("items", [])
            for item in items:
                iid = str(item.get("id", ""))
                if iid in ids_cat: continue
                ids_cat.add(iid)
                with _ids_lock:
                    if iid in _ids_vus: continue
                    _ids_vus.add(iid)
                marque = item.get("brand_title", "") or item.get("brand", "")
                prix = float(item.get("price", {}).get("amount", 0) if isinstance(item.get("price"), dict) else item.get("price", 0))
                photos = item.get("photos", [])
                photo_url = photos[0].get("url", "") if photos else ""
                art = {
                    "id": iid, "titre": item.get("title", ""),
                    "marque": marque, "prix": prix,
                    "categorie": cat["nom"], "taille": item.get("size_title", ""),
                    "nb_favoris": item.get("favourite_count", 0),
                    "url": "https://www.vinted.fr/items/" + iid,
                    "photo_url": photo_url,
                    "date_scraping": datetime.now().isoformat(),
                }
                sauvegarder(art)
                try: feed_queue.put_nowait(art)
                except queue.Full:
                    try: feed_queue.get_nowait()
                    except: pass
                    try: feed_queue.put_nowait(art)
                    except: pass
        except:
            session = get_session()
            time.sleep(random.uniform(3, 8))
        time.sleep(random.uniform(1.5, 3))

def start_scanner():
    time.sleep(2)
    for cat in CATEGORIES:
        threading.Thread(target=scanner_cat, args=(cat,), daemon=True).start()
        time.sleep(0.3)

@app.route("/")
def index():
    return HTML

HTML = """
<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>VintedFeed</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--acc:#b8ff00;--dark:#0a0a0a;--surface:#1a1a1a;--text:#fff;--t2:rgba(255,255,255,.55)}
body{background:var(--dark);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100dvh;overflow:hidden;display:flex;flex-direction:column}
.header{padding:10px 14px 6px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.logo{font-size:17px;font-weight:800}
.logo em{color:var(--acc);font-style:normal}
.live-pill{display:flex;align-items:center;gap:5px;background:rgba(255,255,255,.08);padding:4px 10px;border-radius:20px;font-size:11px;color:var(--t2)}
.ldot{width:6px;height:6px;border-radius:50%;background:#444}
.ldot.on{background:var(--acc);animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.filters{padding:0 12px 8px;display:flex;gap:6px;overflow-x:auto;flex-shrink:0;scrollbar-width:none;position:relative}
.filters::-webkit-scrollbar{display:none}
.fpill{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:var(--t2);padding:6px 14px;border-radius:20px;font-size:12px;white-space:nowrap;cursor:pointer;flex-shrink:0;transition:all .15s;user-select:none}
.fpill.active{background:var(--acc);border-color:var(--acc);color:#000;font-weight:600}
.fpill.reset{color:rgba(255,80,80,.7)}
.dd{position:fixed;background:var(--surface);border:1px solid rgba(255,255,255,.12);border-radius:14px;min-width:220px;max-width:calc(100vw - 24px);max-height:55vh;overflow-y:auto;z-index:9999;display:none;box-shadow:0 12px 40px rgba(0,0,0,.9)}
.dd.open{display:block}
.dd::-webkit-scrollbar{width:3px}
.dd::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:2px}
.ditem{display:flex;align-items:center;gap:10px;padding:10px 16px;font-size:13px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.04)}
.ditem:last-child{border-bottom:none}
.ditem:hover{background:rgba(255,255,255,.06)}
.ditem.sel{color:var(--acc);background:rgba(184,255,0,.06)}
.dcheck{width:16px;height:16px;border-radius:4px;border:1.5px solid rgba(255,255,255,.3);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:10px}
.ditem.sel .dcheck{background:var(--acc);border-color:var(--acc);color:#000}
.dsep{font-size:10px;color:var(--t2);padding:10px 16px 4px;text-transform:uppercase;letter-spacing:.06em;background:rgba(255,255,255,.02)}
.dsearch{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.06)}
.dsearch input{width:100%;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#fff;padding:7px 12px;border-radius:8px;font-size:13px;outline:none}
.dsearch input:focus{border-color:var(--acc)}
.prix-row{display:flex;gap:8px;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,.06)}
.prix-inp{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#fff;padding:7px 10px;border-radius:8px;font-size:13px;width:90px;outline:none}
.prix-inp:focus{border-color:var(--acc)}
.dpreset{padding:9px 16px;font-size:13px;cursor:pointer;color:var(--t2);border-bottom:1px solid rgba(255,255,255,.04)}
.dpreset:hover{color:#fff;background:rgba(255,255,255,.04)}
.feed{flex:1;overflow-y:scroll;scroll-snap-type:y mandatory;scrollbar-width:none;display:flex;flex-direction:column;align-items:center;gap:10px;padding:6px 0}
.feed::-webkit-scrollbar{display:none}
.card{width:min(390px,96vw);height:82dvh;scroll-snap-align:start;flex-shrink:0;position:relative;display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden;border-radius:16px;background:#111}
.cbg{position:absolute;inset:0}
.cimg{width:100%;height:100%;object-fit:cover;display:block}
.cgrad{position:absolute;inset:0;background:linear-gradient(to bottom,rgba(0,0,0,.15) 0%,transparent 35%,transparent 55%,rgba(0,0,0,.88) 100%)}
.ctop{position:absolute;top:12px;left:12px;right:12px;display:flex;justify-content:space-between;align-items:flex-start;z-index:2}
.bnew{background:var(--acc);color:#000;font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px}
.bts{background:rgba(0,0,0,.55);color:rgba(255,255,255,.7);font-size:10px;padding:3px 9px;border-radius:20px;backdrop-filter:blur(6px)}
.cactions{position:absolute;right:12px;bottom:90px;display:flex;flex-direction:column;gap:8px;z-index:2;align-items:center}
.bbuy{width:50px;height:50px;border-radius:50%;background:var(--acc);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 4px 16px rgba(184,255,0,.5);text-decoration:none;transition:transform .15s}
.bbuy:hover{transform:scale(1.08)}
.bsee{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;font-size:14px;text-decoration:none;color:#fff}
.cinfo{position:relative;z-index:2;padding:12px 14px 16px}
.cprow{display:flex;align-items:baseline;gap:8px;margin-bottom:8px}
.cprix{font-size:26px;font-weight:800}
.cfrais{font-size:11px;color:var(--t2)}
.cpills{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px}
.cpill{background:rgba(255,255,255,.12);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.18);padding:3px 10px;border-radius:20px;font-size:11px;font-weight:500}
.ctitre{font-size:12px;color:rgba(255,255,255,.75);line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.waiting{width:min(390px,96vw);height:82dvh;scroll-snap-align:start;flex-shrink:0;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:var(--t2);background:#111}
.wicon{font-size:44px}
.bottombar{flex-shrink:0;background:rgba(10,10,10,.96);backdrop-filter:blur(12px);border-top:1px solid rgba(255,255,255,.06);padding:10px 20px 14px;display:flex;align-items:center;justify-content:space-around}
.bbbtn{display:flex;flex-direction:column;align-items:center;gap:3px;background:none;border:none;color:var(--t2);font-size:10px;cursor:pointer;padding:0}
.bbbtn.active{color:var(--acc)}
.bbico{font-size:20px}
</style>
</head><body>

<div class="header">
  <div class="logo">Vinted<em>Feed</em></div>
  <div class="live-pill"><div class="ldot" id="ldot"></div><span id="ltxt">Connexion...</span></div>
</div>

<div class="filters">
  <div class="fpill" id="pillCat" onclick="openDD('ddCat',this)">Catégorie <span id="lblCat"></span></div>
  <div class="fpill" id="pillMarque" onclick="openDD('ddMarque',this)">Marque <span id="lblMarque"></span></div>
  <div class="fpill" id="pillTaille" onclick="openDD('ddTaille',this)">Taille <span id="lblTaille"></span></div>
  <div class="fpill" id="pillPrix" onclick="openDD('ddPrix',this)">Prix <span id="lblPrix"></span></div>
  <div class="fpill reset" onclick="resetAll()">Reset</div>
</div>

<!-- DROPDOWNS -->
<div class="dd" id="ddCat"></div>

<div class="dd" id="ddMarque">
  <div class="dsearch"><input type="text" id="searchB" placeholder="Rechercher une marque..." oninput="filterB()" onclick="event.stopPropagation()"></div>
  <div id="blist"></div>
</div>

<div class="dd" id="ddTaille">
  <div class="dsep">Vêtements</div>
  <div class="ditem" onclick="toggleT('XS',this)"><div class="dcheck"></div>XS</div>
  <div class="ditem" onclick="toggleT('S',this)"><div class="dcheck"></div>S</div>
  <div class="ditem" onclick="toggleT('M',this)"><div class="dcheck"></div>M</div>
  <div class="ditem" onclick="toggleT('L',this)"><div class="dcheck"></div>L</div>
  <div class="ditem" onclick="toggleT('XL',this)"><div class="dcheck"></div>XL</div>
  <div class="ditem" onclick="toggleT('XXL',this)"><div class="dcheck"></div>XXL</div>
  <div class="dsep">Chaussures</div>
  <div class="ditem" onclick="toggleT('36',this)"><div class="dcheck"></div>36</div>
  <div class="ditem" onclick="toggleT('37',this)"><div class="dcheck"></div>37</div>
  <div class="ditem" onclick="toggleT('38',this)"><div class="dcheck"></div>38</div>
  <div class="ditem" onclick="toggleT('39',this)"><div class="dcheck"></div>39</div>
  <div class="ditem" onclick="toggleT('40',this)"><div class="dcheck"></div>40</div>
  <div class="ditem" onclick="toggleT('41',this)"><div class="dcheck"></div>41</div>
  <div class="ditem" onclick="toggleT('42',this)"><div class="dcheck"></div>42</div>
  <div class="ditem" onclick="toggleT('43',this)"><div class="dcheck"></div>43</div>
  <div class="ditem" onclick="toggleT('44',this)"><div class="dcheck"></div>44</div>
  <div class="ditem" onclick="toggleT('45',this)"><div class="dcheck"></div>45</div>
</div>

<div class="dd" id="ddPrix">
  <div class="prix-row">
    <input class="prix-inp" type="number" id="pMin" placeholder="Min €" oninput="applyPrix()">
    <input class="prix-inp" type="number" id="pMax" placeholder="Max €" oninput="applyPrix()">
  </div>
  <div class="dpreset" onclick="setPreset(0,10)">Moins de 10€</div>
  <div class="dpreset" onclick="setPreset(0,20)">Moins de 20€</div>
  <div class="dpreset" onclick="setPreset(0,50)">Moins de 50€</div>
  <div class="dpreset" onclick="setPreset(10,30)">10€ — 30€</div>
  <div class="dpreset" onclick="setPreset(20,50)">20€ — 50€</div>
  <div class="dpreset" onclick="setPreset(50,999)">Plus de 50€</div>
</div>

<div class="feed" id="feed">
  <div class="waiting"><div class="wicon">⚡</div><div>Connexion...</div></div>
</div>

<div class="bottombar">
  <button class="bbbtn active"><span class="bbico">⚡</span><span>Feed</span></button>
  <button class="bbbtn" onclick="scrollTop()"><span class="bbico">🔝</span><span>Top</span></button>
  <button class="bbbtn" id="notifBtn" onclick="toggleNotif()"><span class="bbico">🔔</span><span>Alertes</span></button>
</div>

<script>
var CATS = ["Vetements femme","Vetements homme","Chaussures femme","Chaussures homme","Sacs","Accessoires","Sport","Electronique","Maison","Jeux video","Livres","Enfants"];
var CATS_LABEL = {"Vetements femme":"Vêtements femme","Vetements homme":"Vêtements homme","Chaussures femme":"Chaussures femme","Chaussures homme":"Chaussures homme","Sacs":"Sacs","Accessoires":"Accessoires","Sport":"Sport","Electronique":"Électronique","Maison":"Maison","Jeux video":"Jeux vidéo","Livres":"Livres","Enfants":"Enfants"};
var BRANDS = ["Nike","Adidas","Jordan","New Balance","Puma","Converse","Vans","Reebok","Asics","Saucony","Supreme","Carhartt","Stone Island","Palace","Stussy","Off-White","Kith","Zara","H&M","Mango","Pull&Bear","Bershka","Uniqlo","Cos","Ralph Lauren","Tommy Hilfiger","Lacoste","Levi's","Calvin Klein","Guess","The North Face","Patagonia","Arc'teryx","Salomon","Columbia","Napapijri","Canada Goose","Louis Vuitton","Gucci","Prada","Balenciaga","Dior","Chanel","Hermes","Burberry","Apple","Samsung","Sony","Nintendo","Bose","JBL","Under Armour","Lululemon","Decathlon","Vintage","Y2K"];

var selCats = [], selBrands = [], selTailles = [], pMin = 0, pMax = 0;
var renderedIds = new Set();
var notifOn = false;
var sse = null;
var currentDD = null;

// Init catégories
(function(){
  var dd = document.getElementById('ddCat');
  dd.innerHTML = CATS.map(function(c){
    return '<div class="ditem" data-val="'+c+'" onclick="toggleC(\\''+c+'\\',this)"><div class="dcheck"></div>'+CATS_LABEL[c]+'</div>';
  }).join('');
})();

// Init marques
var allBrands = BRANDS.slice();
renderBrands(allBrands);

function renderBrands(list){
  var el = document.getElementById('blist');
  el.innerHTML = list.map(function(b){
    var sel = selBrands.indexOf(b) !== -1;
    return '<div class="ditem'+(sel?' sel':'')+'" data-val="'+b+'" onclick="toggleB(\\''+b.replace(/'/g,"\\\\'")+'\\',this)"><div class="dcheck">'+(sel?'✓':'')+'</div>'+b+'</div>';
  }).join('');
}

function filterB(){
  var q = document.getElementById('searchB').value.toLowerCase();
  renderBrands(q ? allBrands.filter(function(b){return b.toLowerCase().indexOf(q)!==-1;}) : allBrands);
}

// Dropdowns
function openDD(id, pill){
  event.stopPropagation();
  var dd = document.getElementById(id);
  var wasOpen = dd.classList.contains('open');
  closeAllDD();
  if(!wasOpen){
    var rect = pill.getBoundingClientRect();
    dd.style.top = (rect.bottom + 6) + 'px';
    dd.style.left = Math.max(8, rect.left) + 'px';
    dd.classList.add('open');
    currentDD = dd;
  }
}
function closeAllDD(){
  document.querySelectorAll('.dd').forEach(function(d){d.classList.remove('open');});
  currentDD = null;
}
document.addEventListener('click', closeAllDD);

// Toggle catégorie
function toggleC(val, el){
  event.stopPropagation();
  var idx = selCats.indexOf(val);
  if(idx===-1){ selCats.push(val); el.classList.add('sel'); el.querySelector('.dcheck').textContent='✓'; }
  else { selCats.splice(idx,1); el.classList.remove('sel'); el.querySelector('.dcheck').textContent=''; }
  updLabel('lblCat', selCats.length, 'pillCat');
  reload();
}

// Toggle marque
function toggleB(val, el){
  event.stopPropagation();
  var idx = selBrands.indexOf(val);
  if(idx===-1){ selBrands.push(val); el.classList.add('sel'); el.querySelector('.dcheck').textContent='✓'; }
  else { selBrands.splice(idx,1); el.classList.remove('sel'); el.querySelector('.dcheck').textContent=''; }
  updLabel('lblMarque', selBrands.length, 'pillMarque');
  reload();
}

// Toggle taille
function toggleT(val, el){
  event.stopPropagation();
  var idx = selTailles.indexOf(val);
  if(idx===-1){ selTailles.push(val); el.classList.add('sel'); el.querySelector('.dcheck').textContent='✓'; }
  else { selTailles.splice(idx,1); el.classList.remove('sel'); el.querySelector('.dcheck').textContent=''; }
  updLabel('lblTaille', selTailles.length, 'pillTaille');
  reload();
}

// Prix
function applyPrix(){
  pMin = parseFloat(document.getElementById('pMin').value)||0;
  pMax = parseFloat(document.getElementById('pMax').value)||0;
  var lbl = (pMin||pMax) ? (pMin?pMin+'€':'')+(pMax?'-'+pMax+'€':'') : '';
  document.getElementById('lblPrix').textContent = lbl ? '('+lbl+')' : '';
  document.getElementById('pillPrix').classList.toggle('active', !!(pMin||pMax));
  reload();
}
function setPreset(mn,mx){
  event.stopPropagation();
  document.getElementById('pMin').value = mn||'';
  document.getElementById('pMax').value = mx===999?'':mx;
  pMin=mn; pMax=mx;
  var lbl = mn+'€-'+mx+'€';
  document.getElementById('lblPrix').textContent='('+lbl+')';
  document.getElementById('pillPrix').classList.add('active');
  reload();
}

function updLabel(lblId, count, pillId){
  document.getElementById(lblId).textContent = count ? '('+count+')' : '';
  document.getElementById(pillId).classList.toggle('active', count>0);
}

function resetAll(){
  selCats=[]; selBrands=[]; selTailles=[]; pMin=0; pMax=0;
  document.getElementById('pMin').value='';
  document.getElementById('pMax').value='';
  document.getElementById('searchB').value='';
  document.querySelectorAll('.ditem.sel').forEach(function(el){
    el.classList.remove('sel'); el.querySelector('.dcheck').textContent='';
  });
  ['lblCat','lblMarque','lblTaille','lblPrix'].forEach(function(id){document.getElementById(id).textContent='';});
  ['pillCat','pillMarque','pillTaille','pillPrix'].forEach(function(id){document.getElementById(id).classList.remove('active');});
  renderBrands(allBrands);
  reload();
}

// Feed
function buildUrl(){
  var url = '/api/feed?limit=80';
  selCats.forEach(function(c){url+='&cat='+encodeURIComponent(c);});
  selBrands.forEach(function(b){url+='&marque='+encodeURIComponent(b);});
  selTailles.forEach(function(t){url+='&taille='+encodeURIComponent(t);});
  if(pMin) url+='&pmin='+pMin;
  if(pMax) url+='&pmax='+pMax;
  return url;
}

function makeCard(o, isNew){
  var frais = Math.round(o.prix*1.05*100)/100;
  var img = o.photo_url ? '<img class="cimg" src="'+o.photo_url+'" loading="lazy">' : '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:64px">🏷️</div>';
  var pills = '';
  if(o.marque) pills += '<span class="cpill">'+o.marque+'</span>';
  if(o.taille) pills += '<span class="cpill">'+o.taille+'</span>';
  if(o.categorie) pills += '<span class="cpill">'+(CATS_LABEL[o.categorie]||o.categorie)+'</span>';
  var d = document.createElement('div');
  d.className = 'card';
  d.innerHTML =
    '<div class="cbg">'+img+'<div class="cgrad"></div></div>'+
    '<div class="ctop">'+
      (isNew?'<span class="bnew">NOUVEAU</span>':'<span></span>')+
      '<span class="bts">'+o.prix+'€</span>'+
    '</div>'+
    '<div class="cactions">'+
      '<a href="'+o.url+'" target="_blank" class="bbuy">⚡</a>'+
      '<a href="'+o.url+'" target="_blank" class="bsee">↗</a>'+
    '</div>'+
    '<div class="cinfo">'+
      '<div class="cprow"><span class="cprix">'+o.prix+'€</span><span class="cfrais">'+frais+'€ frais incl.</span></div>'+
      '<div class="cpills">'+pills+'</div>'+
      '<div class="ctitre">'+(o.titre||'')+'</div>'+
    '</div>';
  return d;
}

function addCard(o, isNew){
  if(renderedIds.has(o.id)) return;
  renderedIds.add(o.id);
  var feed = document.getElementById('feed');
  var w = feed.querySelector('.waiting');
  if(w) feed.innerHTML='';
  var card = makeCard(o, isNew);
  if(isNew) feed.prepend(card); else feed.appendChild(card);
  while(feed.children.length>200) feed.removeChild(feed.lastChild);
  if(notifOn && isNew && Notification.permission==='granted'){
    var n = new Notification((o.marque||o.categorie||'Vinted')+' — '+o.prix+'€',{body:o.titre,tag:o.id});
    n.onclick=function(){window.open(o.url,'_blank');n.close();};
    setTimeout(function(){n.close();},6000);
  }
}

function reload(){
  renderedIds.clear();
  var feed = document.getElementById('feed');
  feed.innerHTML='<div class="waiting"><div class="wicon">🔍</div><div>Filtrage...</div></div>';
  setTimeout(loadInitial, 50);
}

async function loadInitial(){
  try{
    var d = await fetch(buildUrl()).then(function(r){return r.json();});
    var arts = (d.articles||[]).reverse();
    if(!arts.length){
      document.getElementById('feed').innerHTML='<div class="waiting"><div class="wicon">🕵️</div><div>Aucun article pour ces filtres</div></div>';
      return;
    }
    arts.forEach(function(o){addCard(o,false);});
  }catch(e){}
}

function connectSSE(){
  if(sse) sse.close();
  sse = new EventSource('/api/stream');
  sse.onopen=function(){
    document.getElementById('ldot').classList.add('on');
    document.getElementById('ltxt').textContent='En direct';
  };
  sse.onmessage=function(e){
    if(!e.data||e.data==='{}') return;
    try{
      var o=JSON.parse(e.data);
      if(!o.id) return;
      // Vérifier filtres côté client
      if(selCats.length && selCats.indexOf(o.categorie)===-1) return;
      if(selBrands.length && !selBrands.some(function(b){return (o.marque||'').toLowerCase().indexOf(b.toLowerCase())!==-1;})) return;
      if(selTailles.length && !selTailles.some(function(t){return (o.taille||'').indexOf(t)!==-1;})) return;
      if(pMin && o.prix<pMin) return;
      if(pMax && o.prix>pMax) return;
      addCard(o,true);
    }catch(err){}
  };
  sse.onerror=function(){
    document.getElementById('ldot').classList.remove('on');
    document.getElementById('ltxt').textContent='Reconnexion...';
    sse.close();
    setTimeout(connectSSE,3000);
  };
}

function toggleNotif(){
  if(!('Notification' in window)) return;
  Notification.requestPermission().then(function(p){
    if(p==='granted'){
      notifOn=!notifOn;
      document.getElementById('notifBtn').classList.toggle('active',notifOn);
    }
  });
}

function scrollTop(){document.getElementById('feed').scrollTo({top:0,behavior:'smooth'});}

loadInitial();
connectSSE();
</script>
</body></html>

"""

@app.route("/api/stream")
def api_stream():
    def generate():
        yield "data: {}" + chr(10) + chr(10)
        while True:
            try:
                art = feed_queue.get(timeout=20)
                yield "data: " + json.dumps(art, ensure_ascii=False) + chr(10) + chr(10)
            except queue.Empty:
                yield ": ping" + chr(10) + chr(10)
    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/feed")
def api_feed():
    try:
        limit = int(request.args.get("limit", 50))
        cats = request.args.getlist("cat")
        marques = request.args.getlist("marque")
        tailles = request.args.getlist("taille")
        prix_min = request.args.get("pmin", "")
        prix_max = request.args.get("pmax", "")

        where = "WHERE 1=1"
        params = []
        if cats:
            where += " AND categorie IN (" + ",".join("?"*len(cats)) + ")"
            params.extend(cats)
        if marques:
            where += " AND (" + " OR ".join(["LOWER(marque) LIKE LOWER(?)"]*len(marques)) + ")"
            params.extend(["%" + m + "%" for m in marques])
        if tailles:
            where += " AND (" + " OR ".join(["taille LIKE ?"]*len(tailles)) + ")"
            params.extend(["%" + t + "%" for t in tailles])
        if prix_min:
            where += " AND prix >= ?"; params.append(float(prix_min))
        if prix_max:
            where += " AND prix <= ?"; params.append(float(prix_max))

        c = sqlite3.connect(DB)
        rows = c.execute("SELECT id,titre,marque,prix,categorie,taille,nb_favoris,url,photo_url FROM articles " + where + " ORDER BY date_scraping DESC LIMIT ?", params + [limit]).fetchall()
        c.close()
        arts = [{"id":r[0],"titre":r[1],"marque":r[2],"prix":r[3],"categorie":r[4],"taille":r[5],"nb_favoris":r[6],"url":r[7],"photo_url":r[8]} for r in rows]
        return jsonify({"articles": arts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

init_db()
threading.Thread(target=start_scanner, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

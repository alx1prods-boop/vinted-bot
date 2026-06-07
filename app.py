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
            h, port, u, pw = parts
            proxies.append("http://" + u + ":" + pw + "@" + h + ":" + port)
    return proxies

PROXIES = load_proxies()
_pi = 0
_pl = threading.Lock()

def get_proxy():
    global _pi
    if not PROXIES: return None
    with _pl:
        p = PROXIES[_pi % len(PROXIES)]; _pi += 1
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
    px = get_proxy()
    if px: s.proxies.update(px)
    try: s.get("https://www.vinted.fr", timeout=8)
    except: pass
    return s

def sauvegarder(art):
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT OR IGNORE INTO articles (id,titre,marque,prix,categorie,taille,nb_favoris,url,photo_url,date_scraping) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (art["id"],art["titre"],art["marque"],art["prix"],art["categorie"],art["taille"],art["nb_favoris"],art["url"],art["photo_url"],art["date_scraping"]))
        c.commit(); c.close()
    except: pass

def scanner_cat(cat):
    ids_cat = set()
    time.sleep(random.uniform(0.3, 2))
    session = get_session()
    while True:
        try:
            r = session.get("https://www.vinted.fr/api/v2/catalog/items",
                params={"catalog_ids": cat["id"], "page": 1, "per_page": 96, "order": "newest_first"}, timeout=10)
            if r.status_code == 429:
                time.sleep(random.uniform(15, 30)); session = get_session(); continue
            for item in r.json().get("items", []):
                iid = str(item.get("id", ""))
                if iid in ids_cat: continue
                ids_cat.add(iid)
                with _ids_lock:
                    if iid in _ids_vus: continue
                    _ids_vus.add(iid)
                marque = item.get("brand_title", "") or item.get("brand", "")
                prix = float(item.get("price", {}).get("amount", 0) if isinstance(item.get("price"), dict) else item.get("price", 0))
                photos = item.get("photos", [])
                art = {
                    "id": iid, "titre": item.get("title", ""), "marque": marque, "prix": prix,
                    "categorie": cat["nom"], "taille": item.get("size_title", ""),
                    "nb_favoris": item.get("favourite_count", 0),
                    "url": "https://www.vinted.fr/items/" + iid,
                    "photo_url": photos[0].get("url", "") if photos else "",
                    "date_scraping": datetime.now().isoformat(),
                }
                sauvegarder(art)
                try: feed_queue.put_nowait(art)
                except queue.Full:
                    try: feed_queue.get_nowait()
                    except: pass
                    try: feed_queue.put_nowait(art)
                    except: pass
        except: session = get_session(); time.sleep(random.uniform(3, 8))
        time.sleep(random.uniform(1.5, 3))

def start_scanner():
    time.sleep(2)
    for cat in CATEGORIES:
        threading.Thread(target=scanner_cat, args=(cat,), daemon=True).start()
        time.sleep(0.3)

@app.route("/")
def index():
    return HTML

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
        limit = int(request.args.get("limit", 80))
        cats = request.args.getlist("cat")
        marques = request.args.getlist("marque")
        tailles = request.args.getlist("taille")
        pmin = request.args.get("pmin", "")
        pmax = request.args.get("pmax", "")
        where = "WHERE 1=1"; params = []
        if cats: where += " AND categorie IN (" + ",".join(["?"]*len(cats)) + ")"; params.extend(cats)
        if marques: where += " AND (" + " OR ".join(["LOWER(marque) LIKE LOWER(?)"]*len(marques)) + ")"; params.extend(["%" + m + "%" for m in marques])
        if tailles: where += " AND (" + " OR ".join(["taille LIKE ?"]*len(tailles)) + ")"; params.extend(["%" + t + "%" for t in tailles])
        if pmin: where += " AND prix >= ?"; params.append(float(pmin))
        if pmax: where += " AND prix <= ?"; params.append(float(pmax))
        c = sqlite3.connect(DB)
        rows = c.execute("SELECT id,titre,marque,prix,categorie,taille,nb_favoris,url,photo_url FROM articles " + where + " ORDER BY date_scraping DESC LIMIT ?", params + [limit]).fetchall()
        c.close()
        return jsonify({"articles": [{"id":r[0],"titre":r[1],"marque":r[2],"prix":r[3],"categorie":r[4],"taille":r[5],"nb_favoris":r[6],"url":r[7],"photo_url":r[8]} for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

init_db()
threading.Thread(target=start_scanner, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

HTML = r"""<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>VintedFeed</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--acc:#b8ff00;--bg:#0a0a0a;--s:#1a1a1a;--b:rgba(255,255,255,.1);--t:#fff;--t2:rgba(255,255,255,.55)}
body{background:var(--bg);color:var(--t);font-family:-apple-system,BlinkMacSystemFont,sans-serif;height:100dvh;overflow:hidden;display:flex;flex-direction:column}
.hdr{padding:10px 14px 6px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.logo{font-size:17px;font-weight:800}.logo em{color:var(--acc);font-style:normal}
.lpill{display:flex;align-items:center;gap:5px;background:rgba(255,255,255,.08);padding:4px 10px;border-radius:20px;font-size:11px;color:var(--t2)}
.ldot{width:6px;height:6px;border-radius:50%;background:#444}
.ldot.on{background:var(--acc);animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.fbar{padding:0 12px 8px;display:flex;gap:6px;overflow-x:auto;flex-shrink:0;scrollbar-width:none}
.fbar::-webkit-scrollbar{display:none}
.fp{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:var(--t2);padding:6px 14px;border-radius:20px;font-size:12px;white-space:nowrap;cursor:pointer;flex-shrink:0;transition:all .15s;user-select:none}
.fp.on{background:var(--acc);border-color:var(--acc);color:#000;font-weight:600}
.fp.rst{color:rgba(255,80,80,.7)}
.dd{position:fixed;background:#181818;border:1px solid rgba(255,255,255,.12);border-radius:14px;min-width:220px;max-width:calc(100vw - 24px);max-height:55vh;overflow-y:auto;z-index:9999;display:none;box-shadow:0 12px 40px rgba(0,0,0,.95)}
.dd.open{display:block}
.dd::-webkit-scrollbar{width:3px}
.dd::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:2px}
.di{display:flex;align-items:center;gap:10px;padding:10px 16px;font-size:13px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.04)}
.di:hover{background:rgba(255,255,255,.05)}
.di.on{color:var(--acc);background:rgba(184,255,0,.05)}
.dck{width:16px;height:16px;border-radius:4px;border:1.5px solid rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:10px}
.di.on .dck{background:var(--acc);border-color:var(--acc);color:#000}
.dsep{font-size:10px;color:var(--t2);padding:9px 16px 3px;text-transform:uppercase;letter-spacing:.06em;background:rgba(255,255,255,.02)}
.dsi{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.06)}
.dsi input{width:100%;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#fff;padding:7px 12px;border-radius:8px;font-size:13px;outline:none}
.dsi input:focus{border-color:var(--acc)}
.prow{display:flex;gap:8px;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,.06)}
.pinp{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#fff;padding:7px 10px;border-radius:8px;font-size:13px;width:90px;outline:none}
.pinp:focus{border-color:var(--acc)}
.dpre{padding:9px 16px;font-size:13px;cursor:pointer;color:var(--t2);border-bottom:1px solid rgba(255,255,255,.04)}
.dpre:hover{color:#fff;background:rgba(255,255,255,.04)}
.feed{flex:1;overflow-y:scroll;scrollbar-width:none;display:flex;flex-direction:column;align-items:center;gap:10px;padding:6px 0}
.feed::-webkit-scrollbar{display:none}
.card{width:min(390px,96vw);height:82dvh;flex-shrink:0;position:relative;display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden;border-radius:16px;background:#111;animation:pop .3s ease}
@keyframes pop{from{opacity:0;transform:translateY(-16px)}to{opacity:1;transform:translateY(0)}}
.cbg{position:absolute;inset:0}
.ci{width:100%;height:100%;object-fit:cover;display:block}
.cg{position:absolute;inset:0;background:linear-gradient(to bottom,rgba(0,0,0,.1) 0%,transparent 35%,transparent 55%,rgba(0,0,0,.88) 100%)}
.ctop{position:absolute;top:12px;left:12px;right:12px;display:flex;justify-content:space-between;align-items:center;z-index:2}
.bnew{background:var(--acc);color:#000;font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px}
.bdel{background:rgba(0,0,0,.55);border:none;color:rgba(255,255,255,.8);width:30px;height:30px;border-radius:50%;cursor:pointer;font-size:15px;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(6px)}
.cact{position:absolute;right:12px;bottom:90px;display:flex;flex-direction:column;gap:8px;z-index:2;align-items:center}
.bbuy{width:50px;height:50px;border-radius:50%;background:var(--acc);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 4px 16px rgba(184,255,0,.5);text-decoration:none;transition:transform .15s}
.bbuy:hover{transform:scale(1.08)}
.bsee{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;font-size:14px;text-decoration:none;color:#fff}
.cinf{position:relative;z-index:2;padding:12px 14px 16px}
.cpr{display:flex;align-items:baseline;gap:8px;margin-bottom:8px}
.cpx{font-size:26px;font-weight:800}
.cfs{font-size:11px;color:var(--t2)}
.cpls{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px}
.cpl{background:rgba(255,255,255,.12);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.18);padding:3px 10px;border-radius:20px;font-size:11px;font-weight:500}
.cti{font-size:12px;color:rgba(255,255,255,.75);line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.wt{width:min(390px,96vw);height:82dvh;flex-shrink:0;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:var(--t2);background:#111}
.bbar{flex-shrink:0;background:rgba(10,10,10,.96);backdrop-filter:blur(12px);border-top:1px solid rgba(255,255,255,.06);padding:10px 20px 14px;display:flex;align-items:center;justify-content:space-around}
.bb{display:flex;flex-direction:column;align-items:center;gap:3px;background:none;border:none;color:var(--t2);font-size:10px;cursor:pointer}
.bb.on{color:var(--acc)}
.bbi{font-size:20px}
</style></head><body>

<div class="hdr">
  <div class="logo">Vinted<em>Feed</em></div>
  <div class="lpill"><div class="ldot" id="ldot"></div><span id="ltxt">Connexion...</span></div>
</div>

<div class="fbar">
  <div class="fp" id="fpCat" onclick="openDD(event,'ddCat',this)">Catégorie <span id="lCat"></span></div>
  <div class="fp" id="fpMar" onclick="openDD(event,'ddMarque',this)">Marque <span id="lMar"></span></div>
  <div class="fp" id="fpTai" onclick="openDD(event,'ddTaille',this)">Taille <span id="lTai"></span></div>
  <div class="fp" id="fpPrix" onclick="openDD(event,'ddPrix',this)">Prix <span id="lPrix"></span></div>
  <div class="fp rst" onclick="resetAll()">Reset</div>
</div>

<div class="dd" id="ddCat"></div>
<div class="dd" id="ddMarque">
  <div class="dsi"><input type="text" id="sb" placeholder="Rechercher..." oninput="filterB()" onclick="event.stopPropagation()"></div>
  <div id="bl"></div>
</div>
<div class="dd" id="ddTaille">
  <div class="dsep">Vetements</div>
  <div class="di" onclick="togT('XS',this)"><div class="dck"></div>XS</div>
  <div class="di" onclick="togT('S',this)"><div class="dck"></div>S</div>
  <div class="di" onclick="togT('M',this)"><div class="dck"></div>M</div>
  <div class="di" onclick="togT('L',this)"><div class="dck"></div>L</div>
  <div class="di" onclick="togT('XL',this)"><div class="dck"></div>XL</div>
  <div class="di" onclick="togT('XXL',this)"><div class="dck"></div>XXL</div>
  <div class="dsep">Chaussures</div>
  <div class="di" onclick="togT('36',this)"><div class="dck"></div>36</div>
  <div class="di" onclick="togT('37',this)"><div class="dck"></div>37</div>
  <div class="di" onclick="togT('38',this)"><div class="dck"></div>38</div>
  <div class="di" onclick="togT('39',this)"><div class="dck"></div>39</div>
  <div class="di" onclick="togT('40',this)"><div class="dck"></div>40</div>
  <div class="di" onclick="togT('41',this)"><div class="dck"></div>41</div>
  <div class="di" onclick="togT('42',this)"><div class="dck"></div>42</div>
  <div class="di" onclick="togT('43',this)"><div class="dck"></div>43</div>
  <div class="di" onclick="togT('44',this)"><div class="dck"></div>44</div>
  <div class="di" onclick="togT('45',this)"><div class="dck"></div>45</div>
</div>
<div class="dd" id="ddPrix">
  <div class="prow">
    <input class="pinp" type="number" id="pMn" placeholder="Min" oninput="apPrix()">
    <input class="pinp" type="number" id="pMx" placeholder="Max" oninput="apPrix()">
  </div>
  <div class="dpre" onclick="setPre(0,10)">Moins de 10€</div>
  <div class="dpre" onclick="setPre(0,20)">Moins de 20€</div>
  <div class="dpre" onclick="setPre(0,50)">Moins de 50€</div>
  <div class="dpre" onclick="setPre(10,30)">10 - 30€</div>
  <div class="dpre" onclick="setPre(20,50)">20 - 50€</div>
  <div class="dpre" onclick="setPre(50,999)">Plus de 50€</div>
</div>

<div class="feed" id="feed">
  <div class="wt"><div style="font-size:44px">⚡</div><div>Connexion...</div></div>
</div>

<div class="bbar">
  <button class="bb on"><span class="bbi">⚡</span><span>Feed</span></button>
  <button class="bb" onclick="goTop()"><span class="bbi">🔝</span><span>Top</span></button>
  <button class="bb" id="nBtn" onclick="togNotif()"><span class="bbi">🔔</span><span>Alertes</span></button>
</div>

<script>
var CATS = ["Vetements femme","Vetements homme","Chaussures femme","Chaussures homme","Sacs","Accessoires","Sport","Electronique","Maison","Jeux video","Livres","Enfants"];
var CLBL = {"Vetements femme":"Vetements femme","Vetements homme":"Vetements homme","Chaussures femme":"Chaussures femme","Chaussures homme":"Chaussures homme","Sacs":"Sacs","Accessoires":"Accessoires","Sport":"Sport","Electronique":"Electronique","Maison":"Maison","Jeux video":"Jeux video","Livres":"Livres","Enfants":"Enfants"};
var BRANDS = ["Nike","Adidas","Jordan","New Balance","Puma","Converse","Vans","Reebok","Asics","Supreme","Carhartt","Stone Island","Palace","Stussy","Off-White","Zara","H&M","Mango","Pull&Bear","Bershka","Uniqlo","Ralph Lauren","Tommy Hilfiger","Lacoste","Levi's","Calvin Klein","Guess","The North Face","Patagonia","Arc'teryx","Salomon","Columbia","Napapijri","Louis Vuitton","Gucci","Prada","Balenciaga","Dior","Chanel","Apple","Samsung","Sony","Nintendo","Bose","Under Armour","Lululemon","Decathlon"];

var sC=[], sB=[], sT=[], pMn=0, pMx=0;
var rIds = new Set();
var notifOn = false;
var sse = null;
var loading = false;

// Init cats
(function(){
  var dd = document.getElementById('ddCat');
  dd.innerHTML = CATS.map(function(c){
    return '<div class="di" onclick="togC(\''+c+'\',this)"><div class="dck"></div>'+CLBL[c]+'</div>';
  }).join('');
})();

var allB = BRANDS.slice();
function renderB(list){
  document.getElementById('bl').innerHTML = list.map(function(b){
    var s = sB.indexOf(b) !== -1;
    return '<div class="di'+(s?' on':'')+'" onclick="togB(\''+b.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\',this)"><div class="dck">'+(s?'v':'')+'</div>'+b+'</div>';
  }).join('');
}
renderB(allB);

function filterB(){
  var q = document.getElementById('sb').value.toLowerCase();
  renderB(q ? allB.filter(function(b){return b.toLowerCase().indexOf(q)>-1;}) : allB);
}

function openDD(e, id, pill){
  e.stopPropagation();
  var dd = document.getElementById(id);
  var was = dd.classList.contains('open');
  document.querySelectorAll('.dd').forEach(function(d){d.classList.remove('open');});
  if(!was){
    var r = pill.getBoundingClientRect();
    dd.style.top = (r.bottom+6)+'px';
    dd.style.left = Math.max(8,r.left)+'px';
    dd.classList.add('open');
  }
}
document.addEventListener('click', function(){
  document.querySelectorAll('.dd').forEach(function(d){d.classList.remove('open');});
});

function togC(v, el){ e_stop(); toggle(v, sC, el, 'fpCat', 'lCat'); reload(); }
function togB(v, el){ e_stop(); toggle(v, sB, el, 'fpMar', 'lMar'); reload(); }
function togT(v, el){ e_stop(); toggle(v, sT, el, 'fpTai', 'lTai'); reload(); }

function e_stop(){ if(event) event.stopPropagation(); }

function toggle(v, arr, el, pillId, lblId){
  var i = arr.indexOf(v);
  if(i===-1){ arr.push(v); el.classList.add('on'); el.querySelector('.dck').textContent='v'; }
  else { arr.splice(i,1); el.classList.remove('on'); el.querySelector('.dck').textContent=''; }
  document.getElementById(lblId).textContent = arr.length ? '('+arr.length+')' : '';
  document.getElementById(pillId).classList.toggle('on', arr.length>0);
}

function apPrix(){
  pMn = parseFloat(document.getElementById('pMn').value)||0;
  pMx = parseFloat(document.getElementById('pMx').value)||0;
  document.getElementById('lPrix').textContent = (pMn||pMx) ? '('+( pMn?pMn+'':'')+'-'+(pMx?pMx+'€':'')+')'  : '';
  document.getElementById('fpPrix').classList.toggle('on', !!(pMn||pMx));
  reload();
}

function setPre(mn,mx){
  if(event) event.stopPropagation();
  document.getElementById('pMn').value = mn||'';
  document.getElementById('pMx').value = mx===999?'':mx;
  pMn=mn; pMx=mx;
  document.getElementById('lPrix').textContent='('+mn+'-'+mx+'€)';
  document.getElementById('fpPrix').classList.add('on');
  reload();
}

function resetAll(){
  sC=[]; sB=[]; sT=[]; pMn=0; pMx=0;
  document.getElementById('pMn').value='';
  document.getElementById('pMx').value='';
  document.getElementById('sb').value='';
  document.querySelectorAll('.di.on').forEach(function(el){el.classList.remove('on');el.querySelector('.dck').textContent='';});
  ['lCat','lMar','lTai','lPrix'].forEach(function(id){document.getElementById(id).textContent='';});
  ['fpCat','fpMar','fpTai','fpPrix'].forEach(function(id){document.getElementById(id).classList.remove('on');});
  renderB(allB);
  reload();
}

function buildUrl(){
  var u='/api/feed?limit=80';
  sC.forEach(function(c){u+='&cat='+encodeURIComponent(c);});
  sB.forEach(function(b){u+='&marque='+encodeURIComponent(b);});
  sT.forEach(function(t){u+='&taille='+encodeURIComponent(t);});
  if(pMn) u+='&pmin='+pMn;
  if(pMx) u+='&pmax='+pMx;
  return u;
}

function matchF(o){
  if(sC.length && sC.indexOf(o.categorie)===-1) return false;
  if(sB.length && !sB.some(function(b){return (o.marque||'').toLowerCase().indexOf(b.toLowerCase())>-1;})) return false;
  if(sT.length && !sT.some(function(t){return (o.taille||'').indexOf(t)>-1;})) return false;
  if(pMn && o.prix<pMn) return false;
  if(pMx && o.prix>pMx) return false;
  return true;
}

function mkCard(o, isNew){
  var fr = Math.round(o.prix*1.05*100)/100;
  var img = o.photo_url ? '<img class="ci" src="'+o.photo_url+'" loading="lazy">' : '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:64px">🏷️</div>';
  var pills = '';
  if(o.marque) pills+='<span class="cpl">'+o.marque+'</span>';
  if(o.taille) pills+='<span class="cpl">'+o.taille+'</span>';
  if(o.categorie) pills+='<span class="cpl">'+(CLBL[o.categorie]||o.categorie)+'</span>';
  var d = document.createElement('div');
  d.className = 'card';
  d.id = 'c'+o.id;
  d.innerHTML =
    '<div class="cbg">'+img+'<div class="cg"></div></div>'+
    '<div class="ctop">'+
      (isNew?'<span class="bnew">NOUVEAU</span>':'<span></span>')+
      '<button class="bdel" onclick="delCard(\'c'+o.id+'\')">✕</button>'+
    '</div>'+
    '<div class="cact">'+
      '<a href="'+o.url+'" target="_blank" class="bbuy" title="Acheter">⚡</a>'+
      '<a href="'+o.url+'" target="_blank" class="bsee">🔍</a>'+
    '</div>'+
    '<div class="cinf">'+
      '<div class="cpr"><span class="cpx">'+o.prix+'€</span><span class="cfs">'+fr+'€ frais incl.</span></div>'+
      '<div class="cpls">'+pills+'</div>'+
      '<div class="cti">'+(o.titre||'')+'</div>'+
    '</div>';
  return d;
}

function delCard(id){
  var el = document.getElementById(id);
  if(!el) return;
  el.style.transition='opacity .2s,transform .2s';
  el.style.opacity='0'; el.style.transform='scale(.94)';
  setTimeout(function(){el.remove();},200);
}

function addCard(o, isNew){
  if(rIds.has(o.id)) return;
  rIds.add(o.id);
  var feed = document.getElementById('feed');
  var w = feed.querySelector('.wt');
  if(w) feed.innerHTML='';
  var card = mkCard(o, isNew);
  if(isNew) feed.prepend(card); else feed.appendChild(card);
  if(notifOn && isNew && Notification.permission==='granted'){
    var n=new Notification((o.marque||'Vinted')+' — '+o.prix+'€',{body:o.titre,tag:o.id});
    n.onclick=function(){window.open(o.url,'_blank');n.close();};
    setTimeout(function(){n.close();},6000);
  }
}

function reload(){
  rIds.clear();
  var feed=document.getElementById('feed');
  feed.innerHTML='<div class="wt"><div style="font-size:36px">🔍</div><div>Filtrage...</div></div>';
  setTimeout(loadInit,50);
}

async function loadInit(){
  try{
    var d=await fetch(buildUrl()).then(function(r){return r.json();});
    var arts=(d.articles||[]).reverse();
    if(!arts.length){
      document.getElementById('feed').innerHTML='<div class="wt"><div style="font-size:36px">🕵️</div><div>Aucun article</div></div>';
      return;
    }
    arts.forEach(function(o){addCard(o,false);});
  }catch(e){}
}

document.getElementById('feed').addEventListener('scroll',function(){
  var f=this;
  if(f.scrollHeight-f.scrollTop-f.clientHeight<400 && !loading){
    loading=true;
    fetch(buildUrl()).then(function(r){return r.json();}).then(function(d){
      (d.articles||[]).forEach(function(o){addCard(o,false);});
      loading=false;
    }).catch(function(){loading=false;});
  }
});

function connectSSE(){
  if(sse) sse.close();
  sse=new EventSource('/api/stream');
  sse.onopen=function(){document.getElementById('ldot').classList.add('on');document.getElementById('ltxt').textContent='En direct';};
  sse.onmessage=function(e){
    if(!e.data||e.data==='{}') return;
    try{
      var o=JSON.parse(e.data);
      if(!o.id||rIds.has(o.id)) return;
      if(!matchF(o)) return;
      addCard(o,true);
    }catch(err){}
  };
  sse.onerror=function(){
    document.getElementById('ldot').classList.remove('on');
    document.getElementById('ltxt').textContent='Reconnexion...';
    sse.close(); setTimeout(connectSSE,3000);
  };
}

function togNotif(){
  if(!('Notification' in window)) return;
  Notification.requestPermission().then(function(p){
    if(p==='granted'){ notifOn=!notifOn; document.getElementById('nBtn').classList.toggle('on',notifOn); }
  });
}

function goTop(){document.getElementById('feed').scrollTo({top:0,behavior:'smooth'});}

loadInit();
connectSSE();

// Recharge les nouveaux articles toutes les 10 secondes
setInterval(async function(){
  try {
    var d = await fetch(buildUrl()).then(function(r){return r.json();});
    (d.articles||[]).forEach(function(o){ addCard(o, true); });
  } catch(e) {}
}, 10000);
</script>
</body></html>"""

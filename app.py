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

# Cache marques et catégories Vinted
_vinted_brands = []
_vinted_cats = []
_vinted_data_lock = threading.Lock()

def load_vinted_data():
    """Charge les vraies marques et catégories depuis l'API Vinted."""
    global _vinted_brands, _vinted_cats
    try:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "fr-FR,fr;q=0.9",
        })
        s.get("https://www.vinted.fr", timeout=8)

        # Catégories
        r = s.get("https://www.vinted.fr/api/v2/catalogs", timeout=8)
        if r.status_code == 200:
            data = r.json()
            cats = []
            def extract_cats(items, prefix=""):
                for item in items:
                    title = item.get("title", "")
                    full = (prefix + " > " + title) if prefix else title
                    cats.append({"id": item.get("id"), "title": full, "short": title})
                    if item.get("catalogs"):
                        extract_cats(item["catalogs"], full)
            extract_cats(data.get("catalogs", []))
            with _vinted_data_lock:
                _vinted_cats = cats[:80]

        # Marques populaires
        r2 = s.get("https://www.vinted.fr/api/v2/brands?page=1&per_page=200&sort=popularity", timeout=8)
        if r2.status_code == 200:
            brands = r2.json().get("brands", [])
            with _vinted_data_lock:
                _vinted_brands = [{"id": b.get("id"), "title": b.get("title", "")} for b in brands if b.get("title")]
    except Exception as e:
        pass

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
                    "id": iid,
                    "titre": item.get("title", ""),
                    "marque": marque,
                    "prix": prix,
                    "categorie": cat["nom"],
                    "taille": item.get("size_title", ""),
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

HTML = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>VintedFeed</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--acc:#b8ff00;--dark:#0a0a0a;--surface:rgba(20,20,20,.95);--text:#fff;--t2:rgba(255,255,255,.6)}
body{background:var(--dark);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100dvh;overflow:hidden;display:flex;flex-direction:column}

/* HEADER */
.header{padding:10px 14px 6px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:10}
.logo{font-size:16px;font-weight:800;letter-spacing:-.5px}
.logo em{color:var(--acc);font-style:normal}
.header-right{display:flex;align-items:center;gap:8px}
.live-pill{display:flex;align-items:center;gap:5px;background:rgba(255,255,255,.08);padding:4px 10px;border-radius:20px;font-size:11px;color:var(--t2)}
.live-dot{width:6px;height:6px;border-radius:50%;background:#555}
.live-dot.on{background:var(--acc);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* FILTRES */
.filters{padding:0 12px 8px;display:flex;gap:6px;overflow-x:auto;flex-shrink:0;scrollbar-width:none}
.filters::-webkit-scrollbar{display:none}
.filter-pill{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:var(--t2);padding:5px 12px;border-radius:20px;font-size:12px;white-space:nowrap;cursor:pointer;flex-shrink:0;position:relative;user-select:none;transition:all .15s}
.filter-pill.active{background:var(--acc);border-color:var(--acc);color:#000;font-weight:600}
.dd{position:fixed;top:100px;left:12px;background:#1a1a1a;border:1px solid rgba(255,255,255,.12);border-radius:14px;min-width:220px;max-width:calc(100vw - 24px);max-height:60vh;overflow-y:auto;z-index:1000;display:none;box-shadow:0 8px 32px rgba(0,0,0,.9)}
.dd.open{display:block}
.dd::-webkit-scrollbar{width:3px}
.dd::-webkit-scrollbar-thumb{background:rgba(255,255,255,.2);border-radius:2px}
.dd-item{display:flex;align-items:center;gap:10px;padding:9px 14px;font-size:13px;cursor:pointer;transition:background .1s}
.dd-item:hover{background:rgba(255,255,255,.06)}
.dd-item.on{color:var(--acc)}
.dd-item input{accent-color:var(--acc);width:15px;height:15px;flex-shrink:0}
.dd-sep{font-size:10px;color:var(--t2);padding:10px 14px 4px;text-transform:uppercase;letter-spacing:.06em;border-top:1px solid rgba(255,255,255,.06);margin-top:4px}
.dd-sep:first-child{border-top:none;margin-top:0}
.prix-row{display:flex;gap:6px;padding:10px 14px}
.prix-inp{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#fff;padding:6px 10px;border-radius:8px;font-size:13px;width:80px;outline:none}
.prix-inp:focus{border-color:var(--acc)}
.dd-preset{padding:8px 14px;font-size:13px;cursor:pointer;color:var(--t2)}
.dd-preset:hover{color:#fff}

/* FEED VERTICAL */
.feed{flex:1;overflow-y:scroll;scroll-snap-type:y mandatory;scrollbar-width:none;display:flex;flex-direction:column;align-items:center;gap:10px;padding:8px 0}
.feed::-webkit-scrollbar{display:none}

/* CARTE */
.card{width:min(390px,100%);height:82dvh;scroll-snap-align:start;flex-shrink:0;position:relative;display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden;border-radius:16px}
.card-bg{position:absolute;inset:0;z-index:0}
.card-img{width:100%;height:100%;object-fit:cover}
.card-img-ph{width:100%;height:100%;background:#1a1a1a;display:flex;align-items:center;justify-content:center;font-size:64px}
.card-gradient{position:absolute;inset:0;background:linear-gradient(to bottom, rgba(0,0,0,.2) 0%, transparent 30%, transparent 50%, rgba(0,0,0,.85) 100%)}

/* BADGES TOP */
.card-top{position:absolute;top:14px;left:14px;right:14px;display:flex;align-items:flex-start;justify-content:space-between;z-index:2}
.badge-new{background:var(--acc);color:#000;font-size:11px;font-weight:700;padding:4px 10px;border-radius:20px}
.badge-ts{background:rgba(0,0,0,.5);color:var(--t2);font-size:11px;padding:4px 10px;border-radius:20px;backdrop-filter:blur(8px)}

/* INFOS BAS */
.card-info{position:relative;z-index:2;padding:14px 14px 18px}
.card-prix-row{display:flex;align-items:baseline;gap:8px;margin-bottom:8px}
.card-prix-main{font-size:26px;font-weight:800;line-height:1}
.card-prix-frais{font-size:12px;color:var(--t2);text-decoration:line-through}
.card-pills{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.cpill{background:rgba(255,255,255,.12);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.2);padding:4px 12px;border-radius:20px;font-size:12px;font-weight:500}
.card-titre{font-size:13px;color:rgba(255,255,255,.8);line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}

/* BOUTON ACHAT */
.card-actions{position:absolute;right:12px;bottom:80px;display:flex;flex-direction:column;gap:6px;z-index:2;align-items:center}
.btn-flash{width:50px;height:50px;border-radius:50%;background:var(--acc);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 4px 16px rgba(184,255,0,.4);transition:transform .15s}
.btn-flash:hover{transform:scale(1.1)}
.btn-see{width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);backdrop-filter:blur(8px);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:16px;text-decoration:none}

/* BARRE BAS */
.bottombar{flex-shrink:0;background:rgba(10,10,10,.95);backdrop-filter:blur(12px);border-top:1px solid rgba(255,255,255,.06);padding:10px 20px;display:flex;align-items:center;justify-content:space-around;z-index:10}
.bb-btn{display:flex;flex-direction:column;align-items:center;gap:3px;background:none;border:none;color:var(--t2);font-size:10px;cursor:pointer;padding:0}
.bb-btn.active{color:var(--acc)}
.bb-icon{font-size:20px}

/* WAITING */
.waiting-card{width:min(390px,100%);height:82dvh;scroll-snap-align:start;flex-shrink:0;border-radius:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;scroll-snap-align:start;gap:12px;color:var(--t2);flex-shrink:0}
.waiting-icon{font-size:48px}
</style>
</head><body>

<div class="header">
  <div class="logo">Vinted<em>Feed</em></div>
  <div class="header-right">
    <div class="live-pill">
      <div class="live-dot" id="liveDot"></div>
      <span id="liveText">Connexion...</span>
    </div>
  </div>
</div>

<div class="filters" id="filtersBar">
  <div class="filter-pill" id="pillCat" onclick="toggleDD('ddCat',this,event)">
    Catégorie <span id="lblCat"></span>
    <div class="dd" id="ddCat"><div style="padding:10px;font-size:12px;color:var(--t2)">Chargement...</div></div>
  </div>

  <div class="filter-pill" id="pillMarque" onclick="toggleDD('ddMarque',this,event)">
    Marque <span id="lblMarque"></span>
    <div class="dd" id="ddMarque">
      <div style="padding:8px 14px"><input type="text" id="searchMarque" placeholder="Rechercher une marque..." oninput="filterBrands()" onclick="event.stopPropagation()" style="width:100%;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#fff;padding:6px 10px;border-radius:8px;font-size:12px;outline:none"></div>
      <div id="brandsList"><div style="padding:10px;font-size:12px;color:var(--t2)">Chargement...</div></div>
    </div>
  </div>

  <div class="filter-pill" id="pillTaille" onclick="toggleDD('ddTaille',this,event)">
    Taille <span id="lblTaille"></span>
    <div class="dd" id="ddTaille">
      <div class="dd-sep">Vêtements</div>
      <div class="dd-item" onclick="toggleTaille('XS',this)"><span style="font-size:14px;margin-right:4px"></span>XS</div>
      <div class="dd-item" onclick="toggleTaille('S',this)"><span style="font-size:14px;margin-right:4px"></span>S</div>
      <div class="dd-item" onclick="toggleTaille('M',this)"><span style="font-size:14px;margin-right:4px"></span>M</div>
      <div class="dd-item" onclick="toggleTaille('L',this)"><span style="font-size:14px;margin-right:4px"></span>L</div>
      <div class="dd-item" onclick="toggleTaille('XL',this)"><span style="font-size:14px;margin-right:4px"></span>XL</div>
      <div class="dd-item" onclick="toggleTaille('XXL',this)"><span style="font-size:14px;margin-right:4px"></span>XXL</div>
      <div class="dd-sep">Chaussures</div>
      <div class="dd-item" onclick="toggleTaille('36',this)"><span style="font-size:14px;margin-right:4px"></span>36</div>
      <div class="dd-item" onclick="toggleTaille('37',this)"><span style="font-size:14px;margin-right:4px"></span>37</div>
      <div class="dd-item" onclick="toggleTaille('38',this)"><span style="font-size:14px;margin-right:4px"></span>38</div>
      <div class="dd-item" onclick="toggleTaille('39',this)"><span style="font-size:14px;margin-right:4px"></span>39</div>
      <div class="dd-item" onclick="toggleTaille('40',this)"><span style="font-size:14px;margin-right:4px"></span>40</div>
      <div class="dd-item" onclick="toggleTaille('41',this)"><span style="font-size:14px;margin-right:4px"></span>41</div>
      <div class="dd-item" onclick="toggleTaille('42',this)"><span style="font-size:14px;margin-right:4px"></span>42</div>
      <div class="dd-item" onclick="toggleTaille('43',this)"><span style="font-size:14px;margin-right:4px"></span>43</div>
      <div class="dd-item" onclick="toggleTaille('44',this)"><span style="font-size:14px;margin-right:4px"></span>44</div>
      <div class="dd-item" onclick="toggleTaille('45',this)"><span style="font-size:14px;margin-right:4px"></span>45</div>
    </div>
  </div>

  <div class="filter-pill" id="pillPrix" onclick="toggleDD('ddPrix',this,event)">
    Prix <span id="lblPrix"></span>
    <div class="dd" id="ddPrix" style="min-width:200px">
      <div class="prix-row">
        <input class="prix-inp" type="number" id="pMin" placeholder="Min €" oninput="onFilter()">
        <input class="prix-inp" type="number" id="pMax" placeholder="Max €" oninput="onFilter()">
      </div>
      <div class="dd-preset" onclick="setPreset(0,10)">Moins de 10€</div>
      <div class="dd-preset" onclick="setPreset(0,20)">Moins de 20€</div>
      <div class="dd-preset" onclick="setPreset(0,50)">Moins de 50€</div>
      <div class="dd-preset" onclick="setPreset(10,30)">10€ — 30€</div>
      <div class="dd-preset" onclick="setPreset(20,50)">20€ — 50€</div>
      <div class="dd-preset" onclick="setPreset(50,999)">Plus de 50€</div>
    </div>
  </div>

  <div class="filter-pill" id="pillReset" onclick="resetAll()" style="color:rgba(255,100,100,.7)">Reset</div>
</div>

<div class="feed" id="feed">
  <div class="waiting-card">
    <div class="waiting-icon">⚡</div>
    <div>Connexion au feed...</div>
  </div>
</div>

<div class="bottombar">
  <button class="bb-btn active">
    <span class="bb-icon">⚡</span>
    <span>Feed</span>
  </button>
  <button class="bb-btn" onclick="scrollToTop()">
    <span class="bb-icon">🔝</span>
    <span>Top</span>
  </button>
  <button class="bb-btn" id="notifBtn" onclick="toggleNotif()">
    <span class="bb-icon">🔔</span>
    <span>Alertes</span>
  </button>
</div>

<script>
let seenIds = new Set();
let renderedIds = new Set();
let notifOn = false;
let sseSource = null;
let filters = {cats:[], marques:[], tailles:[], pMin:0, pMax:0};

// ── DROPDOWN ─────────────────────────────────────────────────────────────────
function toggleDD(id, pill, e) {
  e.preventDefault();
  e.stopPropagation();
  const dd = document.getElementById(id);
  const wasOpen = dd.classList.contains('open');
  document.querySelectorAll('.dd').forEach(d => d.classList.remove('open'));
  if (!wasOpen) {
    const rect = pill.getBoundingClientRect();
    dd.style.top = (rect.bottom + 6) + 'px';
    dd.style.left = Math.max(12, rect.left) + 'px';
    dd.classList.add('open');
  }
}
document.addEventListener('click', function(e) {
  if (!e.target.closest('.filter-pill')) {
    document.querySelectorAll('.dd').forEach(d => d.classList.remove('open'));
  }
});
document.addEventListener('touchstart', function(e) {
  if (!e.target.closest('.filter-pill')) {
    document.querySelectorAll('.dd').forEach(d => d.classList.remove('open'));
  }
});

// ── FILTRES ───────────────────────────────────────────────────────────────────
function onFilter() {
  filters.pMin = parseFloat(document.getElementById('pMin').value) || 0;
  filters.pMax = parseFloat(document.getElementById('pMax').value) || 0;
  updatePills();
  reloadFeedWithFilters();
}

function updatePills() {
  document.getElementById('pillCat').classList.toggle('active', filters.cats.length > 0);
  document.getElementById('pillMarque').classList.toggle('active', filters.marques.length > 0);
  document.getElementById('pillTaille').classList.toggle('active', filters.tailles.length > 0);
  document.getElementById('pillPrix').classList.toggle('active', !!(filters.pMin || filters.pMax));
}

function setPreset(mn, mx) {
  document.getElementById('pMin').value = mn || '';
  document.getElementById('pMax').value = mx === 999 ? '' : mx;
  filters.pMin = mn; filters.pMax = mx;
  updatePills();
  reloadFeedWithFilters();
}

function resetAll() {
  document.getElementById('pMin').value = '';
  document.getElementById('pMax').value = '';
  if (document.getElementById('searchMarque')) document.getElementById('searchMarque').value = '';
  selectedBrands = []; selectedCats = [];
  filters = {cats:[], marques:[], tailles:[], pMin:0, pMax:0};
  document.querySelectorAll('.dd-item.on').forEach(el => {
    el.classList.remove('on');
    el.style.color = ''; el.style.background = '';
    const sp = el.querySelector('span'); if(sp) sp.textContent = '';
  });
  document.querySelectorAll('.dd input[type=checkbox]').forEach(cb => cb.checked = false);
  ['lblCat','lblMarque','lblTaille','lblPrix'].forEach(id => { const el = document.getElementById(id); if(el) el.textContent = ''; });
  ['pillCat','pillMarque','pillTaille','pillPrix'].forEach(id => { const el = document.getElementById(id); if(el) el.classList.remove('active'); });
  initStaticFilters();
}

function matchFilters(o) {
  if (filters.cats.length && !filters.cats.includes(o.categorie)) return false;
  if (filters.marques.length && !filters.marques.some(m => (o.marque||'').toLowerCase().includes(m.toLowerCase()))) return false;
  if (filters.tailles.length && !filters.tailles.some(t => (o.taille||'').includes(t))) return false;
  if (filters.pMin && o.prix < filters.pMin) return false;
  if (filters.pMax && o.prix > filters.pMax) return false;
  return true;
}

// ── CARTE ─────────────────────────────────────────────────────────────────────
function makeCard(o, isNew) {
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.id = o.id;

  const fraisIncl = Math.round(o.prix * 1.05 * 100) / 100;
  const ts = isNew ? 'Il y a 1 seconde' : 'Recent';

  let imgHtml = '';
  if (o.photo_url) {
    imgHtml = '<img class="card-img" src="' + o.photo_url + '" loading="lazy">';
  } else {
    imgHtml = '<div class="card-img-ph">🏷️</div>';
  }

  let pills = '';
  if (o.marque) pills += '<span class="cpill">' + o.marque + '</span>';
  if (o.taille) pills += '<span class="cpill">' + o.taille + '</span>';
  if (o.categorie) pills += '<span class="cpill">' + o.categorie + '</span>';

  card.innerHTML =
    '<div class="card-bg">' + imgHtml + '<div class="card-gradient"></div></div>' +
    '<div class="card-top">' +
      (isNew ? '<span class="badge-new">NOUVEAU</span>' : '<span></span>') +
      '<span class="badge-ts">' + ts + '</span>' +
    '</div>' +
    '<div class="card-actions">' +
      '<a href="' + o.url + '" target="_blank" class="btn-flash" title="Acheter">⚡</a>' +
      '<a href="' + o.url + '" target="_blank" class="btn-see" title="Voir">↗</a>' +
    '</div>' +
    '<div class="card-info">' +
      '<div class="card-prix-row">' +
        '<span class="card-prix-main">' + o.prix + '€</span>' +
        '<span class="card-prix-frais">' + fraisIncl + '€ frais incl.</span>' +
      '</div>' +
      '<div class="card-pills">' + pills + '</div>' +
      '<div class="card-titre">' + (o.titre || '') + '</div>' +
    '</div>';

  return card;
}

function addCard(o, isNew) {
  if (renderedIds.has(o.id)) return;
  renderedIds.add(o.id);
  if (!matchFilters(o)) return;

  const feed = document.getElementById('feed');
  const waiting = feed.querySelector('.waiting-card');
  if (waiting) feed.innerHTML = '';

  const card = makeCard(o, isNew);

  if (isNew) {
    feed.prepend(card);
  } else {
    feed.appendChild(card);
  }

  // Notification
  if (notifOn && isNew && Notification.permission === 'granted') {
    const n = new Notification((o.marque || o.categorie) + ' — ' + o.prix + '€', {
      body: o.titre,
      tag: o.id,
    });
    n.onclick = function() { window.open(o.url, '_blank'); n.close(); };
    setTimeout(function() { n.close(); }, 6000);
  }

  // Limiter à 200 cartes
  while (feed.children.length > 200) feed.removeChild(feed.lastChild);
}

// ── SSE ───────────────────────────────────────────────────────────────────────
function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource('/api/stream');
  sseSource.onopen = function() {
    document.getElementById('liveDot').classList.add('on');
    document.getElementById('liveText').textContent = 'En direct';
  };
  sseSource.onmessage = function(e) {
    if (!e.data || e.data === '{}') return;
    try {
      var o = JSON.parse(e.data);
      if (!o.id) return;
      // Ne pas bloquer les nouveaux articles SSE
      if (renderedIds.has(o.id)) return;
      seenIds.add(o.id);
      addCard(o, true);
    } catch(err) {}
  };
  sseSource.onerror = function() {
    document.getElementById('liveDot').classList.remove('on');
    document.getElementById('liveText').textContent = 'Reconnexion...';
    sseSource.close();
    setTimeout(connectSSE, 3000);
  };
}

// ── NOTIFS ────────────────────────────────────────────────────────────────────
function toggleNotif() {
  if (!('Notification' in window)) return;
  Notification.requestPermission().then(function(p) {
    if (p === 'granted') {
      notifOn = !notifOn;
      var btn = document.getElementById('notifBtn');
      btn.classList.toggle('active', notifOn);
    }
  });
}

function scrollToTop() {
  document.getElementById('feed').scrollTo({top: 0, behavior: 'smooth'});
}

// ── INIT ──────────────────────────────────────────────────────────────────────
async function loadInitial() {
  try {
    var d = await fetch('/api/feed?limit=50').then(function(r) { return r.json(); });
    var arts = (d.articles || []).reverse();
    arts.forEach(function(o) { addCard(o, false); });
  } catch(e) {}
}

// Charger les vraies marques et catégories Vinted
let allBrands = [];
let selectedBrands = [];
let selectedCats = [];

// Données statiques immédiates
const CATS_STATIC = [
  "Vetements femme","Vetements homme","Chaussures femme","Chaussures homme",
  "Sacs","Accessoires","Sport","Electronique","Maison","Jeux video","Livres","Enfants"
];
const BRANDS_STATIC = [
  "Nike","Adidas","Jordan","New Balance","Puma","Converse","Vans","Reebok","Asics","Saucony",
  "Supreme","Carhartt","Stone Island","Palace","Stussy","Off-White","A Bathing Ape","Kith",
  "Zara","H&M","Mango","Pull&Bear","Bershka","Uniqlo","Cos","& Other Stories",
  "Ralph Lauren","Tommy Hilfiger","Lacoste","Levi's","Wrangler","Lee","Calvin Klein","Guess",
  "The North Face","Patagonia","Arc'teryx","Salomon","Columbia","Napapijri","Canada Goose",
  "Louis Vuitton","Gucci","Prada","Balenciaga","Dior","Chanel","Hermes","Givenchy","Burberry",
  "Apple","Samsung","Sony","Nintendo","Microsoft","Bose","JBL","Beats",
  "Decathlon","Nike Training","Adidas Sport","Under Armour","Lululemon",
  "Vintage","Y2K","Retro"
];

function initStaticFilters() {
  // Catégories statiques
  const ddCat = document.getElementById('ddCat');
  ddCat.innerHTML = CATS_STATIC.map(c => {
    const label = c.replace('Vetements','Vêtements').replace('Electronique','Électronique').replace('video','vidéo');
    return '<div class="dd-item" onclick="toggleCat('' + c + '',this)">' +
      '<span style="font-size:14px;margin-right:4px"></span>' + label + '</div>';
  }).join('');

  // Marques statiques
  allBrands = BRANDS_STATIC.map(b => ({title: b}));
  renderBrands(allBrands);
}

async function loadVintedData() {
  // Charger d'abord les données statiques immédiatement
  initStaticFilters();

  // Puis essayer de charger les vraies données en arrière-plan
  try {
    const db = await fetch('/api/brands').then(r => r.json());
    if (db.brands && db.brands.length > 0) {
      allBrands = db.brands;
      renderBrands(allBrands);
    }
  } catch(e) {}
}

function renderBrands(brands) {
  const list = document.getElementById('brandsList');
  if (!brands.length) { list.innerHTML = '<div style="padding:10px;font-size:12px;color:var(--t2)">Aucune marque trouvée</div>'; return; }
  list.innerHTML = brands.slice(0, 150).map(b => {
    const sel = selectedBrands.includes(b.title);
    return '<div class="dd-item' + (sel ? ' on' : '') + '" onclick="toggleBrand('' + b.title.replace(/'/g,"\'") + '',this)" style="' + (sel ? 'color:var(--acc);background:rgba(184,255,0,.08)' : '') + '">' +
      '<span style="font-size:14px;margin-right:4px">' + (sel ? '✓' : '') + '</span>' + b.title + '</div>';
  }).join('');
}

function toggleBrand(title, el) {
  event.stopPropagation();
  const idx = selectedBrands.indexOf(title);
  if (idx === -1) {
    selectedBrands.push(title);
    el.classList.add('on');
    el.style.color = 'var(--acc)';
    el.style.background = 'rgba(184,255,0,.08)';
    el.querySelector('span').textContent = '✓';
  } else {
    selectedBrands.splice(idx, 1);
    el.classList.remove('on');
    el.style.color = '';
    el.style.background = '';
    el.querySelector('span').textContent = '';
  }
  filters.marques = selectedBrands.slice();
  document.getElementById('lblMarque').textContent = selectedBrands.length ? '(' + selectedBrands.length + ')' : '';
  document.getElementById('pillMarque').classList.toggle('active', selectedBrands.length > 0);
  reloadFeedWithFilters();
}

function filterBrands() {
  const q = document.getElementById('searchMarque').value.toLowerCase();
  const filtered = q ? allBrands.filter(b => b.title.toLowerCase().includes(q)) : allBrands;
  renderBrands(filtered);
}

function reloadFeedWithFilters() {
  // Vider le feed visuel et recharger depuis la base
  renderedIds.clear();
  const feed = document.getElementById('feed');
  feed.innerHTML = '<div class="waiting-card"><div class="waiting-icon">🔍</div><div>Filtrage...</div></div>';
  setTimeout(loadInitial, 100);
}

function toggleCat(val, el) {
  event.stopPropagation();
  const idx = selectedCats.indexOf(val);
  if (idx === -1) {
    selectedCats.push(val);
    el.classList.add('on');
    el.style.color = 'var(--acc)';
    el.style.background = 'rgba(184,255,0,.08)';
    el.querySelector('span').textContent = '✓';
  } else {
    selectedCats.splice(idx, 1);
    el.classList.remove('on');
    el.style.color = '';
    el.style.background = '';
    el.querySelector('span').textContent = '';
  }
  filters.cats = selectedCats.slice();
  document.getElementById('lblCat').textContent = selectedCats.length ? '(' + selectedCats.length + ')' : '';
  document.getElementById('pillCat').classList.toggle('active', selectedCats.length > 0);
  reloadFeedWithFilters();
}

function onFilterCat() {}

function onFilterMarque() {}

function toggleTaille(val, el) {
  event.stopPropagation();
  const idx = filters.tailles.indexOf(val);
  if (idx === -1) {
    filters.tailles.push(val);
    el.classList.add('on');
    el.style.color = 'var(--acc)';
    el.style.background = 'rgba(184,255,0,.08)';
    el.querySelector('span').textContent = '✓';
  } else {
    filters.tailles.splice(idx, 1);
    el.classList.remove('on');
    el.style.color = '';
    el.style.background = '';
    el.querySelector('span').textContent = '';
  }
  document.getElementById('lblTaille').textContent = filters.tailles.length ? '(' + filters.tailles.length + ')' : '';
  document.getElementById('pillTaille').classList.toggle('active', filters.tailles.length > 0);
  reloadFeedWithFilters();
}

loadVintedData();
loadInitial();
connectSSE();
</script>
</body></html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

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

@app.route("/api/brands")
def api_brands():
    with _vinted_data_lock:
        return jsonify({"brands": _vinted_brands})

@app.route("/api/cats")
def api_cats():
    with _vinted_data_lock:
        return jsonify({"cats": _vinted_cats})

@app.route("/api/feed")
def api_feed():
    try:
        limit = int(request.args.get("limit", 50))
        c = sqlite3.connect(DB)
        rows = c.execute("SELECT id,titre,marque,prix,categorie,taille,nb_favoris,url,photo_url FROM articles ORDER BY date_scraping DESC LIMIT ?", (limit,)).fetchall()
        c.close()
        arts = [{"id":r[0],"titre":r[1],"marque":r[2],"prix":r[3],"categorie":r[4],"taille":r[5],"nb_favoris":r[6],"url":r[7],"photo_url":r[8]} for r in rows]
        return jsonify({"articles": arts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

init_db()
threading.Thread(target=load_vinted_data, daemon=True).start()
threading.Thread(target=start_scanner, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

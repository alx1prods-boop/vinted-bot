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
            proxies.append(f"http://{user}:{pwd}@{host}:{port}")
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
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VintedFeed</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0a0a0f;--surface:#13131a;--s2:#1c1c26;--border:#252530;--text:#f0f0f8;--t2:#7070a0;--accent:#7c6af7;--green:#00d68f;--red:#ff4d6d;}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}

.header{padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;flex-shrink:0}
.logo{font-size:17px;font-weight:700}.logo em{color:var(--accent);font-style:normal}
.live{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--t2)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--border)}
.dot.on{background:var(--green);animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.hright{margin-left:auto;display:flex;align-items:center;gap:8px}
.speed-group{display:flex;gap:4px}
.spd{background:var(--s2);border:1px solid var(--border);color:var(--t2);padding:4px 10px;border-radius:20px;font-size:11px;cursor:pointer;transition:all .15s}
.spd.on{border-color:var(--accent);color:var(--accent)}
.ctr{font-size:12px;color:var(--t2)}

.filters{padding:8px 12px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0;align-items:center}
.filter-btn{background:var(--s2);border:1px solid var(--border);color:var(--text);padding:6px 12px;border-radius:20px;font-size:12px;cursor:pointer;position:relative;user-select:none}
.filter-btn:hover{border-color:var(--accent)}
.filter-btn.active{border-color:var(--accent);color:var(--accent)}
.dropdown{position:absolute;top:calc(100% + 6px);left:0;background:var(--surface);border:1px solid var(--border);border-radius:10px;min-width:200px;max-height:280px;overflow-y:auto;z-index:100;display:none;box-shadow:0 8px 24px rgba(0,0,0,.5)}
.dropdown.open{display:block}
.dropdown::-webkit-scrollbar{width:4px}
.dropdown::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.dd-item{display:flex;align-items:center;gap:8px;padding:8px 12px;font-size:12px;cursor:pointer;transition:background .1s}
.dd-item:hover{background:var(--s2)}
.dd-item.checked{color:var(--accent)}
.dd-item input[type=checkbox]{accent-color:var(--accent);width:14px;height:14px;flex-shrink:0}
.dd-sep{font-size:10px;color:var(--t2);padding:8px 12px 4px;text-transform:uppercase;letter-spacing:.05em;border-top:1px solid var(--border);margin-top:4px}
.prix-inputs{display:flex;gap:6px;padding:8px 12px}
.prix-input{background:var(--s2);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:7px;font-size:12px;width:80px}
.btn-reset{background:transparent;border:1px solid var(--border);color:var(--t2);padding:6px 12px;border-radius:20px;font-size:12px;cursor:pointer}

.progress-bar{height:3px;background:var(--s2);flex-shrink:0}
.progress-fill{height:100%;background:var(--accent);width:100%}

.stage{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:16px;position:relative}

.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;width:100%;max-width:360px;overflow:hidden;animation:pop .3s cubic-bezier(.34,1.56,.64,1)}
@keyframes pop{from{opacity:0;transform:scale(.9)}to{opacity:1;transform:scale(1)}}
.card-img-wrap{position:relative;height:280px;background:var(--s2);overflow:hidden}
.card-img{width:100%;height:100%;object-fit:cover;display:block}
.card-ph{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:48px;color:var(--border)}
.badge-new{position:absolute;top:10px;left:10px;font-size:10px;font-weight:700;background:var(--green);color:#000;padding:3px 9px;border-radius:20px}
.badge-cat{position:absolute;top:10px;right:10px;font-size:10px;background:rgba(0,0,0,.65);color:#fff;padding:3px 9px;border-radius:20px}
.card-body{padding:14px}
.card-row1{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px}
.card-titre{font-size:14px;font-weight:600;line-height:1.3;flex:1}
.card-prix{font-size:22px;font-weight:700;color:var(--accent);flex-shrink:0}
.card-pills{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.pill{font-size:11px;padding:3px 9px;border-radius:20px;background:var(--s2)}
.pill.marque{color:var(--accent);border:1px solid rgba(124,106,247,.3)}
.card-fav{font-size:11px;color:var(--t2);margin-bottom:12px}
.card-actions{display:flex;gap:8px}
.btn-buy{flex:2;background:var(--accent);color:#fff;font-size:13px;font-weight:700;padding:11px;border-radius:10px;text-decoration:none;text-align:center;transition:opacity .15s;display:block}
.btn-buy:hover{opacity:.85}
.btn-see{flex:1;background:var(--s2);border:1px solid var(--border);color:var(--t2);font-size:13px;padding:11px;border-radius:10px;text-decoration:none;text-align:center;display:block;transition:all .15s}
.btn-see:hover{border-color:var(--t2);color:var(--text)}
.btn-skip{flex:1;background:transparent;border:1px solid var(--border);color:var(--t2);font-size:13px;padding:11px;border-radius:10px;cursor:pointer;transition:all .15s}
.btn-skip:hover{border-color:var(--red);color:var(--red)}

.waiting{text-align:center;color:var(--t2)}.waiting-icon{font-size:40px;margin-bottom:10px}
.overlay{display:none;position:absolute;inset:0;background:rgba(0,0,0,.5);align-items:center;justify-content:center;font-size:14px;color:#fff;border-radius:16px;cursor:pointer}
.overlay.show{display:flex}
</style>
</head><body>

<div class="header">
  <div class="logo">Vinted<em>Feed</em></div>
  <div class="live"><div class="dot" id="dot"></div><span id="liveText">Connexion...</span></div>
  <div class="hright">
    <span class="ctr" id="ctr">0 vus</span>
    <div class="speed-group">
      <button class="spd" onclick="setSpd(3000,this)">3s</button>
      <button class="spd on" onclick="setSpd(6000,this)">6s</button>
      <button class="spd" onclick="setSpd(10000,this)">10s</button>
    </div>
  </div>
</div>

<div class="filters" id="filtersBar">
  <!-- Catégories -->
  <div class="filter-btn" id="btnCat" onclick="toggleDD('ddCat',event)">
    Catégorie <span id="lblCat"></span> ▾
    <div class="dropdown" id="ddCat">
      <label class="dd-item"><input type="checkbox" value="Vetements femme" onchange="updateFilter('cat',event)"> Vêtements femme</label>
      <label class="dd-item"><input type="checkbox" value="Vetements homme" onchange="updateFilter('cat',event)"> Vêtements homme</label>
      <label class="dd-item"><input type="checkbox" value="Chaussures femme" onchange="updateFilter('cat',event)"> Chaussures femme</label>
      <label class="dd-item"><input type="checkbox" value="Chaussures homme" onchange="updateFilter('cat',event)"> Chaussures homme</label>
      <label class="dd-item"><input type="checkbox" value="Sacs" onchange="updateFilter('cat',event)"> Sacs</label>
      <label class="dd-item"><input type="checkbox" value="Accessoires" onchange="updateFilter('cat',event)"> Accessoires</label>
      <label class="dd-item"><input type="checkbox" value="Sport" onchange="updateFilter('cat',event)"> Sport</label>
      <label class="dd-item"><input type="checkbox" value="Electronique" onchange="updateFilter('cat',event)"> Électronique</label>
      <label class="dd-item"><input type="checkbox" value="Maison" onchange="updateFilter('cat',event)"> Maison</label>
      <label class="dd-item"><input type="checkbox" value="Jeux video" onchange="updateFilter('cat',event)"> Jeux vidéo</label>
      <label class="dd-item"><input type="checkbox" value="Livres" onchange="updateFilter('cat',event)"> Livres</label>
      <label class="dd-item"><input type="checkbox" value="Enfants" onchange="updateFilter('cat',event)"> Enfants</label>
    </div>
  </div>

  <!-- Marques -->
  <div class="filter-btn" id="btnMarque" onclick="toggleDD('ddMarque',event)">
    Marque <span id="lblMarque"></span> ▾
    <div class="dropdown" id="ddMarque">
      <div class="dd-sep">Sneakers</div>
      <label class="dd-item"><input type="checkbox" value="Nike" onchange="updateFilter('marque',event)"> Nike</label>
      <label class="dd-item"><input type="checkbox" value="Adidas" onchange="updateFilter('marque',event)"> Adidas</label>
      <label class="dd-item"><input type="checkbox" value="Jordan" onchange="updateFilter('marque',event)"> Jordan</label>
      <label class="dd-item"><input type="checkbox" value="New Balance" onchange="updateFilter('marque',event)"> New Balance</label>
      <label class="dd-item"><input type="checkbox" value="Puma" onchange="updateFilter('marque',event)"> Puma</label>
      <label class="dd-item"><input type="checkbox" value="Converse" onchange="updateFilter('marque',event)"> Converse</label>
      <label class="dd-item"><input type="checkbox" value="Vans" onchange="updateFilter('marque',event)"> Vans</label>
      <label class="dd-item"><input type="checkbox" value="Reebok" onchange="updateFilter('marque',event)"> Reebok</label>
      <div class="dd-sep">Streetwear</div>
      <label class="dd-item"><input type="checkbox" value="Supreme" onchange="updateFilter('marque',event)"> Supreme</label>
      <label class="dd-item"><input type="checkbox" value="Carhartt" onchange="updateFilter('marque',event)"> Carhartt</label>
      <label class="dd-item"><input type="checkbox" value="Stone Island" onchange="updateFilter('marque',event)"> Stone Island</label>
      <label class="dd-item"><input type="checkbox" value="Palace" onchange="updateFilter('marque',event)"> Palace</label>
      <label class="dd-item"><input type="checkbox" value="Stussy" onchange="updateFilter('marque',event)"> Stüssy</label>
      <div class="dd-sep">Mode</div>
      <label class="dd-item"><input type="checkbox" value="Zara" onchange="updateFilter('marque',event)"> Zara</label>
      <label class="dd-item"><input type="checkbox" value="H&M" onchange="updateFilter('marque',event)"> H&M</label>
      <label class="dd-item"><input type="checkbox" value="Ralph Lauren" onchange="updateFilter('marque',event)"> Ralph Lauren</label>
      <label class="dd-item"><input type="checkbox" value="Tommy Hilfiger" onchange="updateFilter('marque',event)"> Tommy Hilfiger</label>
      <label class="dd-item"><input type="checkbox" value="Lacoste" onchange="updateFilter('marque',event)"> Lacoste</label>
      <label class="dd-item"><input type="checkbox" value="Levi's" onchange="updateFilter('marque',event)"> Levi's</label>
      <div class="dd-sep">Outdoor</div>
      <label class="dd-item"><input type="checkbox" value="The North Face" onchange="updateFilter('marque',event)"> The North Face</label>
      <label class="dd-item"><input type="checkbox" value="Patagonia" onchange="updateFilter('marque',event)"> Patagonia</label>
      <label class="dd-item"><input type="checkbox" value="Arc'teryx" onchange="updateFilter('marque',event)"> Arc'teryx</label>
      <label class="dd-item"><input type="checkbox" value="Salomon" onchange="updateFilter('marque',event)"> Salomon</label>
      <div class="dd-sep">Luxe</div>
      <label class="dd-item"><input type="checkbox" value="Louis Vuitton" onchange="updateFilter('marque',event)"> Louis Vuitton</label>
      <label class="dd-item"><input type="checkbox" value="Gucci" onchange="updateFilter('marque',event)"> Gucci</label>
      <label class="dd-item"><input type="checkbox" value="Balenciaga" onchange="updateFilter('marque',event)"> Balenciaga</label>
      <label class="dd-item"><input type="checkbox" value="Dior" onchange="updateFilter('marque',event)"> Dior</label>
      <div class="dd-sep">Tech</div>
      <label class="dd-item"><input type="checkbox" value="Apple" onchange="updateFilter('marque',event)"> Apple</label>
      <label class="dd-item"><input type="checkbox" value="Samsung" onchange="updateFilter('marque',event)"> Samsung</label>
      <label class="dd-item"><input type="checkbox" value="Sony" onchange="updateFilter('marque',event)"> Sony</label>
      <label class="dd-item"><input type="checkbox" value="Nintendo" onchange="updateFilter('marque',event)"> Nintendo</label>
    </div>
  </div>

  <!-- Taille -->
  <div class="filter-btn" id="btnTaille" onclick="toggleDD('ddTaille',event)">
    Taille <span id="lblTaille"></span> ▾
    <div class="dropdown" id="ddTaille">
      <div class="dd-sep">Vêtements</div>
      <label class="dd-item"><input type="checkbox" value="XS" onchange="updateFilter('taille',event)"> XS</label>
      <label class="dd-item"><input type="checkbox" value="S" onchange="updateFilter('taille',event)"> S</label>
      <label class="dd-item"><input type="checkbox" value="M" onchange="updateFilter('taille',event)"> M</label>
      <label class="dd-item"><input type="checkbox" value="L" onchange="updateFilter('taille',event)"> L</label>
      <label class="dd-item"><input type="checkbox" value="XL" onchange="updateFilter('taille',event)"> XL</label>
      <label class="dd-item"><input type="checkbox" value="XXL" onchange="updateFilter('taille',event)"> XXL</label>
      <div class="dd-sep">Chaussures</div>
      <label class="dd-item"><input type="checkbox" value="36" onchange="updateFilter('taille',event)"> 36</label>
      <label class="dd-item"><input type="checkbox" value="37" onchange="updateFilter('taille',event)"> 37</label>
      <label class="dd-item"><input type="checkbox" value="38" onchange="updateFilter('taille',event)"> 38</label>
      <label class="dd-item"><input type="checkbox" value="39" onchange="updateFilter('taille',event)"> 39</label>
      <label class="dd-item"><input type="checkbox" value="40" onchange="updateFilter('taille',event)"> 40</label>
      <label class="dd-item"><input type="checkbox" value="41" onchange="updateFilter('taille',event)"> 41</label>
      <label class="dd-item"><input type="checkbox" value="42" onchange="updateFilter('taille',event)"> 42</label>
      <label class="dd-item"><input type="checkbox" value="43" onchange="updateFilter('taille',event)"> 43</label>
      <label class="dd-item"><input type="checkbox" value="44" onchange="updateFilter('taille',event)"> 44</label>
      <label class="dd-item"><input type="checkbox" value="45" onchange="updateFilter('taille',event)"> 45</label>
    </div>
  </div>

  <!-- Prix -->
  <div class="filter-btn" id="btnPrix" onclick="toggleDD('ddPrix',event)">
    Prix <span id="lblPrix"></span> ▾
    <div class="dropdown" id="ddPrix" style="min-width:180px">
      <div class="prix-inputs">
        <input class="prix-input" type="number" id="prixMin" placeholder="Min €" onchange="updatePrix()">
        <input class="prix-input" type="number" id="prixMax" placeholder="Max €" onchange="updatePrix()">
      </div>
      <label class="dd-item" onclick="setPreset(0,10)"> Moins de 10€</label>
      <label class="dd-item" onclick="setPreset(0,20)"> Moins de 20€</label>
      <label class="dd-item" onclick="setPreset(0,50)"> Moins de 50€</label>
      <label class="dd-item" onclick="setPreset(10,30)"> 10€ — 30€</label>
      <label class="dd-item" onclick="setPreset(20,50)"> 20€ — 50€</label>
      <label class="dd-item" onclick="setPreset(50,999)"> Plus de 50€</label>
    </div>
  </div>

  <button class="btn-reset" onclick="resetAll()">Reset</button>
</div>

<div class="progress-bar"><div class="progress-fill" id="pFill" style="width:100%;transition:none"></div></div>

<div class="stage" id="stage">
  <div class="waiting"><div class="waiting-icon">⚡</div><div>Connexion au feed...</div></div>
</div>

<script>
// ── ÉTAT ─────────────────────────────────────────────────────────────────────
let buffer = [];
let seenIds = new Set();
let viewed = 0;
let speed = 6000;
let paused = false;
let timer = null;
let filters = {cats:[], marques:[], tailles:[], prixMin:0, prixMax:0};
let sseSource = null;

// ── VITESSE ───────────────────────────────────────────────────────────────────
function setSpd(ms, btn) {
  speed = ms;
  document.querySelectorAll('.spd').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  if (!paused) scheduleNext(false);
}

// ── FILTRES DROPDOWN ──────────────────────────────────────────────────────────
function toggleDD(id, e) {
  e.stopPropagation();
  const dd = document.getElementById(id);
  const wasOpen = dd.classList.contains('open');
  document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('open'));
  if (!wasOpen) dd.classList.add('open');
}
document.addEventListener('click', () => {
  document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('open'));
});

function updateFilter(type, e) {
  e.stopPropagation();
  const val = e.target.value;
  const checked = e.target.checked;
  if (type === 'cat') {
    if (checked) filters.cats.push(val);
    else filters.cats = filters.cats.filter(v => v !== val);
    document.getElementById('lblCat').textContent = filters.cats.length ? '('+filters.cats.length+')' : '';
    document.getElementById('btnCat').classList.toggle('active', filters.cats.length > 0);
  } else if (type === 'marque') {
    if (checked) filters.marques.push(val);
    else filters.marques = filters.marques.filter(v => v !== val);
    document.getElementById('lblMarque').textContent = filters.marques.length ? '('+filters.marques.length+')' : '';
    document.getElementById('btnMarque').classList.toggle('active', filters.marques.length > 0);
  } else if (type === 'taille') {
    if (checked) filters.tailles.push(val);
    else filters.tailles = filters.tailles.filter(v => v !== val);
    document.getElementById('lblTaille').textContent = filters.tailles.length ? '('+filters.tailles.length+')' : '';
    document.getElementById('btnTaille').classList.toggle('active', filters.tailles.length > 0);
  }
}

function updatePrix() {
  filters.prixMin = parseFloat(document.getElementById('prixMin').value) || 0;
  filters.prixMax = parseFloat(document.getElementById('prixMax').value) || 0;
  const lbl = filters.prixMin || filters.prixMax ?
    (filters.prixMin ? filters.prixMin+'€' : '') + (filters.prixMax ? '—'+filters.prixMax+'€' : '') : '';
  document.getElementById('lblPrix').textContent = lbl ? '('+lbl+')' : '';
  document.getElementById('btnPrix').classList.toggle('active', !!(filters.prixMin || filters.prixMax));
}

function setPreset(mn, mx) {
  document.getElementById('prixMin').value = mn || '';
  document.getElementById('prixMax').value = mx === 999 ? '' : mx;
  filters.prixMin = mn; filters.prixMax = mx;
  updatePrix();
}

function resetAll() {
  filters = {cats:[], marques:[], tailles:[], prixMin:0, prixMax:0};
  document.querySelectorAll('.dropdown input[type=checkbox]').forEach(cb => cb.checked = false);
  document.getElementById('prixMin').value = '';
  document.getElementById('prixMax').value = '';
  ['Cat','Marque','Taille','Prix'].forEach(k => {
    document.getElementById('lbl'+k).textContent = '';
    document.getElementById('btn'+k).classList.remove('active');
  });
}

function matchFilters(o) {
  if (filters.cats.length && !filters.cats.includes(o.categorie)) return false;
  if (filters.marques.length && !filters.marques.some(m => (o.marque||'').toLowerCase().includes(m.toLowerCase()))) return false;
  if (filters.tailles.length && !filters.tailles.some(t => (o.taille||'').includes(t))) return false;
  if (filters.prixMin && o.prix < filters.prixMin) return false;
  if (filters.prixMax && o.prix > filters.prixMax) return false;
  return true;
}

// ── CARTE ─────────────────────────────────────────────────────────────────────
function showCard(o) {
  const stage = document.getElementById('stage');
  stage.innerHTML = '';

  const isNew = o._isNew || false;
  const imgHtml = o.photo_url
    ? '<img class="card-img" src="'+o.photo_url+'" loading="eager" onerror="this.style.display=\'none\';document.getElementById(\'ph_'+o.id+'\').style.display=\'flex\'">'
    : '';
  const phHtml = '<div class="card-ph" id="ph_'+o.id+'" style="'+(o.photo_url?'display:none':'')+'">🏷️</div>';

  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML =
    '<div class="card-img-wrap">' +
      imgHtml + phHtml +
      (isNew ? '<span class="badge-new">NOUVEAU</span>' : '') +
      '<span class="badge-cat">'+(o.categorie||'')+'</span>' +
    '</div>' +
    '<div class="card-body">' +
      '<div class="card-row1">' +
        '<div class="card-titre">'+(o.titre||'')+'</div>' +
        '<div class="card-prix">'+o.prix+'€</div>' +
      '</div>' +
      '<div class="card-pills">' +
        (o.marque ? '<span class="pill marque">'+o.marque+'</span>' : '') +
        (o.taille ? '<span class="pill">'+o.taille+'</span>' : '') +
      '</div>' +
      (o.nb_favoris ? '<div class="card-fav">❤️ '+o.nb_favoris+' favoris</div>' : '') +
      '<div class="card-actions">' +
        '<a class="btn-buy" href="'+o.url+'" target="_blank">💳 Acheter</a>' +
        '<a class="btn-see" href="'+o.url+'" target="_blank">👁 Voir</a>' +
        '<button class="btn-skip" onclick="skipCard()">⏭</button>' +
      '</div>' +
    '</div>';

  stage.appendChild(card);
  viewed++;
  document.getElementById('ctr').textContent = viewed + ' vus';
  startProgress();
}

function nextCard() {
  let art = null;
  while (buffer.length > 0) {
    const a = buffer.shift();
    if (matchFilters(a)) { art = a; break; }
  }
  if (art) {
    showCard(art);
  } else {
    document.getElementById('stage').innerHTML = '<div class="waiting"><div class="waiting-icon">⏳</div><div>En attente de nouveaux articles...</div></div>';
    document.getElementById('pFill').style.width = '100%';
  }
}

function skipCard() {
  clearTimeout(timer);
  nextCard();
}

function scheduleNext(immediate) {
  clearTimeout(timer);
  if (paused) return;
  timer = setTimeout(nextCard, immediate ? 0 : speed);
}

function startProgress() {
  const bar = document.getElementById('pFill');
  bar.style.transition = 'none';
  bar.style.width = '100%';
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      bar.style.transition = 'width '+speed+'ms linear';
      bar.style.width = '0%';
    });
  });
  scheduleNext(false);
}

// ── SSE ───────────────────────────────────────────────────────────────────────
function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource('/api/stream');
  sseSource.onopen = () => {
    document.getElementById('dot').classList.add('on');
    document.getElementById('liveText').textContent = 'En direct';
  };
  sseSource.onmessage = (e) => {
    if (!e.data || e.data === '{}') return;
    try {
      const o = JSON.parse(e.data);
      if (!o.id || seenIds.has(o.id)) return;
      seenIds.add(o.id);
      o._isNew = true;
      buffer.push(o);
      if (document.querySelector('.waiting')) nextCard();
    } catch(err) {}
  };
  sseSource.onerror = () => {
    document.getElementById('dot').classList.remove('on');
    document.getElementById('liveText').textContent = 'Reconnexion...';
    sseSource.close();
    setTimeout(connectSSE, 3000);
  };
}

async function loadInitial() {
  try {
    const d = await fetch('/api/feed?limit=200').then(r => r.json());
    (d.articles||[]).forEach(o => {
      if (!seenIds.has(o.id)) { seenIds.add(o.id); buffer.push(o); }
    });
    nextCard();
  } catch(e) { nextCard(); }
}

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

@app.route("/api/feed")
def api_feed():
    try:
        limit = int(request.args.get("limit", 200))
        c = sqlite3.connect(DB)
        rows = c.execute("SELECT id,titre,marque,prix,categorie,taille,nb_favoris,url,photo_url FROM articles ORDER BY date_scraping DESC LIMIT ?", (limit,)).fetchall()
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

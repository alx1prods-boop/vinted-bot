import os, time, sqlite3, requests, threading, json, random, queue
from datetime import datetime
from flask import Flask, jsonify, render_template_string, Response, stream_with_context, request

app = Flask(__name__)
DB = os.path.join("/tmp", "vinted_feed.db")

CATEGORIES = [
    {"nom": "Vêtements femme",   "id": 1904},
    {"nom": "Vêtements homme",   "id": 4},
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
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]

feed_queue = queue.Queue(maxsize=500)
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
                    "id": iid, "titre": item.get("title", ""),
                    "marque": marque, "prix": prix,
                    "categorie": cat["nom"], "taille": item.get("size_title", ""),
                    "nb_favoris": item.get("favourite_count", 0),
                    "url": f"https://www.vinted.fr/items/{iid}",
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

HTML = r"""<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VintedFeed</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0a0f;--surface:#13131a;--surface2:#1c1c26;--border:#252530;
  --text:#f0f0f8;--text2:#7070a0;--accent:#7c6af7;--green:#00d68f;--red:#ff4d6d;
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}

.header{padding:12px 16px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;flex-shrink:0}
.logo{font-size:17px;font-weight:700}.logo em{color:var(--accent);font-style:normal}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--border);flex-shrink:0}
.live-dot.on{background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.header-right{margin-left:auto;display:flex;align-items:center;gap:8px}
.counter{font-size:12px;color:var(--text2)}
.speed-wrap{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2)}
.speed-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:4px 10px;border-radius:20px;font-size:11px;cursor:pointer}
.speed-btn.active{border-color:var(--accent);color:var(--accent)}

.filters{padding:8px 12px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0}
.filter-select{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:20px;font-size:12px;cursor:pointer;outline:none}
.btn-reset{background:transparent;border:1px solid var(--border);color:var(--text2);padding:5px 10px;border-radius:20px;font-size:12px;cursor:pointer}

.stage{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;padding:20px}

.card{
  background:var(--surface);border:1px solid var(--border);border-radius:16px;
  width:100%;max-width:380px;overflow:hidden;
  animation:popIn .35s cubic-bezier(.34,1.56,.64,1);
  position:relative;
}
@keyframes popIn{from{opacity:0;transform:scale(.92)}to{opacity:1;transform:scale(1)}}

.card-img-wrap{position:relative;width:100%;height:320px;background:var(--surface2)}
.card-img{width:100%;height:100%;object-fit:cover;display:block}
.card-img-placeholder{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:48px;color:var(--border)}
.badge-new{position:absolute;top:12px;left:12px;font-size:11px;font-weight:700;background:var(--green);color:#000;padding:4px 10px;border-radius:20px}
.badge-cat{position:absolute;top:12px;right:12px;font-size:11px;background:rgba(0,0,0,.6);color:#fff;padding:4px 10px;border-radius:20px}

.card-body{padding:16px}
.card-top{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:8px}
.card-titre{font-size:15px;font-weight:600;line-height:1.3;flex:1}
.card-prix-big{font-size:24px;font-weight:700;color:var(--accent);margin-left:12px;flex-shrink:0}
.card-meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.meta-pill{font-size:11px;padding:3px 10px;border-radius:20px;background:var(--surface2)}
.meta-pill.marque{color:var(--accent);border:1px solid rgba(124,106,247,.3)}
.card-fav{font-size:12px;color:var(--text2);margin-bottom:14px}

.card-actions{display:flex;gap:8px}
.btn-buy{flex:2;background:var(--accent);color:#fff;font-size:14px;font-weight:700;padding:12px;border-radius:10px;text-decoration:none;text-align:center;transition:opacity .15s}
.btn-buy:hover{opacity:.85}
.btn-skip{flex:1;background:var(--surface2);border:1px solid var(--border);color:var(--text2);font-size:14px;padding:12px;border-radius:10px;cursor:pointer;text-align:center;transition:all .15s}
.btn-skip:hover{border-color:var(--red);color:var(--red)}

.progress-bar{height:3px;background:var(--surface2);position:relative;overflow:hidden}
.progress-fill{height:100%;background:var(--accent);width:100%;transform-origin:left;transition:none}
.progress-fill.running{transition:width linear}

.waiting{text-align:center;color:var(--text2);font-size:14px}
.waiting-icon{font-size:48px;margin-bottom:12px}

.paused-banner{position:absolute;top:12px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.8);color:var(--text);font-size:12px;padding:6px 14px;border-radius:20px;z-index:10;display:none}
.paused-banner.show{display:block}
</style>
</head><body>

<div class="header">
  <div class="logo">Vinted<em>Feed</em></div>
  <div class="live-dot" id="liveDot"></div>
  <span id="liveText" style="font-size:12px;color:var(--text2)">Connexion...</span>
  <div class="header-right">
    <span class="counter" id="counter">0 vus</span>
    <div class="speed-wrap">
      Vitesse :
      <button class="speed-btn" onclick="setSpeed(3000)">Lente</button>
      <button class="speed-btn active" onclick="setSpeed(5000)">Normal</button>
      <button class="speed-btn" onclick="setSpeed(8000)">Rapide</button>
    </div>
  </div>
</div>

<div class="filters">
  <select class="filter-select" id="fCat" onchange="applyFilters()">
    <option value="">Toutes catégories</option>
    <option>Vetements femme</option>
    <option>Vetements homme</option>
    <option>Chaussures femme</option>
    <option>Chaussures homme</option>
    <option>Sacs</option><option>Accessoires</option>
    <option>Sport</option><option>Electronique</option>
    <option>Maison</option><option>Jeux video</option>
    <option>Livres</option><option>Enfants</option>
  </select>
  <select class="filter-select" id="fTaille" onchange="applyFilters()">
    <option value="">Toutes tailles</option>
    <optgroup label="Vetements"><option>XS</option><option>S</option><option>M</option><option>L</option><option>XL</option><option>XXL</option></optgroup>
    <optgroup label="Chaussures"><option>36</option><option>37</option><option>38</option><option>39</option><option>40</option><option>41</option><option>42</option><option>43</option><option>44</option><option>45</option></optgroup>
  </select>
  <select class="filter-select" id="fPrix" onchange="applyFilters()">
    <option value="">Tous prix</option>
    <option value="0,10">Moins de 10€</option>
    <option value="0,20">Moins de 20€</option>
    <option value="0,50">Moins de 50€</option>
    <option value="10,30">10€ — 30€</option>
    <option value="20,50">20€ — 50€</option>
    <option value="50,999">Plus de 50€</option>
  </select>
  <select class="filter-select" id="fMarque" onchange="applyFilters()">
    <option value="">Toutes marques</option>
    <option>Nike</option><option>Adidas</option><option>Jordan</option>
    <option>New Balance</option><option>Puma</option><option>Converse</option>
    <option>Vans</option><option>The North Face</option><option>Levi's</option>
    <option>Zara</option><option>H&M</option><option>Ralph Lauren</option>
    <option>Tommy Hilfiger</option><option>Lacoste</option><option>Stone Island</option>
    <option>Supreme</option><option>Carhartt</option><option>Apple</option>
    <option>Samsung</option><option>Sony</option><option>Nintendo</option>
  </select>
  <button class="btn-reset" onclick="resetFilters()">Reset</button>
</div>

<div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>

<div class="stage" id="stage">
  <div class="waiting"><div class="waiting-icon">⚡</div>Connexion au feed...</div>
</div>
<div class="paused-banner" id="pausedBanner">⏸ En pause — clique pour reprendre</div>

<script>
let buffer = [];
let seenIds = new Set();
let currentArt = null;
let autoTimer = null;
let speed = 5000;
let paused = false;
let viewed = 0;
let sseSource = null;
let filters = {cat:'',taille:'',prixMin:0,prixMax:0,marque:''};

function setSpeed(ms) {
  speed = ms;
  document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  if (!paused) scheduleNext(true);
}

function applyFilters() {
  const prixVal = document.getElementById('fPrix').value;
  filters.cat    = document.getElementById('fCat').value;
  filters.taille = document.getElementById('fTaille').value;
  filters.marque = document.getElementById('fMarque').value;
  filters.prixMin = prixVal ? parseFloat(prixVal.split(',')[0]) : 0;
  filters.prixMax = prixVal ? parseFloat(prixVal.split(',')[1]) : 0;
}

function resetFilters() {
  ['fCat','fTaille','fPrix','fMarque'].forEach(id => document.getElementById(id).value = '');
  filters = {cat:'',taille:'',prixMin:0,prixMax:0,marque:''};
}

function matchFilters(o) {
  if (filters.cat && !o.categorie.includes(filters.cat)) return false;
  if (filters.taille && !(o.taille||'').includes(filters.taille)) return false;
  if (filters.marque && !(o.marque||'').toLowerCase().includes(filters.marque.toLowerCase())) return false;
  if (filters.prixMin && o.prix < filters.prixMin) return false;
  if (filters.prixMax && o.prix > filters.prixMax) return false;
  return true;
}

function showCard(o) {
  currentArt = o;
  const isNew = (Date.now()/1000 - (o.ts||0)) < 120;
  const stage = document.getElementById('stage');

  const img = o.photo_url
    ? `<img class="card-img" src="${o.photo_url}" loading="eager" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
    : '';
  const placeholder = `<div class="card-img-placeholder" style="${o.photo_url?'display:none':''}">🏷️</div>`;

  stage.innerHTML = `
    <div class="card">
      <div class="card-img-wrap">
        ${img}${placeholder}
        ${isNew ? '<span class="badge-new">NOUVEAU</span>' : ''}
        <span class="badge-cat">${o.categorie||''}</span>
      </div>
      <div class="card-body">
        <div class="card-top">
          <div class="card-titre">${o.titre||''}</div>
          <div class="card-prix-big">${o.prix}€</div>
        </div>
        <div class="card-meta">
          ${o.marque ? `<span class="meta-pill marque">${o.marque}</span>` : ''}
          ${o.taille ? `<span class="meta-pill">${o.taille}</span>` : ''}
        </div>
        ${o.nb_favoris ? `<div class="card-fav">❤️ ${o.nb_favoris} favoris</div>` : ''}
        <div class="card-actions">
          <a class="btn-buy" href="${o.url}/buy" target="_blank">💳 Acheter</a>
          <div class="btn-skip" onclick="skipCard()">⏭ Passer</div>
        </div>
      </div>
    </div>`;

  viewed++;
  document.getElementById('counter').textContent = viewed + ' vus';
  startProgress();
}

function nextCard() {
  let art = null;
  while (buffer.length > 0) {
    art = buffer.shift();
    if (matchFilters(art)) break;
    art = null;
  }
  if (art) {
    showCard(art);
  } else {
    document.getElementById('stage').innerHTML = `<div class="waiting"><div class="waiting-icon">⏳</div>En attente de nouveaux articles...</div>`;
    document.getElementById('progressFill').style.width = '100%';
  }
}

function skipCard() {
  clearTimeout(autoTimer);
  document.getElementById('progressFill').style.transition = 'none';
  document.getElementById('progressFill').style.width = '100%';
  nextCard();
}

function scheduleNext(immediate) {
  clearTimeout(autoTimer);
  if (paused) return;
  autoTimer = setTimeout(() => nextCard(), immediate ? 0 : speed);
}

function startProgress() {
  const bar = document.getElementById('progressFill');
  bar.style.transition = 'none';
  bar.style.width = '100%';
  setTimeout(() => {
    bar.style.transition = `width ${speed}ms linear`;
    bar.style.width = '0%';
  }, 50);
  scheduleNext(false);
}

// Toggle pause au clic sur la scène
document.getElementById('stage').addEventListener('click', function(e) {
  if (e.target.classList.contains('btn-skip') || e.target.classList.contains('btn-buy')) return;
  paused = !paused;
  const banner = document.getElementById('pausedBanner');
  banner.classList.toggle('show', paused);
  if (!paused) {
    banner.classList.remove('show');
    scheduleNext(false);
  } else {
    clearTimeout(autoTimer);
    const bar = document.getElementById('progressFill');
    bar.style.transition = 'none';
  }
});

// SSE
function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource('/api/stream');
  sseSource.onopen = () => {
    document.getElementById('liveDot').classList.add('on');
    document.getElementById('liveText').textContent = 'En direct';
  };
  sseSource.onmessage = (e) => {
    if (!e.data || e.data === '{}') return;
    try {
      const o = JSON.parse(e.data);
      if (!o.id || seenIds.has(o.id)) return;
      seenIds.add(o.id);
      o.ts = Date.now()/1000;
      if (matchFilters(o)) buffer.push(o);
      // Si rien n'est affiché, démarrer
      if (!currentArt || document.querySelector('.waiting')) nextCard();
    } catch(e) {}
  };
  sseSource.onerror = () => {
    document.getElementById('liveDot').classList.remove('on');
    document.getElementById('liveText').textContent = 'Reconnexion...';
    sseSource.close();
    setTimeout(connectSSE, 3000);
  };
}

// Charger articles initiaux
async function loadInitial() {
  try {
    const d = await fetch('/api/feed?limit=100').then(r => r.json());
    (d.articles||[]).forEach(o => {
      if (!seenIds.has(o.id)) { seenIds.add(o.id); buffer.push(o); }
    });
    nextCard();
  } catch(e) {}
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
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/feed")
def api_feed():
    try:
        limit = int(request.args.get("limit", 100))
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

import os, time, sqlite3, requests, threading, json, random, queue
from datetime import datetime
from flask import Flask, jsonify, render_template_string, Response, stream_with_context, request

app = Flask(__name__)
DB = os.path.join("/tmp", "vinted_feed.db")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

CATEGORIES = [
    {"nom": "Vêtements femme",   "id": 1904},
    {"nom": "Vêtements homme",   "id": 4},
    {"nom": "Chaussures femme",  "id": 16},
    {"nom": "Chaussures homme",  "id": 812},
    {"nom": "Sacs",              "id": 3},
    {"nom": "Accessoires",       "id": 2},
    {"nom": "Sport",             "id": 77},
    {"nom": "Électronique",      "id": 2225},
    {"nom": "Maison",            "id": 1560},
    {"nom": "Jeux vidéo",        "id": 1194},
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

# Queue SSE
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
                prix   = float(item.get("price", {}).get("amount", 0) if isinstance(item.get("price"), dict) else item.get("price", 0))
                photos = item.get("photos", [])
                photo_url = ""
                if photos:
                    photo_url = photos[0].get("url", "") or photos[0].get("full_size_url", "")

                art = {
                    "id":            iid,
                    "titre":         item.get("title", ""),
                    "marque":        marque,
                    "prix":          prix,
                    "categorie":     cat["nom"],
                    "taille":        item.get("size_title", ""),
                    "nb_favoris":    item.get("favourite_count", 0),
                    "url":           f"https://www.vinted.fr/items/{iid}",
                    "photo_url":     photo_url,
                    "date_scraping": datetime.now().isoformat(),
                    "ts":            int(datetime.now().timestamp()),
                }
                sauvegarder(art)
                try: feed_queue.put_nowait(art)
                except queue.Full:
                    try: feed_queue.get_nowait()
                    except: pass
                    try: feed_queue.put_nowait(art)
                    except: pass

        except Exception as e:
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
  --text:#f0f0f8;--text2:#7070a0;--accent:#7c6af7;--green:#00d68f;
  --red:#ff4d6d;--yellow:#ffd166;
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* HEADER */
.header{padding:12px 16px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;flex-shrink:0}
.logo{font-size:18px;font-weight:700;letter-spacing:-.5px}
.logo em{color:var(--accent);font-style:normal}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--border);flex-shrink:0;transition:background .3s}
.live-dot.on{background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.counter{font-size:12px;color:var(--text2);margin-left:auto}

/* FILTRES */
.filters{padding:10px 12px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;gap:8px;flex-wrap:wrap;flex-shrink:0}
.filter-select{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:20px;font-size:12px;cursor:pointer;outline:none}
.filter-select:focus{border-color:var(--accent)}
.btn-reset{background:transparent;border:1px solid var(--border);color:var(--text2);padding:6px 12px;border-radius:20px;font-size:12px;cursor:pointer}
.btn-reset:hover{border-color:var(--accent);color:var(--accent)}
.btn-notif{background:transparent;border:1px solid var(--border);color:var(--text2);padding:6px 12px;border-radius:20px;font-size:12px;cursor:pointer;margin-left:auto}
.btn-notif.on{border-color:var(--green);color:var(--green)}

/* FEED */
.feed{flex:1;overflow-y:auto;padding:12px;display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;align-content:start}
.feed::-webkit-scrollbar{width:4px}
.feed::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* CARTE */
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;animation:slideIn .3s ease;cursor:pointer;transition:transform .15s,border-color .15s}
.card:hover{transform:translateY(-2px);border-color:var(--accent)}
.card.new{border-color:var(--green)}
@keyframes slideIn{from{opacity:0;transform:translateY(-12px)}to{opacity:1;transform:translateY(0)}}
.card-img{width:100%;height:160px;object-fit:cover;display:block;background:var(--surface2)}
.card-img-placeholder{width:100%;height:160px;background:var(--surface2);display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--text2)}
.card-badges{position:absolute;top:8px;left:8px;right:8px;display:flex;justify-content:space-between;pointer-events:none}
.card-img-wrap{position:relative}
.badge{font-size:10px;font-weight:700;padding:3px 8px;border-radius:20px}
.badge-new{background:var(--green);color:#000}
.badge-eco{color:#000}
.card-body{padding:10px;flex:1;display:flex;flex-direction:column;gap:3px}
.card-cat{font-size:10px;color:var(--text2)}
.card-titre{font-size:12px;font-weight:500;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;line-height:1.3}
.card-marque{font-size:11px;font-weight:600;color:var(--accent)}
.card-bottom{display:flex;align-items:center;justify-content:space-between;margin-top:4px}
.card-prix{font-size:16px;font-weight:700}
.card-taille{font-size:11px;background:var(--surface2);padding:2px 7px;border-radius:20px}
.card-fav{font-size:11px;color:var(--text2)}
.card-btn{display:block;margin:8px 10px 10px;background:var(--accent);color:#fff;font-size:12px;font-weight:600;padding:7px;border-radius:8px;text-decoration:none;text-align:center;transition:opacity .15s}
.card-btn:hover{opacity:.85}

/* EMPTY */
.empty{grid-column:1/-1;text-align:center;padding:60px 20px;color:var(--text2)}
.empty-icon{font-size:48px;margin-bottom:12px}
.empty-text{font-size:14px}

/* SCROLL TO TOP */
.scroll-top{position:fixed;bottom:20px;right:20px;width:40px;height:40px;border-radius:50%;background:var(--accent);color:#fff;border:none;cursor:pointer;font-size:18px;display:none;align-items:center;justify-content:center;box-shadow:0 4px 12px rgba(124,106,247,.4)}
.scroll-top.show{display:flex}
</style>
</head><body>

<div class="header">
  <div class="logo">Vinted<em>Feed</em></div>
  <div class="live-dot" id="liveDot"></div>
  <span id="liveText" style="font-size:12px;color:var(--text2)">Connexion...</span>
  <span class="counter" id="counter">0 articles</span>
</div>

<div class="filters">
  <select class="filter-select" id="fCat" onchange="applyFilters()">
    <option value="">Toutes catégories</option>
    <option>Vêtements femme</option>
    <option>Vêtements homme</option>
    <option>Chaussures femme</option>
    <option>Chaussures homme</option>
    <option>Sacs</option>
    <option>Accessoires</option>
    <option>Sport</option>
    <option>Électronique</option>
    <option>Maison</option>
    <option>Jeux vidéo</option>
    <option>Livres</option>
    <option>Enfants</option>
  </select>

  <select class="filter-select" id="fTaille" onchange="applyFilters()">
    <option value="">Toutes tailles</option>
    <optgroup label="Vêtements"><option>XS</option><option>S</option><option>M</option><option>L</option><option>XL</option><option>XXL</option></optgroup>
    <optgroup label="Chaussures"><option>36</option><option>37</option><option>38</option><option>39</option><option>40</option><option>41</option><option>42</option><option>43</option><option>44</option><option>45</option></optgroup>
  </select>

  <select class="filter-select" id="fPrix" onchange="applyFilters()">
    <option value="">Tous les prix</option>
    <option value="0,10">Moins de 10€</option>
    <option value="0,20">Moins de 20€</option>
    <option value="0,50">Moins de 50€</option>
    <option value="10,30">10€ — 30€</option>
    <option value="20,50">20€ — 50€</option>
    <option value="50,200">Plus de 50€</option>
  </select>

  <select class="filter-select" id="fMarque" onchange="applyFilters()">
    <option value="">Toutes marques</option>
    <option>Nike</option><option>Adidas</option><option>Jordan</option>
    <option>New Balance</option><option>Puma</option><option>Converse</option>
    <option>Vans</option><option>The North Face</option><option>Patagonia</option>
    <option>Levi's</option><option>Zara</option><option>H&M</option>
    <option>Ralph Lauren</option><option>Tommy Hilfiger</option><option>Lacoste</option>
    <option>Stone Island</option><option>Supreme</option><option>Carhartt</option>
    <option>Apple</option><option>Samsung</option><option>Sony</option><option>Nintendo</option>
    <option>Louis Vuitton</option><option>Gucci</option><option>Balenciaga</option>
  </select>

  <button class="btn-reset" onclick="resetFilters()">Reset</button>
  <button class="btn-notif" id="btnNotif" onclick="toggleNotif()">🔔 Alertes</button>
</div>

<div class="feed" id="feed">
  <div class="empty">
    <div class="empty-icon">⚡</div>
    <div class="empty-text">Le feed démarre...<br>Les articles vont apparaître automatiquement</div>
  </div>
</div>

<button class="scroll-top" id="scrollTop" onclick="scrollToTop()">↑</button>

<script>
let totalCount = 0;
let notifEnabled = false;
let filters = {cat:'', taille:'', prixMin:0, prixMax:0, marque:''};
let sseSource = null;
let reconnectTimer = null;

// ── FILTRES ───────────────────────────────────────────────────────────────────
function applyFilters() {
  const prixVal = document.getElementById('fPrix').value;
  filters.cat    = document.getElementById('fCat').value;
  filters.taille = document.getElementById('fTaille').value;
  filters.marque = document.getElementById('fMarque').value;
  filters.prixMin = prixVal ? parseFloat(prixVal.split(',')[0]) : 0;
  filters.prixMax = prixVal ? parseFloat(prixVal.split(',')[1]) : 0;
}

function resetFilters() {
  document.getElementById('fCat').value = '';
  document.getElementById('fTaille').value = '';
  document.getElementById('fPrix').value = '';
  document.getElementById('fMarque').value = '';
  filters = {cat:'', taille:'', prixMin:0, prixMax:0, marque:''};
}

function matchFilters(o) {
  if (filters.cat && o.categorie !== filters.cat) return false;
  if (filters.taille && !o.taille.includes(filters.taille)) return false;
  if (filters.marque && !(o.marque||'').toLowerCase().includes(filters.marque.toLowerCase())) return false;
  if (filters.prixMin && o.prix < filters.prixMin) return false;
  if (filters.prixMax && o.prix > filters.prixMax) return false;
  return true;
}

// ── CARTE ────────────────────────────────────────────────────────────────────
function makeCard(o, isNew) {
  const img = o.photo_url
    ? `<img class="card-img" src="${o.photo_url}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
    : '';
  const placeholder = `<div class="card-img-placeholder" style="${o.photo_url?'display:none':''}">📷</div>`;
  const newBadge = isNew ? `<span class="badge badge-new">NOUVEAU</span>` : `<span></span>`;
  const ecoColor = o.eco > 25 ? '#ff4d6d' : o.eco > 10 ? '#ffd166' : '#00d68f';
  const ecoBadge = o.eco > 0 ? `<span class="badge badge-eco" style="background:${ecoColor}">-${o.eco}%</span>` : `<span></span>`;

  const card = document.createElement('div');
  card.className = 'card' + (isNew ? ' new' : '');
  card.dataset.id = o.id;
  card.innerHTML = `
    <div class="card-img-wrap">
      ${img}${placeholder}
      <div class="card-badges">${newBadge}${ecoBadge}</div>
    </div>
    <div class="card-body">
      <div class="card-cat">${o.categorie||''}</div>
      <div class="card-titre">${o.titre||''}</div>
      ${o.marque && o.marque !== '—' ? `<div class="card-marque">${o.marque}</div>` : ''}
      <div class="card-bottom">
        <span class="card-prix">${o.prix}€</span>
        ${o.taille ? `<span class="card-taille">${o.taille}</span>` : ''}
      </div>
      ${o.nb_favoris ? `<div class="card-fav">❤️ ${o.nb_favoris}</div>` : ''}
    </div>
    <a class="card-btn" href="${o.url}" target="_blank">Voir sur Vinted →</a>`;

  // Retirer le badge NOUVEAU après 30s
  if (isNew) {
    setTimeout(() => {
      card.classList.remove('new');
      const badge = card.querySelector('.badge-new');
      if (badge) badge.style.display = 'none';
    }, 30000);
  }

  return card;
}

function addCard(o, isNew) {
  if (!matchFilters(o)) return;
  const feed = document.getElementById('feed');
  // Retirer le message empty si présent
  const empty = feed.querySelector('.empty');
  if (empty) feed.innerHTML = '';

  const card = makeCard(o, isNew);
  feed.prepend(card);
  totalCount++;
  document.getElementById('counter').textContent = totalCount.toLocaleString('fr-FR') + ' articles';

  // Notification push
  if (notifEnabled && isNew && Notification.permission === 'granted') {
    const n = new Notification(`${o.marque || o.categorie} — ${o.prix}€`, {
      body: o.titre,
      tag: o.id,
    });
    n.onclick = () => { window.open(o.url, '_blank'); n.close(); };
    setTimeout(() => n.close(), 6000);
  }

  // Limiter à 200 cartes
  while (feed.children.length > 200) feed.removeChild(feed.lastChild);
}

// ── SSE ──────────────────────────────────────────────────────────────────────
function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource('/api/stream');

  sseSource.onopen = () => {
    document.getElementById('liveDot').classList.add('on');
    document.getElementById('liveText').textContent = 'En direct';
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  };

  sseSource.onmessage = (e) => {
    if (!e.data || e.data === '{}') return;
    try {
      const o = JSON.parse(e.data);
      if (!o.id) return;
      addCard(o, true);
    } catch(err) {}
  };

  sseSource.onerror = () => {
    document.getElementById('liveDot').classList.remove('on');
    document.getElementById('liveText').textContent = 'Reconnexion...';
    sseSource.close();
    reconnectTimer = setTimeout(connectSSE, 3000);
  };
}

// ── NOTIFS ───────────────────────────────────────────────────────────────────
function toggleNotif() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    notifEnabled = !notifEnabled;
  } else {
    Notification.requestPermission().then(p => {
      if (p === 'granted') notifEnabled = true;
    });
  }
  const btn = document.getElementById('btnNotif');
  btn.textContent = notifEnabled ? '🔔 Alertes ON' : '🔔 Alertes';
  btn.classList.toggle('on', notifEnabled);
}

// ── SCROLL ───────────────────────────────────────────────────────────────────
const feed = document.getElementById('feed');
feed.addEventListener('scroll', () => {
  document.getElementById('scrollTop').classList.toggle('show', feed.scrollTop > 300);
});
function scrollToTop() { feed.scrollTo({top:0, behavior:'smooth'}); }

// ── INIT ─────────────────────────────────────────────────────────────────────
// Charger les derniers articles au démarrage
async function loadInitial() {
  try {
    const d = await fetch('/api/feed?limit=40').then(r => r.json());
    (d.articles || []).reverse().forEach(o => addCard(o, false));
  } catch(e) {}
}

loadInitial();
connectSSE();
</script>
</body></html>"""

# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/stream")
def api_stream():
    def generate():
        ping = "data: {}" + chr(10) + chr(10)
        yield ping
        while True:
            try:
                art = feed_queue.get(timeout=20)
                line = "data: " + json.dumps(art, ensure_ascii=False) + chr(10) + chr(10)
                yield line
            except queue.Empty:
                yield ": ping" + chr(10) + chr(10)
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.route("/api/feed")
def api_feed():
    try:
        limit  = int(request.args.get("limit", 40))
        cat    = request.args.get("cat", "").strip()
        marque = request.args.get("marque", "").strip()
        taille = request.args.get("taille", "").strip()
        prix_min = request.args.get("prix_min", "").strip()
        prix_max = request.args.get("prix_max", "").strip()

        where = "WHERE 1=1"
        params = []
        if cat:     where += " AND categorie=?"; params.append(cat)
        if marque:  where += " AND LOWER(marque) LIKE LOWER(?)"; params.append(f"%{marque}%")
        if taille:  where += " AND taille LIKE ?"; params.append(f"%{taille}%")
        if prix_min: where += " AND prix>=?"; params.append(float(prix_min))
        if prix_max: where += " AND prix<=?"; params.append(float(prix_max))

        c = sqlite3.connect(DB)
        rows = c.execute(f"""
            SELECT id,titre,marque,prix,categorie,taille,nb_favoris,url,photo_url
            FROM articles {where}
            ORDER BY date_scraping DESC LIMIT ?
        """, params + [limit]).fetchall()
        c.close()

        articles = [{"id":r[0],"titre":r[1],"marque":r[2],"prix":r[3],"categorie":r[4],
                     "taille":r[5],"nb_favoris":r[6],"url":r[7],"photo_url":r[8],"eco":0} for r in rows]
        return jsonify({"articles": articles})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/stats")
def api_stats():
    try:
        c = sqlite3.connect(DB)
        total = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        c.close()
        return jsonify({"total": total, "proxies": len(PROXIES)})
    except:
        return jsonify({"total": 0})

# ── INIT ──────────────────────────────────────────────────────────────────────
init_db()
threading.Thread(target=start_scanner, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

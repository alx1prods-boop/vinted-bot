import os, time, sqlite3, requests, threading, json
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string
from collections import defaultdict

app = Flask(__name__)
DB = os.path.join("/tmp", "vinted_v2.db")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── CATÉGORIES — toutes, sans filtre ────────────────────────────────────────
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

SEUIL_SOUS_COTE = 0.70  # alerte si prix < 70% du prix moyen de la niche

# ── BASE DE DONNÉES ──────────────────────────────────────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS articles (
        id TEXT PRIMARY KEY,
        titre TEXT, marque TEXT, prix REAL,
        categorie TEXT, taille TEXT,
        nb_favoris INTEGER, nb_vues INTEGER,
        date_publication TEXT, date_scraping TEXT,
        url TEXT, vendu INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS alertes_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        titre TEXT, marque TEXT, prix REAL, prix_moyen_niche REAL,
        economie_pct INTEGER, categorie TEXT, url TEXT, date TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS scans_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, nb_nouveaux INTEGER, nb_total INTEGER)""")
    c.commit()
    c.close()

# ── SESSION ──────────────────────────────────────────────────────────────────
def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Origin": "https://www.vinted.fr",
        "Referer": "https://www.vinted.fr/",
    })
    try:
        s.get("https://www.vinted.fr", timeout=10)
    except:
        pass
    return s

# ── TELEGRAM ─────────────────────────────────────────────────────────────────
def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5)
    except:
        pass

# ── ANALYSE DES NICHES ───────────────────────────────────────────────────────
def calculer_niches():
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("""
            SELECT marque, categorie, prix, nb_favoris, nb_vues, date_scraping
            FROM articles
            WHERE marque != '' AND marque IS NOT NULL AND prix > 1
        """).fetchall()
        c.close()
    except:
        return []

    # Agrégation par (marque, categorie)
    data = defaultdict(lambda: {"prix": [], "favoris": [], "vues": [], "dates": []})
    for marque, categorie, prix, fav, vues, date in rows:
        key = (marque.strip(), categorie)
        data[key]["prix"].append(prix)
        data[key]["favoris"].append(fav or 0)
        data[key]["vues"].append(vues or 0)
        if date:
            data[key]["dates"].append(date)

    niches = []
    for (marque, categorie), v in data.items():
        if len(v["prix"]) < 2:
            continue
        nb       = len(v["prix"])
        prix_moy = round(sum(v["prix"]) / nb, 2)
        prix_min = round(min(v["prix"]), 2)
        prix_max = round(max(v["prix"]), 2)
        fav_moy  = round(sum(v["favoris"]) / nb, 1)
        vues_moy = round(sum(v["vues"]) / nb, 1)

        # Score = popularité × prix (indicateur de demande × valeur)
        score = round(fav_moy * prix_moy, 1)

        # Vélocité : articles ajoutés dans les dernières 24h
        recents = 0
        seuil   = (datetime.now() - timedelta(hours=24)).isoformat()
        for d in v["dates"]:
            if d > seuil:
                recents += 1
        velocite = round((recents / nb) * 100, 1)

        niches.append({
            "marque": marque, "categorie": categorie,
            "nb": nb, "prix_moy": prix_moy, "prix_min": prix_min, "prix_max": prix_max,
            "fav_moy": fav_moy, "vues_moy": vues_moy,
            "score": score, "velocite": velocite,
        })

    niches.sort(key=lambda x: x["score"], reverse=True)
    return niches[:50]

def prix_moyen_par_categorie():
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("""
            SELECT categorie, ROUND(AVG(prix),2), COUNT(*), ROUND(AVG(nb_favoris),1)
            FROM articles WHERE prix > 1
            GROUP BY categorie ORDER BY AVG(prix) DESC
        """).fetchall()
        c.close()
        return [{"cat": r[0], "prix_moy": r[1], "nb": r[2], "fav_moy": r[3]} for r in rows]
    except:
        return []

def top_marques():
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("""
            SELECT marque, COUNT(*) as nb, ROUND(AVG(prix),2), ROUND(AVG(nb_favoris),1)
            FROM articles
            WHERE marque != '' AND marque IS NOT NULL AND prix > 1
            GROUP BY marque HAVING nb >= 2
            ORDER BY AVG(nb_favoris) * AVG(prix) DESC
            LIMIT 20
        """).fetchall()
        c.close()
        return [{"marque": r[0], "nb": r[1], "prix_moy": r[2], "fav_moy": r[3]} for r in rows]
    except:
        return []

def tendances_7j():
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("""
            SELECT DATE(date_scraping) as jour, COUNT(*) as nb, ROUND(AVG(prix),2)
            FROM articles
            WHERE date_scraping >= datetime('now', '-7 days')
            GROUP BY jour ORDER BY jour ASC
        """).fetchall()
        c.close()
        return [{"jour": r[0], "nb": r[1], "prix_moy": r[2]} for r in rows]
    except:
        return []

def mots_cles():
    import re
    STOP = {"de","du","la","le","les","un","une","des","en","et","pour","avec","au","aux",
            "par","sur","dans","pas","très","taille","neuf","état","bon","bonne","comme",
            "noir","blanc","bleu","rouge","vert","gris","beige","s","m","l","xl","xxl","xs",
            "homme","femme","fille","garcon","enfant","adulte","ou","qui","que","se","sa",
            "son","ses","ce","cet","cette","au","aux","ne","ni","si","car"}
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("""
            SELECT titre FROM articles
            WHERE nb_favoris >= 1
            ORDER BY nb_favoris DESC LIMIT 1000
        """).fetchall()
        c.close()
    except:
        return []
    from collections import Counter
    mots = []
    for (titre,) in rows:
        if titre:
            tokens = re.findall(r'\b[a-zA-ZÀ-ÿ]{3,}\b', titre.lower())
            mots.extend([t for t in tokens if t not in STOP])
    return [{"mot": m, "count": n} for m, n in Counter(mots).most_common(30)]

# ── ALERTES INTELLIGENTES ────────────────────────────────────────────────────
def verifier_sous_cote(article, prix_moyen_niche):
    if not prix_moyen_niche or prix_moyen_niche == 0:
        return False
    return article["prix"] <= prix_moyen_niche * SEUIL_SOUS_COTE

# ── SCRAPER ──────────────────────────────────────────────────────────────────
def scraper_categorie(session, cat, ids_vus, prix_moyens):
    nouveaux = []
    params = {
        "catalog_ids": cat["id"],
        "page": 1, "per_page": 96,
        "order": "newest_first",
    }
    try:
        r = session.get("https://www.vinted.fr/api/v2/catalog/items", params=params, timeout=12)
        items = r.json().get("items", [])
    except:
        return []

    c = sqlite3.connect(DB)
    for item in items:
        iid = str(item.get("id", ""))
        if iid in ids_vus:
            continue
        marque = item.get("brand_title", "") or item.get("brand", "")
        prix   = float(item.get("price", {}).get("amount", 0) if isinstance(item.get("price"), dict) else item.get("price", 0))
        art = {
            "id":               iid,
            "titre":            item.get("title", ""),
            "marque":           marque,
            "prix":             prix,
            "categorie":        cat["nom"],
            "taille":           item.get("size_title", ""),
            "nb_favoris":       item.get("favourite_count", 0),
            "nb_vues":          item.get("view_count", 0),
            "date_publication": item.get("created_at_ts", ""),
            "date_scraping":    datetime.now().isoformat(),
            "url":              f"https://www.vinted.fr/items/{iid}",
        }
        try:
            c.execute("""INSERT OR IGNORE INTO articles
                (id,titre,marque,prix,categorie,taille,nb_favoris,nb_vues,date_publication,date_scraping,url,vendu)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
                (art["id"],art["titre"],art["marque"],art["prix"],art["categorie"],
                 art["taille"],art["nb_favoris"],art["nb_vues"],art["date_publication"],
                 art["date_scraping"],art["url"]))
            if c.total_changes > len(nouveaux):
                nouveaux.append(art)
                ids_vus.add(iid)
        except:
            pass

        # Alerte sous-cotée
        cle_niche = f"{marque}_{cat['nom']}"
        prix_moy  = prix_moyens.get(cle_niche, 0)
        if verifier_sous_cote(art, prix_moy) and art["nb_favoris"] >= 1:
            economie = round((1 - prix / prix_moy) * 100) if prix_moy else 0
            try:
                c.execute("""INSERT INTO alertes_log
                    (titre,marque,prix,prix_moyen_niche,economie_pct,categorie,url,date)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (art["titre"],marque,prix,prix_moy,economie,cat["nom"],art["url"],
                     datetime.now().strftime("%d/%m %H:%M")))
            except:
                pass
            telegram(
                f"<b>Bonne affaire -{economie}%</b>\n"
                f"{art['titre']}\n"
                f"{marque} | {prix}€ (moy: {prix_moy}€)\n"
                f"{art['url']}"
            )

    c.commit()
    c.close()
    return nouveaux

def build_prix_moyens():
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("""
            SELECT marque, categorie, AVG(prix)
            FROM articles WHERE marque != '' AND prix > 1
            GROUP BY marque, categorie
        """).fetchall()
        c.close()
        return {f"{r[0]}_{r[1]}": round(r[2], 2) for r in rows}
    except:
        return {}

def scanner():
    time.sleep(3)
    session   = get_session()
    ids_vus   = set()
    try:
        c = sqlite3.connect(DB)
        ids_vus = set(x[0] for x in c.execute("SELECT id FROM articles"))
        c.close()
    except:
        pass

    while True:
        prix_moyens  = build_prix_moyens()
        total_nouveaux = 0
        for cat in CATEGORIES:
            nouveaux = scraper_categorie(session, cat, ids_vus, prix_moyens)
            total_nouveaux += len(nouveaux)
            time.sleep(2)

        # Log du scan
        try:
            c = sqlite3.connect(DB)
            nb_total = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            c.execute("INSERT INTO scans_log (date,nb_nouveaux,nb_total) VALUES (?,?,?)",
                (datetime.now().isoformat(), total_nouveaux, nb_total))
            c.commit()
            c.close()
        except:
            pass

        time.sleep(120)

# ── HTML ─────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html><html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VintedBot — Market Analysis</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d0d12;--surface:#14141c;--surface2:#1c1c28;--border:#2a2a3a;--text:#e8e8f4;--text2:#8888aa;--accent:#7c6af7;--green:#00d68f;--red:#ff4d6d;--yellow:#ffd166;--blue:#4d9fff}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px}
.header{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.logo{font-size:17px;font-weight:700;letter-spacing:-.5px}.logo em{color:var(--accent);font-style:normal}
.header-right{display:flex;align-items:center;gap:12px}
.dot-live{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.status-text{font-size:12px;color:var(--text2)}
.nav{display:flex;gap:4px;background:var(--surface2);padding:4px;border-radius:10px}
.nav-btn{padding:6px 14px;border-radius:7px;border:none;cursor:pointer;font-size:12px;font-weight:500;background:transparent;color:var(--text2);transition:all .15s}
.nav-btn.active{background:var(--surface);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.4)}
.page{display:none;padding:20px 24px;max-width:1200px;margin:0 auto}.page.active{display:block}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:20px}
.metric{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px}
.metric-label{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.metric-val{font-size:24px;font-weight:700}
.metric-sub{font-size:11px;color:var(--green);margin-top:4px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}
.card-title{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
table{width:100%;border-collapse:collapse}
th{font-size:11px;color:var(--text2);text-align:left;padding:0 8px 8px 0;border-bottom:1px solid var(--border)}
td{padding:7px 8px 7px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:13px;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.score-bar{height:4px;border-radius:2px;background:var(--surface2);width:80px;display:inline-block;vertical-align:middle;overflow:hidden}
.score-fill{height:4px;border-radius:2px}
.tag{display:inline-block;font-size:10px;padding:2px 7px;border-radius:20px;font-weight:600}
.tag-hot{background:rgba(255,77,109,.15);color:var(--red)}
.tag-warm{background:rgba(255,209,102,.15);color:var(--yellow)}
.tag-ok{background:rgba(0,214,143,.15);color:var(--green)}
.tag-blue{background:rgba(77,159,255,.15);color:var(--blue)}
.alert-item{display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.alert-item:last-child{border-bottom:none}
.alert-dot{width:7px;height:7px;border-radius:50%;margin-top:4px;flex-shrink:0}
.alert-dot-new{background:var(--green)}
.alert-dot-old{background:var(--border)}
.alert-title{font-size:13px;font-weight:500;margin-bottom:2px}
.alert-meta{font-size:11px;color:var(--text2)}
.alert-badge{font-size:11px;font-weight:700;color:var(--green);margin-left:auto;flex-shrink:0;padding-top:2px}
a.voir{font-size:11px;color:var(--blue);text-decoration:none}
a.voir:hover{text-decoration:underline}
.fullrow{grid-column:1/-1}
.chart-wrap{position:relative;width:100%}
.empty{font-size:13px;color:var(--text2);padding:12px 0}
.kw-cloud{display:flex;flex-wrap:wrap;gap:6px;padding-top:4px}
.kw-tag{font-size:12px;padding:4px 10px;border-radius:20px;cursor:default}
.rank-badge{width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;flex-shrink:0}
@media(max-width:640px){.grid2{grid-template-columns:1fr}.header{flex-wrap:wrap;gap:8px}}
</style></head><body>

<div class="header">
  <div class="logo">Vinted<em>Bot</em> <span style="font-size:11px;font-weight:400;color:var(--text2);margin-left:6px;">Market Analysis</span></div>
  <div class="header-right">
    <div style="display:flex;align-items:center;gap:6px"><div class="dot-live" id="liveDot" style="background:var(--border)"></div><span class="status-text" id="statusText">Connexion...</span></div>
    <div class="nav">
      <button class="nav-btn active" onclick="showPage('dashboard',this)">Vue générale</button>
      <button class="nav-btn" onclick="showPage('niches',this)">Niches</button>
      <button class="nav-btn" onclick="showPage('marques',this)">Marques</button>
      <button class="nav-btn" onclick="showPage('alertes',this)">Alertes</button>
    </div>
  </div>
</div>

<!-- PAGE DASHBOARD -->
<div class="page active" id="page-dashboard">
  <div class="metrics" id="metricsRow">
    <div class="metric"><div class="metric-label">Articles collectés</div><div class="metric-val" id="mArt">—</div><div class="metric-sub" id="mArtSub">—</div></div>
    <div class="metric"><div class="metric-label">Niches détectées</div><div class="metric-val" id="mNiches">—</div><div class="metric-sub">marque × catégorie</div></div>
    <div class="metric"><div class="metric-label">Alertes envoyées</div><div class="metric-val" id="mAlerts">—</div><div class="metric-sub">sous-cotées -30%</div></div>
    <div class="metric"><div class="metric-label">Prix moyen global</div><div class="metric-val" id="mPrix">—</div><div class="metric-sub" id="mPrixSub">—</div></div>
    <div class="metric"><div class="metric-label">Dernier scan</div><div class="metric-val" style="font-size:15px;" id="mScan">—</div><div class="metric-sub" id="mScanSub">—</div></div>
  </div>
  <div class="grid2">
    <div class="card">
      <div class="card-title">Top niches (score rentabilité)</div>
      <table><thead><tr><th>#</th><th>Niche</th><th>Score</th><th>Prix moy</th></tr></thead>
      <tbody id="topNichesTable"><tr><td colspan="4" class="empty">En attente des données...</td></tr></tbody></table>
    </div>
    <div class="card">
      <div class="card-title">Dernières alertes sous-cotées</div>
      <div id="dashAlerts"><div class="empty">Aucune alerte encore.</div></div>
    </div>
    <div class="card">
      <div class="card-title">Articles collectés / jour</div>
      <div class="chart-wrap" style="height:200px"><canvas id="chartTendance" role="img" aria-label="Articles collectés par jour">Tendance collecte</canvas></div>
    </div>
    <div class="card">
      <div class="card-title">Prix moyen par catégorie</div>
      <div class="chart-wrap" style="height:200px"><canvas id="chartPrix" role="img" aria-label="Prix moyen par catégorie">Prix par catégorie</canvas></div>
    </div>
    <div class="card fullrow">
      <div class="card-title">Mots-clés tendance (titres populaires)</div>
      <div class="kw-cloud" id="kwCloud"><span class="empty">En attente...</span></div>
    </div>
  </div>
</div>

<!-- PAGE NICHES -->
<div class="page" id="page-niches">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <div style="font-size:16px;font-weight:600;">Analyse des niches</div>
    <select id="nicheCatFilter" onchange="filterNiches()" style="background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:7px;font-size:12px;">
      <option value="">Toutes les catégories</option>
    </select>
  </div>
  <div class="card">
    <table><thead><tr>
      <th>#</th><th>Marque</th><th>Catégorie</th><th>Articles</th>
      <th>Prix moy</th><th>Favoris moy</th><th>Vélocité</th><th>Score</th>
    </tr></thead>
    <tbody id="nichesFullTable"><tr><td colspan="8" class="empty">En attente des données...</td></tr></tbody></table>
  </div>
</div>

<!-- PAGE MARQUES -->
<div class="page" id="page-marques">
  <div style="font-size:16px;font-weight:600;margin-bottom:14px">Top marques</div>
  <div class="grid2">
    <div class="card">
      <div class="card-title">Classement par score</div>
      <div class="chart-wrap" style="height:320px"><canvas id="chartMarques" role="img" aria-label="Top marques par score">Top marques</canvas></div>
    </div>
    <div class="card">
      <div class="card-title">Détail</div>
      <table><thead><tr><th>#</th><th>Marque</th><th>Articles</th><th>Prix moy</th><th>❤️ moy</th></tr></thead>
      <tbody id="marquesTable"><tr><td colspan="5" class="empty">En attente...</td></tr></tbody></table>
    </div>
  </div>
</div>

<!-- PAGE ALERTES -->
<div class="page" id="page-alertes">
  <div style="font-size:16px;font-weight:600;margin-bottom:14px">Alertes intelligentes <span style="font-size:12px;font-weight:400;color:var(--text2);">— articles sous-cotés vs prix moyen de leur niche</span></div>
  <div class="card">
    <div id="alertesFullList"><div class="empty">Aucune alerte encore. Le bot envoie une alerte quand un article est vendu -30% sous le prix moyen de sa niche.</div></div>
  </div>
</div>

<script>
let DATA = {};
let nichesAll = [];
let marquesChartInst = null;

function showPage(id, btn) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('page-' + id).classList.add('active');
  btn.classList.add('active');
}

function scoreColor(s, max) {
  const pct = max > 0 ? s / max : 0;
  if (pct > 0.7) return '#ff4d6d';
  if (pct > 0.4) return '#ffd166';
  return '#00d68f';
}

function renderTopNiches(niches) {
  const tb = document.getElementById('topNichesTable');
  if (!niches || !niches.length) { tb.innerHTML = '<tr><td colspan="4" class="empty">Pas encore assez de données. Le bot collecte...</td></tr>'; return; }
  const max = niches[0].score;
  tb.innerHTML = niches.slice(0, 10).map((n, i) => {
    const col = scoreColor(n.score, max);
    const pct = Math.round((n.score / max) * 100);
    return `<tr>
      <td><span class="rank-badge" style="background:rgba(124,106,247,.15);color:#7c6af7">${i+1}</span></td>
      <td><strong>${n.marque}</strong><br><span style="font-size:11px;color:var(--text2)">${n.categorie}</span></td>
      <td><div class="score-bar"><div class="score-fill" style="width:${pct}%;background:${col}"></div></div></td>
      <td style="font-weight:600">${n.prix_moy}€</td>
    </tr>`;
  }).join('');
}

function renderDashAlerts(alerts) {
  const el = document.getElementById('dashAlerts');
  if (!alerts || !alerts.length) { el.innerHTML = '<div class="empty">Aucune alerte encore.</div>'; return; }
  el.innerHTML = alerts.slice(0, 6).map((a, i) => `
    <div class="alert-item">
      <div class="alert-dot ${i === 0 ? 'alert-dot-new' : 'alert-dot-old'}"></div>
      <div>
        <div class="alert-title">${a.titre.substring(0,45)}${a.titre.length>45?'...':''}</div>
        <div class="alert-meta">${a.categorie} · ${a.marque||'—'} · ${a.prix}€ · ${a.date}</div>
      </div>
      <div style="margin-left:auto;text-align:right;flex-shrink:0">
        <div class="alert-badge">-${a.economie_pct}%</div>
        <a class="voir" href="${a.url}" target="_blank">Voir →</a>
      </div>
    </div>`).join('');
}

function renderNichesTable(niches, catFilter) {
  const tb = document.getElementById('nichesFullTable');
  let data = niches;
  if (catFilter) data = niches.filter(n => n.categorie === catFilter);
  if (!data.length) { tb.innerHTML = '<tr><td colspan="8" class="empty">Aucune niche pour ce filtre.</td></tr>'; return; }
  const max = data[0] ? data[0].score : 1;
  tb.innerHTML = data.map((n, i) => {
    const col = scoreColor(n.score, max);
    const pct = Math.round((n.score / max) * 100);
    const velTag = n.velocite > 30 ? 'tag-hot' : n.velocite > 10 ? 'tag-warm' : 'tag-ok';
    return `<tr>
      <td style="color:var(--text2);font-size:12px">${i+1}</td>
      <td><strong>${n.marque}</strong></td>
      <td style="color:var(--text2);font-size:12px">${n.categorie}</td>
      <td>${n.nb}</td>
      <td style="font-weight:600">${n.prix_moy}€ <span style="font-size:10px;color:var(--text2)">${n.prix_min}–${n.prix_max}€</span></td>
      <td>❤️ ${n.fav_moy}</td>
      <td><span class="tag ${velTag}">${n.velocite}%</span></td>
      <td><div class="score-bar"><div class="score-fill" style="width:${pct}%;background:${col}"></div></div> <span style="font-size:11px">${n.score}</span></td>
    </tr>`;
  }).join('');
}

function filterNiches() {
  const cat = document.getElementById('nicheCatFilter').value;
  renderNichesTable(nichesAll, cat);
}

function renderMarques(marques) {
  const tb = document.getElementById('marquesTable');
  if (!marques || !marques.length) { tb.innerHTML = '<tr><td colspan="5" class="empty">En attente...</td></tr>'; return; }
  tb.innerHTML = marques.map((m, i) => `<tr>
    <td style="color:var(--text2);font-size:12px">${i+1}</td>
    <td><strong>${m.marque}</strong></td>
    <td>${m.nb}</td>
    <td>${m.prix_moy}€</td>
    <td>❤️ ${m.fav_moy}</td>
  </tr>`).join('');

  // Graphique marques
  if (marquesChartInst) { marquesChartInst.destroy(); marquesChartInst = null; }
  const top10 = marques.slice(0, 10);
  marquesChartInst = new Chart(document.getElementById('chartMarques'), {
    type: 'bar',
    data: {
      labels: top10.map(m => m.marque),
      datasets: [{
        label: 'Score',
        data: top10.map(m => Math.round(m.fav_moy * m.prix_moy)),
        backgroundColor: '#7c6af7', borderWidth: 0, borderRadius: 4
      }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#8888aa', font: { size: 11 } }, grid: { color: 'rgba(255,255,255,.05)' }, border: { display: false } },
        y: { ticks: { color: '#e8e8f4', font: { size: 11 } }, grid: { display: false }, border: { display: false } }
      }
    }
  });
}

function renderKwCloud(mots) {
  const el = document.getElementById('kwCloud');
  if (!mots || !mots.length) { el.innerHTML = '<span class="empty">En attente...</span>'; return; }
  const max = mots[0].count;
  const colors = ['rgba(255,77,109,.15)','rgba(255,209,102,.15)','rgba(0,214,143,.15)','rgba(77,159,255,.15)','rgba(124,106,247,.15)'];
  const tcols = ['#ff4d6d','#ffd166','#00d68f','#4d9fff','#7c6af7'];
  el.innerHTML = mots.map((m, i) => {
    const sz = Math.round(11 + (m.count / max) * 8);
    const ci = i % 5;
    return `<span class="kw-tag" style="font-size:${sz}px;background:${colors[ci]};color:${tcols[ci]}">${m.mot} <span style="opacity:.6;font-size:10px">${m.count}</span></span>`;
  }).join('');
}

function renderAlertesFull(alerts) {
  const el = document.getElementById('alertesFullList');
  if (!alerts || !alerts.length) { el.innerHTML = '<div class="empty">Aucune alerte encore.</div>'; return; }
  el.innerHTML = alerts.map((a, i) => `
    <div class="alert-item">
      <div class="alert-dot ${i === 0 ? 'alert-dot-new' : 'alert-dot-old'}"></div>
      <div style="flex:1">
        <div class="alert-title">${a.titre}</div>
        <div class="alert-meta">${a.categorie} · ${a.marque||'—'} · Prix moyen niche: ${a.prix_moyen_niche}€ · ${a.date}</div>
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-size:18px;font-weight:700;color:var(--green)">-${a.economie_pct}%</div>
        <div style="font-size:13px;font-weight:600">${a.prix}€</div>
        <a class="voir" href="${a.url}" target="_blank">Voir sur Vinted →</a>
      </div>
    </div>`).join('');
}

let tendanceChart = null, prixChart = null;

function renderCharts(tendances, categories) {
  if (tendanceChart) { tendanceChart.destroy(); tendanceChart = null; }
  if (prixChart) { prixChart.destroy(); prixChart = null; }

  if (tendances && tendances.length) {
    tendanceChart = new Chart(document.getElementById('chartTendance'), {
      type: 'line',
      data: {
        labels: tendances.map(t => t.jour),
        datasets: [{
          label: 'Articles', data: tendances.map(t => t.nb),
          borderColor: '#7c6af7', backgroundColor: 'rgba(124,106,247,.1)',
          borderWidth: 2, fill: true, tension: 0.4, pointRadius: 3
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#8888aa', font: { size: 10 } }, grid: { display: false }, border: { display: false } },
          y: { ticks: { color: '#8888aa', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,.05)' }, border: { display: false } }
        }
      }
    });
  }

  if (categories && categories.length) {
    const top8 = categories.slice(0, 8);
    prixChart = new Chart(document.getElementById('chartPrix'), {
      type: 'bar',
      data: {
        labels: top8.map(c => c.cat.replace('Vêtements ', 'Vêt. ').replace('Chaussures ', 'Chaus. ')),
        datasets: [{
          label: 'Prix moy', data: top8.map(c => c.prix_moy),
          backgroundColor: '#4d9fff', borderWidth: 0, borderRadius: 4
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#8888aa', font: { size: 9 }, maxRotation: 35 }, grid: { display: false }, border: { display: false } },
          y: { ticks: { color: '#8888aa', font: { size: 10 }, callback: v => v + '€' }, grid: { color: 'rgba(255,255,255,.05)' }, border: { display: false } }
        }
      }
    });
  }
}

async function refresh() {
  try {
    const d = await fetch('/api/full').then(r => r.json());
    if (d.error) {
      document.getElementById('statusText').textContent = 'Erreur';
      return;
    }
    DATA = d;
    nichesAll = d.niches || [];

    // Status
    document.getElementById('liveDot').style.background = 'var(--green)';
    document.getElementById('statusText').textContent = 'Bot actif · scan toutes les 2min';

    // Métriques
    document.getElementById('mArt').textContent = (d.stats.articles || 0).toLocaleString('fr-FR');
    document.getElementById('mArtSub').textContent = '+' + (d.stats.nouveaux_24h || 0) + ' dernières 24h';
    document.getElementById('mNiches').textContent = nichesAll.length;
    document.getElementById('mAlerts').textContent = d.stats.alertes || 0;
    document.getElementById('mPrix').textContent = (d.stats.prix_moyen || 0) + '€';
    document.getElementById('mPrixSub').textContent = 'toutes catégories';
    document.getElementById('mScan').textContent = d.stats.last_scan || '—';
    document.getElementById('mScanSub').textContent = d.stats.nb_total_scans + ' scans effectués';

    // Render
    renderTopNiches(nichesAll);
    renderDashAlerts(d.alertes_recentes);
    renderNichesTable(nichesAll, document.getElementById('nicheCatFilter').value);
    renderMarques(d.marques);
    renderKwCloud(d.mots_cles);
    renderAlertesFull(d.alertes_recentes);
    renderCharts(d.tendances, d.categories);

    // Filtre catégories
    const sel = document.getElementById('nicheCatFilter');
    const cats = [...new Set(nichesAll.map(n => n.categorie))].sort();
    if (sel.options.length <= 1) {
      cats.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o); });
    }

  } catch (e) {
    document.getElementById('liveDot').style.background = 'var(--border)';
    document.getElementById('statusText').textContent = 'Hors ligne';
  }
}

refresh();
setInterval(refresh, 20000);
</script></body></html>"""

# ── API ───────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/full")
def api_full():
    try:
        c = sqlite3.connect(DB)

        articles     = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        alertes_cnt  = c.execute("SELECT COUNT(*) FROM alertes_log").fetchone()[0]
        prix_moyen   = c.execute("SELECT ROUND(AVG(prix),2) FROM articles WHERE prix > 1").fetchone()[0] or 0
        last_scan_r  = c.execute("SELECT date FROM scans_log ORDER BY id DESC LIMIT 1").fetchone()
        nb_scans     = c.execute("SELECT COUNT(*) FROM scans_log").fetchone()[0]
        nouveaux_24h = c.execute("SELECT COUNT(*) FROM articles WHERE date_scraping >= datetime('now','-24 hours')").fetchone()[0]

        last_scan = last_scan_r[0][:16].replace("T", " ") if last_scan_r else None

        alertes_r = c.execute("""
            SELECT titre,marque,prix,prix_moyen_niche,economie_pct,categorie,url,date
            FROM alertes_log ORDER BY id DESC LIMIT 20
        """).fetchall()

        tendances_r = c.execute("""
            SELECT DATE(date_scraping), COUNT(*), ROUND(AVG(prix),2)
            FROM articles WHERE date_scraping >= datetime('now','-7 days')
            GROUP BY DATE(date_scraping) ORDER BY DATE(date_scraping)
        """).fetchall()

        categories_r = c.execute("""
            SELECT categorie, ROUND(AVG(prix),2), COUNT(*), ROUND(AVG(nb_favoris),1)
            FROM articles WHERE prix > 1 GROUP BY categorie ORDER BY AVG(prix) DESC
        """).fetchall()

        marques_r = c.execute("""
            SELECT marque, COUNT(*) as nb, ROUND(AVG(prix),2), ROUND(AVG(nb_favoris),1)
            FROM articles WHERE marque != '' AND prix > 1
            GROUP BY marque HAVING nb >= 2
            ORDER BY AVG(nb_favoris) * AVG(prix) DESC LIMIT 20
        """).fetchall()

        c.close()

        return jsonify({
            "stats": {
                "articles": articles, "alertes": alertes_cnt,
                "prix_moyen": prix_moyen, "last_scan": last_scan,
                "nb_total_scans": nb_scans, "nouveaux_24h": nouveaux_24h,
            },
            "niches":     calculer_niches(),
            "marques":    [{"marque":r[0],"nb":r[1],"prix_moy":r[2],"fav_moy":r[3]} for r in marques_r],
            "mots_cles":  mots_cles(),
            "tendances":  [{"jour":r[0],"nb":r[1],"prix_moy":r[2]} for r in tendances_r],
            "categories": [{"cat":r[0],"prix_moy":r[1],"nb":r[2],"fav_moy":r[3]} for r in categories_r],
            "alertes_recentes": [{"titre":r[0],"marque":r[1],"prix":r[2],"prix_moyen_niche":r[3],
                                  "economie_pct":r[4],"categorie":r[5],"url":r[6],"date":r[7]} for r in alertes_r],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── INIT ──────────────────────────────────────────────────────────────────────
init_db()
threading.Thread(target=scanner, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

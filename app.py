import os, time, sqlite3, requests, threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
DB = os.path.join("/tmp", "vinted.db")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ALERTES = [
    {"nom": "Nike / Jordan", "categorie_id": 812, "marques": ["Nike", "Jordan"], "prix_max": 60, "prix_min": 5, "mots_cles": [], "auto_like": False},
    {"nom": "Vintage femme", "categorie_id": 1904, "marques": [], "prix_max": 30, "prix_min": 3, "mots_cles": ["vintage", "y2k", "retro"], "auto_like": False},
    {"nom": "Électronique Apple", "categorie_id": 2225, "marques": ["Apple"], "prix_max": 80, "prix_min": 5, "mots_cles": [], "auto_like": False},
    {"nom": "Adidas Samba / Campus", "categorie_id": 812, "marques": ["Adidas"], "prix_max": 70, "prix_min": 10, "mots_cles": ["samba", "campus", "gazelle"], "auto_like": False},
    {"nom": "Cartes Pokémon", "categorie_id": 1194, "marques": [], "prix_max": 50, "prix_min": 2, "mots_cles": ["pokemon", "pokémon", "carte"], "auto_like": False},
]

def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS articles (
        id TEXT PRIMARY KEY, titre TEXT, marque TEXT, prix REAL,
        categorie TEXT, nb_favoris INTEGER, url TEXT, date_scraping TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS alertes_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, titre TEXT, marque TEXT,
        prix REAL, categorie TEXT, url TEXT, date TEXT)""")
    c.commit(); c.close()

def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Origin": "https://www.vinted.fr",
        "Referer": "https://www.vinted.fr/",
    })
    try: s.get("https://www.vinted.fr", timeout=10)
    except: pass
    return s

def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except: pass

def ids_vus():
    try:
        c = sqlite3.connect(DB); r = set(x[0] for x in c.execute("SELECT id FROM articles")); c.close(); return r
    except: return set()

def sauvegarder(a):
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?)",
            (a["id"],a["titre"],a["marque"],a["prix"],a["categorie"],a["nb_favoris"],a["url"],datetime.now().isoformat()))
        c.commit(); c.close()
    except: pass

def log_alerte(a):
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT INTO alertes_log (titre,marque,prix,categorie,url,date) VALUES (?,?,?,?,?,?)",
            (a["titre"],a["marque"],a["prix"],a["categorie"],a["url"],datetime.now().strftime("%d/%m %H:%M")))
        c.commit(); c.close()
    except: pass

def scanner():
    session = get_session()
    vus = ids_vus()
    while True:
        for alerte in ALERTES:
            try:
                params = {"catalog_ids": alerte["categorie_id"], "page": 1, "per_page": 48,
                          "order": "newest_first", "price_from": alerte["prix_min"], "price_to": alerte["prix_max"]}
                r = session.get("https://www.vinted.fr/api/v2/catalog/items", params=params, timeout=10)
                items = r.json().get("items", [])
                for item in items:
                    iid = str(item.get("id",""))
                    if iid in vus: continue
                    titre = item.get("title","").lower()
                    marque = item.get("brand_title","") or item.get("brand","")
                    prix = float(item.get("price",{}).get("amount",0) if isinstance(item.get("price"),dict) else item.get("price",0))
                    if alerte["marques"] and not any(m.lower() in marque.lower() for m in alerte["marques"]): continue
                    if alerte["mots_cles"] and not any(mc.lower() in titre for mc in alerte["mots_cles"]): continue
                    art = {"id":iid,"titre":item.get("title",""),"marque":marque,"prix":prix,
                           "categorie":alerte["nom"],"nb_favoris":item.get("favourite_count",0),
                           "url":f"https://www.vinted.fr/items/{iid}"}
                    vus.add(iid); sauvegarder(art); log_alerte(art)
                    telegram(f"🔔 <b>{alerte['nom']}</b>\n{art['titre']}\nMarque : {marque} | Prix : {prix}€\n{art['url']}")
                time.sleep(2)
            except Exception as e:
                session = get_session()
                time.sleep(5)
        time.sleep(90)

# ── HTML DASHBOARD ──────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vinted Bot</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f13;color:#e8e8f0;min-height:100vh}
.topbar{background:#18181f;border-bottom:1px solid #2a2a35;padding:16px 24px;display:flex;align-items:center;justify-content:space-between}
.logo{font-size:18px;font-weight:600;letter-spacing:-.3px}
.logo span{color:#e94560}
.badge{font-size:11px;padding:4px 12px;border-radius:20px;font-weight:500}
.live{background:#0d3d2a;color:#00e096}
.stopped{background:#3d1a1a;color:#ff6b6b}
.main{padding:24px;max-width:1100px;margin:0 auto}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}
.metric{background:#18181f;border:1px solid #2a2a35;border-radius:12px;padding:16px}
.metric-label{font-size:11px;color:#888;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em}
.metric-val{font-size:26px;font-weight:600}
.metric-sub{font-size:11px;color:#00e096;margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.card{background:#18181f;border:1px solid #2a2a35;border-radius:12px;padding:20px}
.card-title{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-size:11px;color:#666;text-align:left;padding:0 0 8px;border-bottom:1px solid #2a2a35}
td{padding:8px 0;border-bottom:1px solid #1e1e28;vertical-align:middle}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;font-weight:500}
.pill-red{background:#3d1020;color:#ff6b9d}
.pill-ylw{background:#3d2d0a;color:#ffd166}
.pill-grn{background:#0d3020;color:#06d6a0}
.pill-blu{background:#0a1e3d;color:#74b9ff}
.alert-row{display:flex;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid #1e1e28}
.alert-row:last-child{border-bottom:none}
.dot{width:8px;height:8px;border-radius:50%;margin-top:4px;flex-shrink:0}
.dot-new{background:#00e096}
.dot-old{background:#333}
.alert-title{font-size:13px;font-weight:500;margin-bottom:2px}
.alert-meta{font-size:11px;color:#666}
.alert-link{font-size:11px;color:#74b9ff;text-decoration:none;margin-left:auto;flex-shrink:0;padding-top:3px}
.fullrow{grid-column:1/-1}
.config-row{display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1e1e28;font-size:13px}
.config-row:last-child{border-bottom:none}
.toggle{position:relative;width:36px;height:20px;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;top:0;left:0;right:0;bottom:0;background:#333;border-radius:20px;transition:.3s}
.slider:before{content:"";position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s}
input:checked+.slider{background:#e94560}
input:checked+.slider:before{transform:translateX(16px)}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style></head><body>
<div class="topbar">
  <div class="logo">Vinted<span>Bot</span></div>
  <span class="badge" id="statusBadge">Chargement...</span>
</div>
<div class="main">
  <div class="metrics">
    <div class="metric"><div class="metric-label">Articles en base</div><div class="metric-val" id="mArticles">—</div><div class="metric-sub" id="mArticlesSub">—</div></div>
    <div class="metric"><div class="metric-label">Alertes envoyées</div><div class="metric-val" id="mAlertes">—</div><div class="metric-sub" id="mAlertesSub">—</div></div>
    <div class="metric"><div class="metric-label">Alertes actives</div><div class="metric-val" id="mActives">5</div><div class="metric-sub">configurées</div></div>
    <div class="metric"><div class="metric-label">Dernier scan</div><div class="metric-val" style="font-size:16px;" id="mScan">—</div><div class="metric-sub" id="mScanSub">—</div></div>
  </div>
  <div class="grid">
    <div class="card">
      <div class="card-title">Dernières alertes Telegram</div>
      <div id="alertesList"><div style="font-size:13px;color:#555;">Aucune alerte encore.</div></div>
    </div>
    <div class="card">
      <div class="card-title">Alertes configurées</div>
      <div id="configList"></div>
    </div>
    <div class="card fullrow">
      <div class="card-title">Top articles (plus de favoris)</div>
      <table><thead><tr><th>Article</th><th>Marque</th><th>Prix</th><th>❤️</th><th>Lien</th></tr></thead>
      <tbody id="topTable"><tr><td colspan="5" style="color:#555;padding:12px 0;">Lance le bot pour voir les données.</td></tr></tbody></table>
    </div>
  </div>
</div>
<script>
const ALERTES_CONFIG=[
  {nom:"Nike / Jordan",cat:"Chaussures homme",prix:"5–60€"},
  {nom:"Vintage femme",cat:"Vêtements femme",prix:"3–30€"},
  {nom:"Électronique Apple",cat:"Électronique",prix:"5–80€"},
  {nom:"Adidas Samba / Campus",cat:"Chaussures homme",prix:"10–70€"},
  {nom:"Cartes Pokémon",cat:"Jeux vidéo",prix:"2–50€"},
];
const PILLS=["pill-red","pill-ylw","pill-grn","pill-blu","pill-red"];

function renderConfig(){
  const c=document.getElementById('configList');
  c.innerHTML=ALERTES_CONFIG.map((a,i)=>`
    <div class="config-row">
      <div>
        <div style="font-weight:500;margin-bottom:2px;">${a.nom}</div>
        <div style="font-size:11px;color:#666;">${a.cat} · ${a.prix}</div>
      </div>
      <span class="pill ${PILLS[i]}">${a.prix}</span>
    </div>`).join('');
}

async function refresh(){
  try{
    const d=await fetch('/api/stats').then(r=>r.json());
    document.getElementById('statusBadge').textContent='Bot actif';
    document.getElementById('statusBadge').className='badge live';
    document.getElementById('mArticles').textContent=d.articles.toLocaleString('fr-FR');
    document.getElementById('mArticlesSub').textContent='en base SQLite';
    document.getElementById('mAlertes').textContent=d.alertes;
    document.getElementById('mAlertesSub').textContent='envoyées sur Telegram';
    document.getElementById('mScan').textContent=d.last_scan||'—';
    document.getElementById('mScanSub').textContent='prochain dans ~90s';

    const al=document.getElementById('alertesList');
    if(d.recent_alertes&&d.recent_alertes.length){
      al.innerHTML=d.recent_alertes.map((a,i)=>`
        <div class="alert-row">
          <div class="dot ${i===0?'dot-new':'dot-old'}"></div>
          <div>
            <div class="alert-title">${a.titre}</div>
            <div class="alert-meta">${a.categorie} · ${a.marque||'—'} · ${a.prix}€ · ${a.date}</div>
          </div>
          <a class="alert-link" href="${a.url}" target="_blank">Voir →</a>
        </div>`).join('');
    }

    const tb=document.getElementById('topTable');
    if(d.top_articles&&d.top_articles.length){
      tb.innerHTML=d.top_articles.map(a=>`
        <tr>
          <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${a.titre}</td>
          <td>${a.marque||'—'}</td>
          <td style="font-weight:500;">${a.prix}€</td>
          <td style="color:#e94560;">❤️ ${a.nb_favoris}</td>
          <td><a href="${a.url}" target="_blank" style="color:#74b9ff;font-size:12px;">Voir →</a></td>
        </tr>`).join('');
    }
  }catch(e){
    document.getElementById('statusBadge').textContent='Hors ligne';
    document.getElementById('statusBadge').className='badge stopped';
  }
}

renderConfig();
refresh();
setInterval(refresh,15000);
</script></body></html>"""

# ── API ROUTES ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/stats")
def stats():
    try:
        c = sqlite3.connect(DB)
        articles = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        alertes  = c.execute("SELECT COUNT(*) FROM alertes_log").fetchone()[0]
        last     = c.execute("SELECT date_scraping FROM articles ORDER BY date_scraping DESC LIMIT 1").fetchone()
        recent   = c.execute("SELECT titre,marque,prix,categorie,url,date FROM alertes_log ORDER BY id DESC LIMIT 8").fetchall()
        top      = c.execute("SELECT titre,marque,prix,nb_favoris,url FROM articles ORDER BY nb_favoris DESC LIMIT 10").fetchall()
        c.close()
        last_scan = last[0][:16].replace("T"," ") if last else None
        return jsonify({
            "articles": articles, "alertes": alertes, "last_scan": last_scan,
            "recent_alertes": [{"titre":r[0],"marque":r[1],"prix":r[2],"categorie":r[3],"url":r[4],"date":r[5]} for r in recent],
            "top_articles":   [{"titre":r[0],"marque":r[1],"prix":r[2],"nb_favoris":r[3],"url":r[4]} for r in top],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── DÉMARRAGE ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    t = threading.Thread(target=scanner, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

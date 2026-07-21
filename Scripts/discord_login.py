# ---------------------------------------------------------------------------
#  CONNEXION DISCORD (OAuth2) -- "Login with Discord", facon appli.
#  Flux (100%% cote client, le SECRET reste sur le Worker Cloudflare) :
#    1. on ouvre un mini serveur HTTP local sur 127.0.0.1:53127 ;
#    2. on ouvre le navigateur sur la page d'autorisation Discord ;
#    3. le joueur autorise -> Discord redirige vers localhost avec un "code" ;
#    4. on envoie ce code au Worker, qui l'echange (avec le secret) et nous
#       renvoie UNIQUEMENT l'identite : {id, username, global_name}.
#  Non bloquant : demarrer_login() lance un thread ; le jeu poll etat_login().
# ---------------------------------------------------------------------------
import http.server
import json
import socketserver
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

CLIENT_ID = "1528119038171938876"
PORT = 53127
REDIRECT_URI = "http://localhost:%d/callback" % PORT
WORKER_URL = "https://tsog-discord-auth.ewangodet.workers.dev"   # ton Worker Cloudflare
SCOPE = "identify"
TIMEOUT = 180          # secondes avant abandon si le joueur ne fait rien

# etat partage (lu par le jeu). statut : idle / attente / ok / echec / annule
_etat = {"statut": "idle", "identite": None, "erreur": ""}
_annule = threading.Event()
_recu = {}             # rempli par le handler HTTP : {"code": ...} ou {"error": ...}

_PAGE = ("<!doctype html><html><head><meta charset='utf-8'><title>The Siege of Grimgate</title>"
         "<style>body{background:#14110d;color:#e6dfce;font-family:Segoe UI,Arial,sans-serif;"
         "text-align:center;padding-top:16vh}h1{color:#d8b46a}p{color:#b0a894}</style></head>"
         "<body><h1>%s</h1><p>%s</p></body></html>")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404); self.end_headers(); return
        q = urllib.parse.parse_qs(parsed.query)
        if "code" in q:
            _recu["code"] = q["code"][0]
            page = _PAGE % ("Connected!", "You can close this tab and return to the game.")
        else:
            _recu["error"] = q.get("error", ["access_denied"])[0]
            page = _PAGE % ("Login cancelled", "You can close this tab and return to the game.")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass                                  # serveur silencieux


def _echanger_code(code):
    """Envoie le code au Worker -> {id, username, global_name} ou None."""
    try:
        data = json.dumps({"code": code, "redirect_uri": REDIRECT_URI}).encode("utf-8")
        req = urllib.request.Request(WORKER_URL, data=data,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "TSOG-login"})
        with urllib.request.urlopen(req, timeout=15) as r:
            info = json.load(r)
        return info if isinstance(info, dict) and info.get("id") else None
    except Exception:
        return None


def _flux():
    _recu.clear()
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", PORT), _Handler)
    except OSError:
        _etat.update(statut="echec", erreur="port_occupe")   # 53127 deja utilise
        return
    httpd.timeout = 0.3
    params = urllib.parse.urlencode({"client_id": CLIENT_ID, "response_type": "code",
                                     "redirect_uri": REDIRECT_URI, "scope": SCOPE})
    try:
        webbrowser.open("https://discord.com/oauth2/authorize?" + params)
    except Exception:
        pass
    t0 = time.time()
    while ("code" not in _recu and "error" not in _recu
           and not _annule.is_set() and time.time() - t0 < TIMEOUT):
        httpd.handle_request()                # traite au plus une requete (timeout 0.3 s)
    httpd.server_close()
    if _annule.is_set():
        _etat.update(statut="annule")
        return
    if "code" not in _recu:
        _etat.update(statut="echec", erreur=_recu.get("error", "timeout"))
        return
    info = _echanger_code(_recu["code"])
    if info:
        _etat.update(statut="ok", identite=info, erreur="")
    else:
        _etat.update(statut="echec", erreur="echange")


def demarrer_login():
    """Lance la connexion Discord en arriere-plan (le jeu reste reactif)."""
    if _etat["statut"] == "attente":
        return
    _annule.clear()
    _etat.update(statut="attente", identite=None, erreur="")
    threading.Thread(target=_flux, daemon=True).start()


def annuler_login():
    """Annule une connexion en cours."""
    _annule.set()


def etat_login():
    """'idle' / 'attente' / 'ok' / 'echec' / 'annule'."""
    return _etat["statut"]


def identite():
    """{'id','username','global_name'} apres un login 'ok', sinon None."""
    return _etat["identite"]


def erreur_login():
    return _etat.get("erreur", "")

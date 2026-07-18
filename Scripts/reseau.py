"""Reseau LAN peer-to-peer en LOCKSTEP pour The Siege of Grimgate.

Aucun serveur dedie : un PC HEBERGE (ecoute), l'autre REJOINT (se connecte) via
l'IP locale. Chaque frame, les 2 PC s'echangent leur input et rejouent la MEME
simulation deterministe (cf. classes.py : temps_ms + Inputs). Un petit "input delay"
masque le ping (l'input local d'une frame est envoye d'avance, joue 'delay' frames
plus tard) -> tant que le ping < delay*33ms, zero a-coup.

Transport : TCP (fiable/ordonne ; une perte d'input = desync, donc on veut la fiabilite).
Paquet d'input = 5 octets : numero de frame (uint32) + masque des 6 actions (1 octet).
"""
import socket
import struct
import select
import time

from classes import Inputs

PORT = 50321
DISCOVERY_PORT = 50322             # port UDP de decouverte LAN (annonces des sessions)
_MAGIC = b"TSOG1"                  # entete des annonces (filtre les paquets etrangers)
# Paquet = input_frame (u32) + masque des 6 inputs (u8) + [GARDE ANTI-DESYNC] state_frame (u32)
# + state_hash (u32). state_frame != 0 = ce paquet porte le HASH de l'etat a cette frame (compare
# au notre pour detecter une divergence deterministe). state_frame == 0 = pas de hash ce coup-ci.
_FMT = ">IBII"
_TAILLE = struct.calcsize(_FMT)    # 13 octets
INPUT_DELAY = 3                    # frames d'avance (3 ~ 100ms de budget de ping)
CHECK_INTERVAL = 20                # frames entre 2 verifications d'etat (garde anti-desync)


def _encoder(inp):
    return ((1 if inp.left else 0) | (2 if inp.right else 0) | (4 if inp.up else 0)
            | (8 if inp.block else 0) | (16 if inp.move1 else 0) | (32 if inp.move2 else 0))


def _decoder(m):
    return Inputs(bool(m & 1), bool(m & 2), bool(m & 4),
                 bool(m & 8), bool(m & 16), bool(m & 32))


# --- Code de session = l'IP encodee en 7 caracteres (PAS un serveur : juste une forme
#     plus agreable que '192.168.1.16'). Base 32 facon Crockford (sans I/L/O/U ambigus),
#     decodage tolerant (I->1, L->1, O->0, U->0). L'hote partage ce code comme il aurait
#     partage l'IP (Discord/voix), mais c'est court, sans points, et resistant aux fautes.
_ALPHA = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHA)}
_DECODE.update({"I": 1, "L": 1, "O": 0, "U": 0})


def code_session(ip):
    """IP 'a.b.c.d' -> code 'XXXX-XXX' (7 caracteres)."""
    try:
        a, b, c, d = (int(x) for x in ip.split("."))
        n = (a << 24) | (b << 16) | (c << 8) | d
    except Exception:
        return "????-???"
    s = ""
    for _ in range(7):
        s = _ALPHA[n & 31] + s
        n >>= 5
    return s[:4] + "-" + s[4:]


def ip_depuis_code(code):
    """Code de session -> IP 'a.b.c.d'. Renvoie None si le code est invalide."""
    code = "".join(ch for ch in code.upper() if ch not in "- ")
    if len(code) != 7:
        return None
    n = 0
    for ch in code:
        if ch not in _DECODE:
            return None
        n = (n << 5) | _DECODE[ch]
    return "%d.%d.%d.%d" % ((n >> 24) & 255, (n >> 16) & 255, (n >> 8) & 255, n & 255)


def ip_locale():
    """IP LAN de cette machine (a afficher cote hote)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class Hote:
    """Cote serveur : ecoute et accepte UNE connexion (accepter() est non bloquant)."""
    def __init__(self, port=PORT):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("", port))
        self.srv.listen(1)
        self.srv.settimeout(0.0)

    def accepter(self):
        """Renvoie un SessionReseau si un client vient de se connecter, sinon None."""
        if not select.select([self.srv], [], [], 0)[0]:
            return None
        try:
            conn, _ = self.srv.accept()
        except Exception:
            return None
        self.srv.close()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return SessionReseau(conn, "Left")

    def fermer(self):
        try:
            self.srv.close()
        except Exception:
            pass


def rejoindre(ip, port=PORT, timeout=6):
    """Cote client : se connecte a l'hote. Renvoie SessionReseau ou None (echec)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.settimeout(None)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return SessionReseau(s, "Right")
    except Exception:
        return None


class Annonceur:
    """Diffuse l'existence d'une session en BROADCAST UDP sur le LAN (facon 'Open to LAN').
    Les clients du meme reseau wifi la voient apparaitre sans IP ni code."""
    def __init__(self, nom, ip_locale_=None, tcp_port=PORT):
        self.nom = (nom or "Game")[:40]
        self.tcp_port = tcp_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # adresses de broadcast : globale + dirigee sur le sous-reseau (/24) pour fiabilite
        self.cibles = ["255.255.255.255"]
        ip = ip_locale_ or ip_locale()
        try:
            self.cibles.append(ip.rsplit(".", 1)[0] + ".255")
        except Exception:
            pass
        self._t = 0.0

    def diffuser(self):
        """A appeler regulierement ; n'emet qu'environ une fois par seconde."""
        now = time.time()
        if now - self._t < 0.8:
            return
        self._t = now
        msg = b"|".join((_MAGIC, self.nom.encode("utf-8"), str(self.tcp_port).encode()))
        for cible in self.cibles:
            try:
                self.sock.sendto(msg, (cible, DISCOVERY_PORT))
            except Exception:
                pass

    def fermer(self):
        try:
            self.sock.close()
        except Exception:
            pass


class ScannerLAN:
    """Ecoute les annonces UDP : liste les sessions du LAN (nom + ip + port)."""
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ok = True
        try:
            self.sock.bind(("", DISCOVERY_PORT))
        except Exception:
            self.ok = False
        self.sock.setblocking(False)
        self.sessions = {}   # (ip, tcp_port) -> {"nom": str, "vu": time}

    def scanner(self):
        """Avale les annonces dispo + oublie celles plus vues depuis 3 s. Renvoie la liste
        triee : [ (nom, ip, tcp_port), ... ]."""
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except (BlockingIOError, OSError):
                break
            parts = data.split(b"|")
            if len(parts) >= 3 and parts[0] == _MAGIC:
                try:
                    nom = parts[1].decode("utf-8", "replace")[:40]
                    tcp_port = int(parts[2])
                except Exception:
                    continue
                self.sessions[(addr[0], tcp_port)] = {"nom": nom, "vu": time.time()}
        maintenant = time.time()
        self.sessions = {k: v for k, v in self.sessions.items() if maintenant - v["vu"] < 3.0}
        return sorted([(v["nom"], k[0], k[1]) for k, v in self.sessions.items()],
                      key=lambda s: s[0].lower())

    def fermer(self):
        try:
            self.sock.close()
        except Exception:
            pass


class SessionReseau:
    """Echange d'inputs en lockstep sur une connexion etablie.
    mon_cote = 'Left' (hote = Joueur 1) ou 'Right' (client = Joueur 2)."""
    def __init__(self, sock, mon_cote, delay=INPUT_DELAY):
        self.sock = sock
        self.sock.setblocking(True)
        self.mon_cote = mon_cote
        self.delay = delay
        self.frame = 0
        self.local_buf = {}     # frame -> Inputs (local)
        self.remote_buf = {}    # frame -> Inputs (distant)
        self.alive = True
        self._buf = b""
        # SANTE RESEAU : temps (ms) passe a ATTENDRE l'input distant chaque frame (lisse en EMA).
        # ~0 = le pair est dans le budget d'input-delay (fluide) ; monte = le reseau ne suit plus.
        self.attente_ms = 0.0
        self.pic_ms = 0.0       # pic recent (retombe doucement) -> capte les a-coups
        # GARDE ANTI-DESYNC : hashs d'etat par frame (les miens + ceux du pair) -> divergence = bug.
        self.my_hash = {}
        self.remote_hash = {}
        self.desync = False

    # --- transport bas niveau ---
    def _envoyer(self, frame, inp, sframe=0, shash=0):
        try:
            self.sock.sendall(struct.pack(_FMT, frame, _encoder(inp),
                                          sframe & 0xFFFFFFFF, shash & 0xFFFFFFFF))
        except Exception:
            self.alive = False

    def _verifier_desync(self, sframe):
        """Compare mon hash et celui du pair pour 'sframe' (des que les DEUX sont connus)."""
        h = self.my_hash.get(sframe)
        if h is not None and sframe in self.remote_hash and h != self.remote_hash[sframe]:
            self.desync = True

    def _parser(self):
        while len(self._buf) >= _TAILLE:
            frame, mask, sframe, shash = struct.unpack(_FMT, self._buf[:_TAILLE])
            self._buf = self._buf[_TAILLE:]
            self.remote_buf[frame] = _decoder(mask)
            if sframe:                                   # ce paquet porte un hash d'etat
                self.remote_hash[sframe] = shash
                self._verifier_desync(sframe)

    def _lire(self):
        try:
            data = self.sock.recv(4096)
        except Exception:
            self.alive = False
            return
        if not data:
            self.alive = False
            return
        self._buf += data
        self._parser()

    def _drain_dispo(self):
        """Avale tout ce qui est immediatement disponible (non bloquant)."""
        while self.alive and select.select([self.sock], [], [], 0)[0]:
            self._lire()

    def _attendre(self, n):
        """Bloque jusqu'a avoir l'input distant de la frame n (ou deconnexion)."""
        while self.alive and n not in self.remote_buf:
            self._lire()

    # --- lockstep ---
    def echanger(self, input_local, etat=0):
        """Une frame de jeu : envoie l'input local (pour frame+delay), attend l'input distant de la
        frame courante, renvoie (inputs_gauche, inputs_droite). 'etat' (optionnel) = hash de l'etat
        de jeu COURANT (= entree de cette frame) : envoye une fois tous CHECK_INTERVAL frames pour
        la garde anti-desync (les 2 cotes hashent le meme etat deterministe -> divergence = bug)."""
        n = self.frame
        self.local_buf[n + self.delay] = input_local
        sframe = shash = 0
        if etat and n and n % CHECK_INTERVAL == 0:       # point de controle -> on joint notre hash
            sframe, shash = n, int(etat) & 0xFFFFFFFF
            self.my_hash[n] = shash
            self._verifier_desync(n)                     # au cas ou le pair est deja arrive
            if len(self.my_hash) > 40:                   # bornage memoire : oublier les vieux points
                for k in [k for k in self.my_hash if k < n - 200]:
                    del self.my_hash[k]
                for k in [k for k in self.remote_hash if k < n - 200]:
                    del self.remote_hash[k]
        self._envoyer(n + self.delay, input_local, sframe, shash)
        self._drain_dispo()
        dt = 0.0
        if n >= self.delay:            # les 'delay' 1res frames : inputs neutres des 2 cotes
            if n not in self.remote_buf:          # le pair est en retard -> on MESURE l'attente
                t0 = time.perf_counter()
                self._attendre(n)
                dt = (time.perf_counter() - t0) * 1000.0
        self.attente_ms += (dt - self.attente_ms) * 0.12     # EMA lissee
        self.pic_ms = max(dt, self.pic_ms * 0.94)            # pic qui retombe doucement
        loc = self.local_buf.pop(n, Inputs())
        rem = self.remote_buf.pop(n, Inputs())
        self.frame += 1
        return (loc, rem) if self.mon_cote == "Left" else (rem, loc)

    def sante(self):
        """Niveau de sante reseau d'apres l'attente moyenne (ms) : 'ok' / 'lag' / 'bad'.
        (En LAN sain, le pair est toujours dans le budget d'input-delay -> attente ~0 = 'ok'.)"""
        a = max(self.attente_ms, self.pic_ms * 0.5)
        return "ok" if a < 6 else ("lag" if a < 22 else "bad")

    # --- petit canal "ligne" pour la selection (avant le combat) ---
    def envoyer_ligne(self, texte):
        try:
            self.sock.sendall((texte + "\n").encode("utf-8"))
        except Exception:
            self.alive = False

    def _lire_brut(self):
        """recv non bloquant SANS parser (phase 'ligne' de selection)."""
        while self.alive and select.select([self.sock], [], [], 0)[0]:
            try:
                data = self.sock.recv(4096)
            except Exception:
                self.alive = False
                return
            if not data:
                self.alive = False
                return
            self._buf += data

    def lire_ligne_dispo(self):
        """Non bloquant : renvoie une ligne texte si une est complete, sinon None.
        (Le reste du buffer = eventuels paquets d'input, laisses pour echanger.)"""
        self._lire_brut()
        if b"\n" in self._buf:
            ligne, _, self._buf = self._buf.partition(b"\n")
            return ligne.decode("utf-8")
        return None

    def lire_ligne(self, timeout=15):
        """Lit une ligne texte (selection persos/map). Bloquant avec timeout."""
        self.sock.settimeout(timeout)
        try:
            while b"\n" not in self._buf:
                data = self.sock.recv(4096)
                if not data:
                    self.alive = False
                    return None
                self._buf += data
        except Exception:
            self.alive = False
            return None
        finally:
            self.sock.settimeout(None)
        ligne, _, self._buf = self._buf.partition(b"\n")
        return ligne.decode("utf-8")

    def fermer(self):
        try:
            self.sock.close()
        except Exception:
            pass

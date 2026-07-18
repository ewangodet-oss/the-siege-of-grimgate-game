# ---------------------------------------------------------------------------
#  Rich Presence Discord ("Joue a The Siege of Grimgate") - ZERO dependance.
#  On parle directement le protocole IPC LOCAL du client Discord du joueur
#  (JSON sur le named pipe \\.\pipe\discord-ipc-N) : rien ne sort de la machine.
#  Tout vit dans UN thread daemon : Discord absent/ferme/plante -> silencieux,
#  reconnexion tentee periodiquement, zero impact sur le jeu.
#  API :  demarrer()  une fois au lancement ;  maj(details, state)  a volonte.
# ---------------------------------------------------------------------------
import json
import os
import struct
import threading
import time

CLIENT_ID = "1528119038171938876"     # Application ID Discord du jeu (public)

# Boutons affiches sous le statut (visibles/cliquables par les AUTRES qui
# regardent le profil du joueur). Max 2. URL vide = bouton non affiche.
URL_ITCH = "https://skakayy.itch.io/the-siege-of-grimgate"
URL_GITHUB = "https://github.com/ewangodet-oss/the-siege-of-grimgate-game"

_OP_HANDSHAKE = 0
_OP_FRAME = 1

_verrou = threading.Lock()
_etat = {"details": "In the menus", "state": None,
         "grande": None, "grande_txt": None, "petite": None, "petite_txt": None}
_version = 0          # bump a chaque maj() -> le thread renvoie l'activite
_demarre = False
_t0 = int(time.time())   # debut de session (chrono "elapsed" sur le profil)


def maj(details, state=None, grande=None, grande_txt=None, petite=None, petite_txt=None):
    """Change le statut affiche (thread-safe, instantane, jamais bloquant).
    grande/petite = cles d'Art Assets Discord (grande image / pastille) ;
    None -> logo du jeu seul."""
    global _version
    with _verrou:
        _etat.update(details=details, state=state, grande=grande,
                     grande_txt=grande_txt, petite=petite, petite_txt=petite_txt)
        _version += 1


def demarrer():
    """Lance le thread de presence (1 seule fois, silencieux si Discord absent)."""
    global _demarre
    if _demarre:
        return
    _demarre = True
    threading.Thread(target=_boucle, daemon=True).start()


# --- interne -----------------------------------------------------------------

def _connecter():
    """Ouvre le pipe IPC de Discord (essaie les 10 emplacements). None si absent."""
    for n in range(10):
        try:
            return open(r"\\.\pipe\discord-ipc-%d" % n, "r+b", buffering=0)
        except OSError:
            continue
    return None


def _envoyer(pipe, op, donnees):
    brut = json.dumps(donnees).encode("utf-8")
    pipe.write(struct.pack("<II", op, len(brut)) + brut)


def _recevoir(pipe):
    entete = pipe.read(8)
    if len(entete) < 8:
        raise OSError("pipe ferme")
    op, taille = struct.unpack("<II", entete)
    return op, json.loads(pipe.read(taille).decode("utf-8"))


def _activite():
    with _verrou:
        v = _version
        act = {
            "details": _etat["details"],
            "timestamps": {"start": _t0},
            "assets": {"large_image": _etat["grande"] or "logo",
                       "large_text": _etat["grande_txt"] or "The Siege of Grimgate"},
        }
        if _etat["state"]:
            act["state"] = _etat["state"]
        if _etat["petite"]:
            act["assets"]["small_image"] = _etat["petite"]
            act["assets"]["small_text"] = _etat["petite_txt"] or _etat["petite"]
        boutons = [{"label": lbl, "url": url} for lbl, url in
                   (("Play free on itch.io", URL_ITCH), ("Source on GitHub", URL_GITHUB))
                   if url][:2]
        if boutons:
            act["buttons"] = boutons
    return v, act


def _boucle():
    pipe = None
    envoyee = -1
    while True:
        try:
            if pipe is None:
                pipe = _connecter()
                if pipe is None:          # Discord pas lance : on retentera
                    time.sleep(30)
                    continue
                _envoyer(pipe, _OP_HANDSHAKE, {"v": 1, "client_id": CLIENT_ID})
                _recevoir(pipe)           # attend le READY
                envoyee = -1              # forcer l'envoi de l'etat courant
            v, act = _activite()
            if v != envoyee:
                _envoyer(pipe, _OP_FRAME, {
                    "cmd": "SET_ACTIVITY",
                    "args": {"pid": os.getpid(), "activity": act},
                    "nonce": str(v),
                })
                _recevoir(pipe)           # draine la reponse (evite de remplir le pipe)
                envoyee = v
            time.sleep(1)
        except Exception:
            try:
                if pipe:
                    pipe.close()
            except Exception:
                pass
            pipe = None
            time.sleep(15)                # Discord ferme/redemarre -> on retentera

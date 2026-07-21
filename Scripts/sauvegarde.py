# ---------------------------------------------------------------------------
#  SAUVEGARDE LOCALE, rangee PAR COMPTE (ID Discord verifie).
#  Fichier save.json a cote du script (gitignore, per-PC). Structure :
#    {"comptes": {"<discord_id>": {username, global_name, cree_le, vu_le,
#                                   progression:{...}}}, "dernier": "<id>",
#     "invite": {progression:{...}}}
#  La PROGRESSION (deblocages, histoire, records...) est un dict libre, prete
#  a accueillir le futur mode Histoire/Arcade. Reglages ecran = settings.json
#  (global a la machine), separe de la progression (par compte).
# ---------------------------------------------------------------------------
import json
import os
import time

SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "save.json")

_data = None            # cache en memoire


def _charger():
    global _data
    if _data is not None:
        return _data
    _data = {"comptes": {}, "dernier": None, "invite": {"progression": {}}}
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            brut = json.load(f)
        if isinstance(brut, dict):
            _data["comptes"] = brut.get("comptes", {}) if isinstance(brut.get("comptes"), dict) else {}
            _data["dernier"] = brut.get("dernier")
            inv = brut.get("invite")
            _data["invite"] = inv if isinstance(inv, dict) else {"progression": {}}
    except (OSError, ValueError):
        pass                                  # absent/corrompu -> vierge
    return _data


def sauver():
    """Ecrit save.json (silencieux si echec disque)."""
    try:
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(_charger(), f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _neuf():
    return {"username": "", "global_name": None, "avatar": None,
            "cree_le": time.time(), "vu_le": time.time(),
            "progression": {}}


def connecter(identite):
    """Ouvre (ou cree) le compte lie a l'identite Discord {id, username, global_name, avatar}.
    Met a jour le pseudo/avatar + la date de derniere connexion, le marque comme 'dernier',
    et renvoie le dict du compte (mutable : le jeu ecrit dans compte['progression'])."""
    d = _charger()
    cid = str(identite["id"])
    c = d["comptes"].setdefault(cid, _neuf())
    c.setdefault("progression", {})
    c["username"] = identite.get("username", c.get("username", ""))
    c["global_name"] = identite.get("global_name", c.get("global_name"))
    c["avatar"] = identite.get("avatar", c.get("avatar"))
    c["vu_le"] = time.time()
    d["dernier"] = cid
    sauver()
    return c


def derniere_identite():
    """Identite {id, username, global_name, avatar} du DERNIER compte connecte, ou None.
    Permet la RECONNEXION AUTO au lancement : la sauvegarde etant 100%% locale, inutile de
    refaire tout le flux OAuth a chaque fois (le login sert juste a verifier une 1re fois)."""
    d = _charger()
    cid = d.get("dernier")
    if not cid:
        return None
    c = d["comptes"].get(str(cid))
    if not c:
        return None
    return {"id": str(cid), "username": c.get("username", ""),
            "global_name": c.get("global_name"), "avatar": c.get("avatar")}


def deconnecter():
    """Oublie le 'dernier' compte -> au prochain lancement (ou tout de suite) le jeu
    redemande le login Discord. Ne SUPPRIME PAS la progression du compte (juste la
    reconnexion auto) : se reconnecter au meme compte retrouve tout."""
    d = _charger()
    d["dernier"] = None
    sauver()


def compte_invite():
    """Compte 'invité' (sans connexion Discord). Progression locale non liee."""
    d = _charger()
    d["invite"].setdefault("progression", {})
    return d["invite"]


def nom_affiche(identite):
    """Pseudo lisible pour l'accueil ('global_name' si dispo, sinon 'username')."""
    if not identite:
        return "Guest"
    return identite.get("global_name") or identite.get("username") or "Player"

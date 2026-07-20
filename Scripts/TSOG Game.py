import os, sys

# Rend le jeu 100%% INDEPENDANT du dossier de lancement ET de la profondeur du script.
# Les assets sont a la RACINE du jeu (dossier contenant "assets/"), mais le script peut
# vivre dans un sous-dossier (ex: Scripts/). On remonte donc l'arborescence depuis le
# script jusqu'a trouver le dossier qui contient "assets/", et on s'y place. Ainsi les
# chemins relatifs ("assets/...", sprites de classes.py) marchent quel que soit l'endroit
# d'ou le jeu est lance (raccourci copie d'un autre PC, double-clic, autre disque...).
try:
    _base = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(_base, "assets")):
            break
        _parent = os.path.dirname(_base)
        if _parent == _base:
            break
        _base = _parent
    os.chdir(_base)
except OSError:
    pass

# Le Python EMBARQUE (embeddable, dossier python_portable) ne met PAS le dossier du
# script dans sys.path -> "import classes/reseau" echouerait maintenant que le code
# est dans Scripts/. On ajoute donc explicitement le dossier du script.
_dossier_script = os.path.dirname(os.path.abspath(__file__))
if _dossier_script not in sys.path:
    sys.path.insert(0, _dossier_script)


def _assurer_dependances():
    """Au lancement : verifie que les paquets requis sont installes et installe ceux
    qui MANQUENT (pip). Permet de tourner sur un env incomplet. Necessite internet +
    pip au 1er lancement ; sans console (pythonw) l'install est silencieuse. Echec
    silencieux : si ca rate, l'import qui suit levera l'erreur normale."""
    import importlib.util, subprocess
    besoins = [("pygame", "pygame"), ("numpy", "numpy"),
               ("pycaw", "pycaw"), ("comtypes", "comtypes")]   # pycaw/comtypes = nom audio Windows (optionnel)
    manquants = [pkg for mod, pkg in besoins if importlib.util.find_spec(mod) is None]
    if manquants:
        try:
            kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}   # CREATE_NO_WINDOW
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *manquants],
                           timeout=600, **kw)
            importlib.invalidate_caches()
        except Exception:
            pass


_assurer_dependances()

import pygame, json, random, math
import classes
import reseau
import discord_rp
from classes import (Button, Lysandra, Kenshi, KonradForgeval, Arinya, Stormr, Oswald, Barrion,
                     TrainingDummy,
                     KEYBINDS, KEYBINDS_DEFAULT, assombrir_nuit, reset_caches_combat,
                     set_filtre_nuit, set_sol, mettre_a_echelle,
                     reset_horloge, avancer_horloge, lire_inputs,
                     reset_horloge_active, avancer_horloge_active)

pygame.init()

# ----------------------------------------------------------------------
#  SAUVEGARDE DES PARAMETRES (options)
#  Les reglages choisis dans les options sont ecrits dans settings.json
#  (a cote du script) et recharges au demarrage. Pour ajouter une option,
#  il suffit d'ajouter une cle a DEFAULT_SETTINGS.
# ----------------------------------------------------------------------
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
DEFAULT_SETTINGS = {"fullscreen": True,
                    "vol_master": 0.8, "vol_music": 0.7, "vol_sfx": 0.9,
                    "vol_perso": 0.9, "vol_ambience": 0.6,
                    "raccourci_ne_plus_demander": False,   # popup raccourcis au lancement
                    "touches_lan": "Left"}                 # profil de touches du joueur LOCAL en LAN (Left/Right)


def charger_settings():
    """Charge les parametres depuis settings.json (defauts si absent/corrompu).
    Applique aussi les touches personnalisees dans KEYBINDS (en place)."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fichier:
            data = json.load(fichier)
        if isinstance(data, dict):
            for cle in DEFAULT_SETTINGS:
                if cle in data:
                    settings[cle] = data[cle]
            # Touches remappees : on ne garde que des entiers valides (robuste).
            kb = data.get("keybinds")
            if isinstance(kb, dict):
                for cote in KEYBINDS:
                    bloc = kb.get(cote)
                    if isinstance(bloc, dict):
                        for action in KEYBINDS[cote]:
                            if action in bloc:
                                valeur = bloc[action]   # None = touche non liee
                                if valeur is None or isinstance(valeur, int):
                                    KEYBINDS[cote][action] = valeur
    except (OSError, ValueError):
        pass  # fichier absent ou illisible -> on garde les defauts
    return settings


def sauver_settings():
    """Ecrit les parametres courants (+ les touches) dans settings.json."""
    SETTINGS["keybinds"] = {cote: dict(KEYBINDS[cote]) for cote in KEYBINDS}
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fichier:
            json.dump(SETTINGS, fichier, indent=2)
    except OSError:
        pass


SETTINGS = charger_settings()

#parametres de l'ecran
SCREEN_WIDTH = 1600
SCREEN_HEIGHT = 900
VERSION = "beta-1.2.1"        # affichee discretement dans un coin du menu principal

# ---------------------------------------------------------------------------
#  MISE A JOUR AUTO (passive). Au lancement, un thread interroge GitHub (release
#  la plus recente du repo) sans bloquer le jeu. Si un tag PLUS RECENT que
#  VERSION existe (avec un .zip attache), le menu principal affiche une popup
#  "Update available" ; le bouton Update lance Scripts/update_tsog.ps1 (copie
#  dans TEMP pour pouvoir remplacer Scripts/) puis le jeu quitte : le script
#  telecharge la release, ecrase les fichiers et relance le jeu.
#  Silencieux dans TOUS les cas d'echec (hors-ligne, pas encore de release...).
GITHUB_REPO = "ewangodet-oss/the-siege-of-grimgate-game"
MAJ_INFO = {"etat": "attente", "tag": None, "cache": False}  # attente / rien / dispo

def _version_tuple(txt):
    """'beta-1.2' -> (1, 2) : ne compare que les nombres (prefixe libre)."""
    import re as _re
    return tuple(int(n) for n in _re.findall(r"\d+", str(txt))) or (0,)

def _verifier_maj_thread():
    try:
        import json as _json, urllib.request as _url
        req = _url.Request("https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO,
                           headers={"User-Agent": "TSOG-updater"})
        with _url.urlopen(req, timeout=6) as rep:
            data = _json.load(rep)
        tag = str(data.get("tag_name") or "")
        a_zip = any(str(a.get("name", "")).lower().endswith(".zip")
                    for a in (data.get("assets") or []))
        if tag and a_zip and _version_tuple(tag) > _version_tuple(VERSION):
            MAJ_INFO["tag"] = tag
            MAJ_INFO["etat"] = "dispo"
        else:
            MAJ_INFO["etat"] = "rien"
    except Exception:
        MAJ_INFO["etat"] = "rien"

import threading as _threading
_threading.Thread(target=_verifier_maj_thread, daemon=True).start()

# Rich Presence Discord ("Joue a The Siege of Grimgate") : thread silencieux,
# ne fait rien si le client Discord n'est pas ouvert sur le PC du joueur.
discord_rp.demarrer()

def lancer_update():
    """Copie l'updater PowerShell dans TEMP (il doit survivre au remplacement de
    Scripts/) et l'ouvre dans SA console ; renvoie True si lance (le jeu doit
    alors quitter pour liberer les fichiers)."""
    import shutil, subprocess, tempfile
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_tsog.ps1")
    if not os.path.isfile(src):
        return False
    try:
        dst = os.path.join(tempfile.gettempdir(), "tsog_update.ps1")
        shutil.copyfile(src, dst)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", dst, "-Install", os.getcwd()],
            creationflags=subprocess.CREATE_NEW_CONSOLE)
        return True
    except Exception:
        return False

FULLSCREEN_MODE = SETTINGS["fullscreen"]  # mode d'affichage (charge depuis la sauvegarde)
# Icone de la fenetre = le logo du jeu (charge avant set_mode pour s'appliquer).
try:
    pygame.display.set_icon(pygame.image.load("assets/Logo.png"))
except Exception:
    pass
# AFFICHAGE ADAPTATIF via le drapeau SCALED de pygame : le jeu est rendu en
# 1600x900 (positions/tailles INCHANGEES) et SDL le met automatiquement a
# l'echelle de l'ecran (avec bandes noires si autre ratio) + convertit la souris.
# En plein ecran on utilise FULLSCREEN|SCALED = "fullscreen desktop" : AUCUN
# changement de resolution du moniteur -> pas de flash noir au lancement.
def appliquer_affichage(plein_ecran):
    """(Re)cree la surface d'affichage 1600x900, mise a l'echelle de l'ecran."""
    global screen
    flags = pygame.SCALED | (pygame.FULLSCREEN if plein_ecran else 0)
    try:
        screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT), flags)
    except Exception:
        screen = pygame.display.set_mode(
            (SCREEN_WIDTH, SCREEN_HEIGHT), pygame.FULLSCREEN if plein_ecran else 0)


appliquer_affichage(FULLSCREEN_MODE)
pygame.display.set_caption("The Siege of Grimgate")

# Logo du jeu pour l'ecran-titre (mis a l'echelle, fond transparent).
try:
    _logo = pygame.image.load("assets/Logo.png").convert_alpha()
    _logo_h = 360
    LOGO_MENU = pygame.transform.smoothscale(
        _logo, (round(_logo.get_width() * _logo_h / _logo.get_height()), _logo_h))
except Exception:
    LOGO_MENU = None

# PROPS_SCALED (calque avant de la map) est charge plus bas, par map (voir props_de_map).

FPS = 30
clock = pygame.time.Clock()

# ----------------------------------------------------------------------
#  AUDIO : volumes par type (Master / Music / SFX / Ambience) regles dans les
#  Options et sauvegardes. L'ambiance (vent) joue en boucle dans les menus.
# ----------------------------------------------------------------------
try:
    pygame.mixer.init()
    pygame.mixer.set_num_channels(24)
    CANAL_AMBIANCE = pygame.mixer.Channel(0)   # ambiance des menus (vent)
    # Canaux dedies aux ambiances de MAP (jouees en boucle ET superposees pendant le combat)
    CANAUX_AMBIANCE_MAP = [pygame.mixer.Channel(1), pygame.mixer.Channel(2)]
    SON_VENT = pygame.mixer.Sound("assets/audio/ambient/Wind.mp3")
except Exception:
    CANAL_AMBIANCE = None
    CANAUX_AMBIANCE_MAP = []
    SON_VENT = None


def nommer_session_audio():
    """Donne a la session audio du jeu un NOM + une ICONE propres dans le melangeur
    de volume Windows (sinon : 'pythonw' + icone Python). Echec silencieux si pycaw
    absent / pas d'audio / autre OS."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioSessionControl2
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Logo_build.ico")
        pid = os.getpid()
        for session in AudioUtilities.GetAllSessions():
            if session.Process and session.Process.pid == pid:
                ctl = session._ctl.QueryInterface(IAudioSessionControl2)
                ctl.SetDisplayName("The Siege of Grimgate", None)
                if os.path.exists(ico):
                    ctl.SetIconPath(ico, None)
    except Exception:
        pass


nommer_session_audio()   # la session existe des l'ouverture du device par mixer.init()


def appliquer_volumes():
    """Applique les volumes (master x type) aux canaux audio."""
    m = SETTINGS.get("vol_master", 0.8)
    amb = m * SETTINGS.get("vol_ambience", 0.6)
    if CANAL_AMBIANCE is not None:
        CANAL_AMBIANCE.set_volume(amb)
    for ch in CANAUX_AMBIANCE_MAP:           # ambiances de map (volume "Ambience")
        if ch is not None and ch.get_busy():
            ch.set_volume(amb)
    try:
        pygame.mixer.music.set_volume(m * SETTINGS.get("vol_music", 0.7))
    except Exception:
        pass


def ambiance_menu(actif):
    """Lance (boucle, fondu) ou arrete le vent de fond des menus principaux."""
    if CANAL_AMBIANCE is None or SON_VENT is None:
        return
    if actif and not CANAL_AMBIANCE.get_busy():
        # Regler le volume AVANT le play : le fondu (fade_ms) monte vers le volume
        # du canal capture au lancement -> sinon il revient au volume plein.
        appliquer_volumes()
        CANAL_AMBIANCE.play(SON_VENT, loops=-1, fade_ms=900)
    elif not actif and CANAL_AMBIANCE.get_busy():
        CANAL_AMBIANCE.fadeout(600)


_AMBIANT_MAPS = {}   # id map -> [Sound, ...] (ambiances chargees 1x)


def _ambiant_de_map(map_id):
    sons = _AMBIANT_MAPS.get(map_id)
    if sons is None:
        sons = []
        for chemin in MAPS.get(map_id, {}).get("ambient", []):
            try:
                sons.append(pygame.mixer.Sound(chemin))
            except Exception:
                pass
        _AMBIANT_MAPS[map_id] = sons
    return sons


def jouer_ambiance_map(map_id):
    """Lance les ambiances de la map EN BOUCLE et SUPERPOSEES (canaux dedies)."""
    arreter_ambiance_map(0)
    if not CANAUX_AMBIANCE_MAP:
        return
    vol = SETTINGS.get("vol_master", 0.8) * SETTINGS.get("vol_ambience", 0.6)
    for ch, son in zip(CANAUX_AMBIANCE_MAP, _ambiant_de_map(map_id)):
        ch.set_volume(vol)
        ch.play(son, loops=-1, fade_ms=800)


def arreter_ambiance_map(fade=600):
    """Coupe les ambiances de map en cours."""
    for ch in CANAUX_AMBIANCE_MAP:
        if ch is not None and ch.get_busy():
            ch.fadeout(fade) if fade > 0 else ch.stop()


# --- Bruitages d'interface : hover + clic, avec pitch legerement varie ---
try:
    import numpy as _np
except Exception:
    _np = None


def _charger_sfx_pitch(chemin, n=12, etendue=0.07, vitesse=1.0):
    """Charge un SFX et pre-genere n variantes au pitch legerement decale (de
    -etendue a +etendue, reparties) -> evite l'effet repetitif. Liste de Sound.
    'vitesse' = facteur de lecture GLOBAL (1.0 = tel quel ; 1.35 = 35% plus court
    et plus aigu, par re-echantillonnage) -- le fichier sur disque reste intact."""
    try:
        base = pygame.mixer.Sound(chemin)
    except Exception:
        return []
    if _np is None or (n <= 1 and vitesse == 1.0):
        return [base]
    try:
        arr = pygame.sndarray.array(base)
        nb = arr.shape[0]
        variantes = []
        for i in range(max(1, n)):
            ecart = etendue * (2 * i / (n - 1) - 1) if n > 1 else 0.0
            r = vitesse * (1.0 + ecart)                 # r>1 = plus rapide/aigu (re-echantillonnage)
            m = max(1, int(nb / r))
            idx = _np.clip((_np.arange(m) * r).astype(_np.int64), 0, nb - 1)
            variantes.append(pygame.sndarray.make_sound(_np.ascontiguousarray(arr[idx])))
        return variantes
    except Exception:
        return [base]


if CANAL_AMBIANCE is not None:
    pygame.mixer.set_reserved(3)   # canaux 0 (vent) + 1,2 (ambiance map) proteges de la selection auto
SFX_HOVER = _charger_sfx_pitch("assets/audio/ui/button hover.mp3")
SFX_CLICK = _charger_sfx_pitch("assets/audio/ui/button click.mp3")
SFX_HOVER_PERSO = _charger_sfx_pitch("assets/audio/ui/hover character.ogg", n=1)   # sans variation
# Annonceur du decompte d'intro : [3, 2, 1, FIGHT !] (sans variation), index = segment
SFX_COMPTE = [_charger_sfx_pitch("assets/audio/ui/3.mp3", n=1),
              _charger_sfx_pitch("assets/audio/ui/2.mp3", n=1),
              _charger_sfx_pitch("assets/audio/ui/1.mp3", n=1),
              _charger_sfx_pitch("assets/audio/ui/FIGHT !.mp3", n=1)]
SFX_KO = _charger_sfx_pitch("assets/audio/ui/K.O announce.mp3", n=1)    # annonce K.O. (fin de round)
SFX_WIN = _charger_sfx_pitch("assets/audio/ui/Win announce.mp3", n=1)   # "Win !" (apres le nom du vainqueur)
SFX_SUDDEN = _charger_sfx_pitch("assets/audio/ui/Sudden death announce.mp3", n=1)   # annonce mort subite
# Annonce vocale "Round X" au debut de chaque manche (index 0..2 = Round 1..3, sans variation).
SFX_ROUND = [_charger_sfx_pitch("assets/audio/ui/Round %d.mp3" % k, n=1) for k in (1, 2, 3)]
# Transitions de brume : cape = la brume se referme (couvrir), vent = elle se dissipe (disperser)
SFX_TRANS_COUVRIR = _charger_sfx_pitch("assets/audio/ambient/cape transition.mp3", n=8)
SFX_TRANS_DISPERSER = _charger_sfx_pitch("assets/audio/ambient/wind transition.mp3", n=8)


def jouer_sfx(pool, gain=1.0, cat="sfx", loops=0):
    """Joue une variante au hasard du pool, au volume Master x (volume de la categorie
    'cat' : sfx pour l'UI, perso pour les sons des combattants) x gain. loops=-1 = boucle.
    Renvoie le Channel."""
    if not pool:
        return None
    son = random.choice(pool)
    vol = SETTINGS.get("vol_master", 0.8) * SETTINGS.get("vol_" + cat, 0.9) * gain
    son.set_volume(min(1.0, vol))
    return son.play(loops=loops)


def stop_sfx_combat():
    """Coupe NET les bruitages de COMBAT (canaux 3+), sans toucher aux canaux d'ambiance
    reserves (0 = vent des menus, 1-2 = ambiance de la map) ni a la musique. Appele a
    l'ouverture d'un menu pause -> les sons LONGS (whoosh, hurlements...) ne trainent plus."""
    try:
        for n in range(3, pygame.mixer.get_num_channels()):
            pygame.mixer.Channel(n).stop()
    except Exception:
        pass


# Gains de base des bruitages d'interface (ces fichiers sont forts -> on attenue).
GAIN_HOVER = 0.40
GAIN_CLICK = 0.52
GAIN_TRANSITION = 0.9       # whoosh de brume des transitions d'ecran
GAIN_MUSIQUE_INTRO = 0.55   # musique de l'ecran-studio, attenuee


def jouer_musique_intro():
    """Lance la musique de l'intro (ecran studio), volume Master x Music x gain."""
    try:
        pygame.mixer.music.load("assets/audio/Music/Intro.mp3")
        vol = SETTINGS.get("vol_master", 0.8) * SETTINGS.get("vol_music", 0.7) * GAIN_MUSIQUE_INTRO
        pygame.mixer.music.set_volume(vol)
        pygame.mixer.music.play()
    except Exception:
        pass


MUSIQUE_MENU = "assets/audio/Music/Main Menu.mp3"
# Registre des MAPS. Pour AJOUTER une map : creer son dossier de frames (1.jpg..8.jpg) dans
# assets/BACKGROUND/map/<dossier>/, deposer sa musique dans assets/audio/Music/maps/, puis
# ajouter une entree ici (label affiche, frames = dossier du decor, music, pos = centre du
# cadre sur la carte ; "pos" se regle selon l'endroit du lieu sur assets/BACKGROUND/map/map bg.png).
MAPS = {
    "chapel": {
        "label":  "Grimgate Chapel",
        "frames": "assets/BACKGROUND/map/Grimgate Chapel",
        "props":  "assets/BACKGROUND/props/Grimgate chapel props.png",
        "music":  "assets/audio/Music/maps/Grimgate fort's chapel.mp3",
        "night":  True,         # scene nocturne -> filtre bleu + effets nuit de Stormr
        "pos":    (1011, 621),  # position du marqueur sur la carte (point rouge du fond)
    },
    "mutiny": {
        "label":    "Mutiny",
        "frames":   "assets/BACKGROUND/map/Mutiny",
        "props":    "assets/BACKGROUND/props/Mutiny.png",
        "frame_dt": 0.08,        # frames toutes les 0,08 s
        "scale":    1.20,        # persos plus GROS sur le navire
        "night":    True,        # filtre OMBRE (tint chaud), SANS le halo de Stormr
        "tint":     (150, 112, 72),   # tonalite chaude/sombre du navire (R>G>B)
        "halo":     False,       # pas de halo (mais slashs surbrillants + particules non teintes)
        "music":    "assets/audio/Music/maps/Mutiny.mp3",
        "ambient": ["assets/audio/ambient/Mutiny ambient 1.ogg",
                    "assets/audio/ambient/Mutiny ambient 2.ogg"],   # boucle + superposees
        "pos":      (552, 152),  # sur le marqueur bleu (haut-gauche de la carte)
    },
    "heahburna": {
        "label":    "Heahburna Waterfalls",
        "frames":   "assets/BACKGROUND/map/Heahburna waterfalls",
        "props":    "assets/BACKGROUND/props/Heahburna waterfalls.png",
        "frame_dt": 0.1,         # frames toutes les 0,1 s
        "scale":    0.80,        # persos plus PETITS (grande scene de cascades)
        "ground":   810,         # sol un peu plus haut (entre l'ancien 850 et 770)
        "music":    "assets/audio/Music/maps/Heahburna waterfalls.mp3",
        "ambient": ["assets/audio/ambient/Heahburna waterfalls ambient 1.ogg",
                    "assets/audio/ambient/Heahburna waterfalls ambient 2.ogg"],   # boucle + superposees
        "pos":      (867, 391),  # sur le marqueur vert de la carte
    },
    "kurohane": {
        "label":   "Kurohane's Mansion",
        "frames":  "assets/BACKGROUND/map/Kurohane's Mansion",
        "props":   "assets/BACKGROUND/props/Kurohane's Mansion.png",
        "night":   True,             # scene nocturne -> filtre nuit + effets nuit de Stormr
        "tint":    (66, 75, 82),     # nuit propre : sombre et neutre/froide (pas le bleu chapelle)
        "music":   "assets/audio/Music/maps/Kurohane's Mansion.mp3",
        "ambient": ["assets/audio/ambient/Kurohane's Mansion ambient 1.ogg",
                    "assets/audio/ambient/Kurohane's Mansion ambient 2.ogg"],   # boucle + superposees
        "pos":     (1237, 605),  # sur le marqueur violet de la carte
    },
    "temple": {
        "label":   "Heroes' Training Temple",
        "frames":  "assets/BACKGROUND/map/Heroes' Training Temple (night)",   # ' droit (dossier)
        "props":   "assets/BACKGROUND/props/Heroes’ Training Temple (night).png",  # ’ courbe (fichier)
        "night":   True,
        "tint":    (74, 82, 100),   # nuit fraiche/propre du temple
        "frame_dt": 0.14,           # frames du decor toutes les 0,14 s
        "music":   "assets/audio/Music/maps/Heroes' Training Temple (night).mp3",
        "ambient": ["assets/audio/ambient/Heroes' Training Temple ambient 1.ogg"],
        "pos":     (906, 301),      # marqueur brun-orange (sur le temple de la carte)
    },
    # Version JOUR du temple : uniquement pour le mode ENTRAINEMENT (masquee du roster).
    "temple_day": {
        "label":   "Heroes' Training Temple",
        "frames":  "assets/BACKGROUND/map/Heroes' Training Temple (day)",
        "props":   "assets/BACKGROUND/props/Heroes’ Training Temple (day).png",
        "night":   False,
        "frame_dt": 0.14,           # frames du decor toutes les 0,14 s (comme la version nuit)
        "music":   "assets/audio/Music/maps/Heroes' Training Temple (day).mp3",
        "ambient": ["assets/audio/ambient/Heroes' Training Temple ambient 1.ogg"],   # meme que la nuit
        "cache_roster": True,       # n'apparait PAS dans la selection de map normale
    },
}
MAP_ACTUELLE = "chapel"     # map jouee actuellement (la chapelle de la forteresse)
MAP_ECHELLE = 1.0           # echelle des persos de la map en cours (reglee par jeu())
IA_NIVEAU = None            # mode SOLO : None = 2 humains ; "facile"/"normal"/"difficile" = J2 = CPU
_musique_actuelle = None   # piste mixer.music en boucle en cours (None = aucune)


def jouer_musique(chemin, fade_ms=1000):
    """Lance une musique en BOUCLE via mixer.music (volume Master x Music).
    Ne fait rien si cette piste tourne deja (retour depuis les options, etc.)."""
    global _musique_actuelle
    if _musique_actuelle == chemin:
        return
    try:
        pygame.mixer.music.load(chemin)
        pygame.mixer.music.set_volume(
            SETTINGS.get("vol_master", 0.8) * SETTINGS.get("vol_music", 0.7))
        pygame.mixer.music.play(loops=-1, fade_ms=fade_ms)
        _musique_actuelle = chemin
    except Exception:
        pass


def arreter_musique(fade_ms=800):
    """Coupe (en fondu) la musique en boucle en cours."""
    global _musique_actuelle
    if _musique_actuelle is not None:
        try:
            pygame.mixer.music.fadeout(fade_ms)
        except Exception:
            pass
        _musique_actuelle = None


def jouer_sfx_hover():
    jouer_sfx(SFX_HOVER, GAIN_HOVER)


def jouer_sfx_click():
    jouer_sfx(SFX_CLICK, GAIN_CLICK)


# Branche les bruitages sur TOUS les boutons de la classe Button (onglets, Apply/
# Back, pause, victoire, confirmation, Fight, roster...).
Button.son_hover = jouer_sfx_hover
Button.son_click = jouer_sfx_click


# ----------------------------------------------------------------------
#  BRUITAGES DE COMBAT (bouclier generique + armes de Konrad)
#  Detectes de facon CENTRALISEE dans jouer_round (etat des combattants).
# ----------------------------------------------------------------------
GAIN_COMBAT = 0.9
GAIN_SHIELD = 0.65   # bouclier un peu plus bas que les autres bruitages de combat
# Gains DEDIES par son de bouclier (fallback GAIN_SHIELD). Le clang d'IMPACT doit CLAQUER
# (a 0.65 il etait noye ; 1.35 -> vol final ~0.97 = quasi le max, ~2x plus fort).
GAINS_SHIELD = {"shield_hit": 1.35}
GAIN_VOIX = 1.0                 # voix (deja normalisees au niveau des autres sons) : leger plus
GAIN_SPARK = 0.5                # crepitement de charge (subtil, en boucle)
SPARK_LENT, SPARK_RAPIDE = 24, 5   # frames entre 2 sparks : charge faible (lent) -> pleine (rapide)
_AUDIO_PERSO = "assets/audio/Characters/"


def _pool_sons(chemins, n=3, etendue=0.05, vitesse=1.0):
    """Pool de variantes (pitch) a partir d'un OU plusieurs fichiers (choix au hasard).
    'vitesse' > 1 accelere le son (plus court + plus aigu), sans toucher le fichier."""
    if isinstance(chemins, str):
        chemins = [chemins]
    pool = []
    for c in chemins:
        pool += _charger_sfx_pitch(c, n=n, etendue=etendue, vitesse=vitesse)
    return pool


def _normaliser(pool, cible=28000, facteur_max=8.0):
    """Amplifie chaque son du pool pour que son PIC atteigne 'cible' (sur 32767) :
    remonte les fichiers enregistres trop bas (les voix) au niveau des autres sons,
    sans saturer. Plafonne le gain a facteur_max (n'amplifie pas le bruit a l'infini)."""
    if _np is None:
        return pool
    out = []
    for s in pool:
        try:
            arr = pygame.sndarray.array(s).astype(_np.float32)
            pic = float(_np.abs(arr).max())
            if pic > 0:
                f = min(facteur_max, cible / pic)
                arr = _np.clip(arr * f, -32768, 32767).astype(_np.int16)
                out.append(pygame.sndarray.make_sound(_np.ascontiguousarray(arr)))
            else:
                out.append(s)
        except Exception:
            out.append(s)
    return out


# --- Bouclier (GENERIQUE, tous les persos) ---
_G = _AUDIO_PERSO + "generic/"
SONS_COMBAT = {
    "shield_up":    _pool_sons(_G + "Shield up.ogg", 4),
    "shield_down":  _pool_sons(_G + "Shield Down.ogg", 4),
    "shield_hit":   _pool_sons([_G + "Shield hit 1.ogg", _G + "Shield hit 2.ogg", _G + "Shield hit 3.ogg"], 2),
    "shield_crack": _pool_sons([_G + "shield crack 1.ogg", _G + "shield crack 2.ogg", _G + "shield crack 3.ogg"], 2),
    "shield_break": _pool_sons(_G + "Shield break.ogg", 3),
}
# Le clang d'IMPACT est enregistre TRES bas -> le volume de lecture (deja au plafond ~0.97)
# ne suffit pas : on AMPLIFIE ses echantillons (pic ~= max) pour qu'il CLAQUE pour de vrai.
SONS_COMBAT["shield_hit"] = _normaliser(SONS_COMBAT["shield_hit"], cible=32000, facteur_max=12.0)


def jouer_son_combat(nom):
    """Joue un bruitage de bouclier (variante au hasard, volume des sons persos)."""
    jouer_sfx(SONS_COMBAT.get(nom), GAINS_SHIELD.get(nom, GAIN_SHIELD), cat="perso")


# --- Mannequin d'entrainement ---
# Les 3 evenements (coup encaisse, brise en 2, moitie haute qui retombe) utilisent tous les
# memes sons "get hit" (une variante au hasard a chaque fois).
_TD = _AUDIO_PERSO + "Training dummy/"
_SON_DUMMY = _pool_sons([_TD + "get hit 1.ogg", _TD + "get hit 2.ogg", _TD + "get hit 3.ogg"], 3)
SONS_DUMMY = {"hit": _SON_DUMMY, "break": _SON_DUMMY, "fall": _SON_DUMMY}


# --- Bruitages PROPRES A CHAQUE PERSO (pour l'instant : Konrad) ---
_K = _AUDIO_PERSO + "Konrad/"
SONS_PERSO = {
    "Konrad": {
        "swing":  {1: _pool_sons(_K + "rapier swing.ogg"),      2: _pool_sons(_K + "spear swing.ogg"),
                   3: _pool_sons(_K + "Heavy sword swing.ogg"), 4: _pool_sons(_K + "mace swing.ogg")},
        "choose": {1: _pool_sons(_K + "rapier choose.ogg", 2),      2: _pool_sons(_K + "Spear choose.ogg", 2),
                   3: _pool_sons(_K + "Heavy sword choose.ogg", 2), 4: _pool_sons(_K + "mace choose.ogg", 2)},
        "hit":    {1: _pool_sons(_K + "rapier hit.ogg"),      2: _pool_sons(_K + "spear hit.ogg"),
                   3: _pool_sons(_K + "heavy sword hit.ogg"), 4: _pool_sons(_K + "mace hit.ogg")},
        "voice_attack": _pool_sons([_K + "voice attack 1.ogg", _K + "voice attack 2.ogg"], 2),
        "voice_hurt":   _pool_sons([_K + "get hit 1.ogg", _K + "get hit 2.ogg", _K + "get hit 3.ogg"], 2),
        "body_hit":     _pool_sons(_K + "body get hit.ogg", 3),
        "death":        _pool_sons(_K + "death.ogg", 2),
        "jump":         _pool_sons(_K + "jump voic sound.ogg", 2),
        "footstep":     _pool_sons([_K + "footstep 1.ogg", _K + "footstep 2.ogg", _K + "footstep 3.ogg"], 2),
        # frames de la course (8 frames) ou un PIED TOUCHE le sol (numerotees a partir
        # de 1). A ajuster si le pas est decale par rapport a l'anim.
        "foot_frames":  (2, 6),
    },
}

_S = _AUDIO_PERSO + "Stormr/"
SONS_PERSO["Stormr"] = {
    # SUR L'ATTAQUE (meme ratee) : whoosh d'epee + epee electrique
    "swing":     _pool_sons([_S + "sword swing 1.ogg", _S + "sword swing 2.ogg", _S + "sword swing 3.ogg"], 2),
    "lightning": _pool_sons([_S + "Lightning sword 1.ogg", _S + "Lightning sword 2.ogg", _S + "Lightning sword 3.ogg"], 2),
    "voice_attack": _pool_sons([_S + "attack voice 1.ogg", _S + "attack voice 2.ogg", _S + "Attack voice 3.ogg"], 2),
    # QUAND IL TOUCHE : impact par coup
    "hit": {1: _pool_sons(_S + "attack 1.ogg"), 2: _pool_sons(_S + "attack 2.ogg"), 3: _pool_sons(_S + "attack 3.ogg")},
    "voice_hurt":   _pool_sons([_S + "get hit voice 1.ogg", _S + "get hit voice 2.ogg", _S + "get hit voice 3.ogg"], 2),
    "body_hit":     _pool_sons(_S + "body get hit.ogg", 3),
    "jump":         _pool_sons(_S + "jump.ogg", 2),
    "footstep":     _pool_sons([_S + "footstep 1.ogg", _S + "footstep 2.ogg", _S + "footstep 3.ogg"], 2),
    "foot_frames":  (2, 6),
    # charge statique : crepitement en BOUCLE (1/2/3 au hasard), cadence selon la charge
    "spark":   _pool_sons([_S + "Spark charge 1.ogg", _S + "Spark Charge 2.ogg", _S + "Spark charge 3.ogg"], 2),
    "thunder": _pool_sons(_S + "Thunder bolt.mp3", 2),   # foudre du finisher
    "death":   _pool_sons(_S + "death.ogg", 2),
}

_L = _AUDIO_PERSO + "Lysandra/"
SONS_PERSO["Lysandra"] = {
    "swing": {1: _pool_sons(_L + "sword swing 1.mp3"), 2: _pool_sons(_L + "sword swing 2.ogg")},
    "hit":   {1: _pool_sons(_L + "sword attack 1.ogg"), 2: _pool_sons(_L + "sword attack 2.ogg")},
    "voice_attack": _pool_sons([_L + "attack voice 1.ogg", _L + "attack voice 2.ogg"], 2),
    "voice_hurt":   _pool_sons([_L + "get hit 1.ogg", _L + "get hit 2.ogg", _L + "get hit 3.ogg"], 2),
    "body_hit":     _pool_sons(_L + "body get hit.ogg", 3),
    "death":        _pool_sons(_L + "death.ogg", 2),
    "jump":         _pool_sons(_L + "jump.ogg", 2),
    "footstep":     _pool_sons([_L + "step 1.ogg", _L + "step 2.ogg", _L + "step 3.ogg"], 2),
    # SEISME : grondement pendant le gel (coupe a la relache) + explosion a PLEINE charge.
    "seisme_charge":    _pool_sons(_L + "seisme charge.ogg", 1),
    "seisme_explosion": _pool_sons(_L + "seisme explosion.ogg", 1),
    "foot_frames":  (2, 6),
}

_A = _AUDIO_PERSO + "Arinya/"
SONS_PERSO["Arinya"] = {
    # combo melee (par coup)
    "swing": {1: _pool_sons(_A + "attack swing 1.ogg"), 2: _pool_sons(_A + "attack swing 2.ogg")},
    "hit":   {1: _pool_sons(_A + "attack 1.ogg"), 2: _pool_sons(_A + "attack 2.ogg")},
    "voice_attack": _pool_sons([_A + "attack voice 1.ogg", _A + "attack voice 2.ogg"], 2),
    "voice_hurt":   _pool_sons([_A + "get hit 1.ogg", _A + "get hit 2.ogg", _A + "get hit 3.ogg"], 2),
    "body_hit":     _pool_sons(_A + "body get hit.ogg", 3),
    "death":        _pool_sons(_A + "death.ogg", 2),
    "jump":         _pool_sons(_A + "jump.ogg", 2),
    "footstep":     _pool_sons([_A + "step 1.ogg", _A + "step 2.ogg", _A + "step 3.ogg"], 2),
    "foot_frames":  (2, 6),
    # LANCE (signature)
    "spear_charge_voice": _pool_sons(_A + "spear throw charge voice (1 play only).ogg", 2),  # voix au debut de la charge
    "spear_throw":  _pool_sons(_A + "spear throw.ogg", 3),       # relache
    "spear_throw_voice": _pool_sons(_A + "spear throw voice.ogg", 2),
    "spear_flight": _pool_sons(_A + "spear flight.ogg", 2),      # sifflement (au lancer)
    "spear_impale": _pool_sons(_A + "spear impale.ogg", 3),      # touche l'ennemi
    "spear_ground": _pool_sons(_A + "spear touch ground.ogg", 3),# se plante au sol
    "spear_bounce": _pool_sons(_A + "spear bounce.ogg", 2),      # rebond sur bouclier (parade)
    "spear_pickup": _pool_sons(_A + "spear pickup.ogg", 2),      # ramassage
    # DASH
    "dash":         _pool_sons(_A + "dash.ogg", 3),
}

_KE = _AUDIO_PERSO + "Kenshi/"
SONS_PERSO["Kenshi"] = {
    "swing": {1: _pool_sons(_KE + "attack swing 1.ogg"), 2: _pool_sons(_KE + "attack swing 2.ogg")},
    "hit":   {1: _pool_sons(_KE + "attack 1.ogg"), 2: _pool_sons(_KE + "attack 2.ogg")},
    "voice_attack": _pool_sons([_KE + "attack voice 1.ogg", _KE + "attack voice 2.ogg"], 2),
    "voice_hurt":   _pool_sons([_KE + "get hit 1.ogg", _KE + "get hit 2.ogg", _KE + "get hit 3.ogg"], 2),
    "body_hit":     _pool_sons(_KE + "body get hit.ogg", 3),
    "death":        _pool_sons(_KE + "death.ogg", 2),
    "jump":         _pool_sons(_KE + "jump.ogg", 2),
    "footstep":     _pool_sons([_KE + "step 1.ogg", _KE + "step 2.ogg", _KE + "step 3.ogg"], 2),
    "foot_frames":  (2, 6),
    # son du swing decale a la frame du SLASH (1-indexed) au lieu du debut de l'attaque
    # (les coups de Kenshi ont un windup ; impact a la frame 5). A ajuster au besoin.
    "swing_frame":  {1: 5, 2: 5},
    # TRAVERSEE (passe-lame) : whoosh de lame au depart du dash (reutilise ses swings,
    # pool pitche -> ne sonne pas comme une attaque).
    "dash": _pool_sons([_KE + "attack swing 1.ogg", _KE + "attack swing 2.ogg"], 3),
    # FLOW : gain de stack (1-2, aleatoire), plein regime (3e stack), stack perdu (aleatoire).
    "flow_stack": _pool_sons([_KE + "flow stack 1.ogg", _KE + "flow stack 2.ogg"], 2),
    "flow_max":   _pool_sons(_KE + "flow stack max.ogg", 1),
    "flow_lose":  _pool_sons([_KE + "stack lose 1.ogg", _KE + "stack lose 2.ogg"], 2),
    # PASSE-LAME : slice de backstab joue SIMULTANEMENT au weapon hit (meme frame).
    "backstab":   _pool_sons(_KE + "kenshi backstab.ogg", 2),
}
# Les swings de Kenshi sont enregistres tres bas -> on les normalise au niveau des autres.
for _w in (1, 2):
    SONS_PERSO["Kenshi"]["swing"][_w] = _normaliser(SONS_PERSO["Kenshi"]["swing"][_w])

_O = _AUDIO_PERSO + "Oswald/"
SONS_PERSO["Oswald"] = {
    # swing PAR COUP, joue a la frame de l'ECLAT de lumiere (les attaques ont un windup :
    # explosion f21 / pilier f11 / eruption f20) -- pas au debut du coup.
    "swing": {1: _pool_sons(_O + "attack swing 1.ogg"), 2: _pool_sons(_O + "attack swing 2.ogg"),
              3: _pool_sons(_O + "attack swing 3.ogg", vitesse=1.35)},   # accelere de 35%
    "swing_frame": {1: 21, 2: 11, 3: 10},   # atk3 : son lance pendant la montee de lumiere
    "hit":   {1: _pool_sons(_O + "attack 1.ogg"), 2: _pool_sons(_O + "attack 2.ogg"),
              3: _pool_sons(_O + "attack 3.ogg")},
    "voice_attack": _pool_sons([_O + "attack voice 1.ogg", _O + "attack voice 2.ogg"], 2),
    "voice_hurt":   _pool_sons([_O + "get hit 1.ogg", _O + "get hit 2.ogg", _O + "get hit 3.ogg"], 2),
    "body_hit":     _pool_sons(_O + "body get hit.ogg", 3),
    "death":        _pool_sons(_O + "death.ogg", 2),
    "death2":       _pool_sons(_O + "death disappear.ogg", 2),   # superpose a death.ogg (dissolution)
    "footstep":     _pool_sons([_O + "footstep 1.ogg", _O + "footstep 2.ogg", _O + "footstep 3.ogg"], 2),
    "foot_frames":  (2, 6),
    # PAS de "jump" : Haut = TELEPORT. Signatures jouees par detecter_sons_combat :
    "teleport":     _pool_sons(_O + "teleport.ogg", 2),        # au blink du teleport
    "sword_impact": _pool_sons(_O + "sword impact.ogg", 2),    # la SWORD OF JUSTICE s'abat
}

_B = _AUDIO_PERSO + "Barrion/"
SONS_PERSO["Barrion"] = {
    "swing": {1: _pool_sons(_B + "attack swing 1.ogg"), 2: _pool_sons(_B + "attack swing 2.ogg"),
              3: _pool_sons(_B + "attack swing 3.ogg")},
    # attack2 N'EST PAS dans "hit" : son marteau frappe LE SOL -> "sol_hit" joue le son
    # d'impact SYSTEMATIQUEMENT a la frame d'impact (touche ou pas), sans double lecture.
    "hit":   {1: _pool_sons(_B + "attack 1.ogg"), 3: _pool_sons(_B + "attack 3.ogg")},
    "sol_hit":       _pool_sons(_B + "attack 2.ogg"),
    "sol_hit_coups": (2,),
    # l'epee trainee RACLE le sol pendant la marche : superpose aux pas, cale sur les
    # eclats de l'anim (drag_frames), pitch VARIE (4 variantes ±18%) pour casser la boucle.
    "drag":        _pool_sons(_B + "sword friction walk.ogg", 4, etendue=0.18),
    "drag_frames": (3, 7),
    "drag_gain": 0.25,         # tres discret (encore trop fort a 0.4)
    "voice_attack": _pool_sons([_B + "attack voice 1.ogg", _B + "attack voice 2.ogg"], 2),
    "voice_hurt":   _pool_sons([_B + "get hit 1.ogg", _B + "get hit 2.ogg", _B + "get hit 3.ogg"], 2),
    "body_hit":     _pool_sons(_B + "body get hit.ogg", 3),
    "jump":         _pool_sons(_B + "jump voice.ogg", 2),
    "footstep":     _pool_sons([_B + "footstep 1.ogg", _B + "footstep 2.ogg", _B + "footstep 3.ogg"], 2),
    "foot_frames":  (2, 6),
    # MORT en 3 TEMPS, calee sur le SPRITE (frames mesurees) : 1) il LACHE ses armes
    # (elles quittent ses mains ~f11), 2) il HURLE bras leves (bouche ouverte f20 ->
    # fermee f28), 3) il S'EFFONDRE au sol (f30).
    "death":            _pool_sons(_B + "death gears fall.ogg", 2),
    "death_frame":      11,
    "death_cri":        _pool_sons(_B + "death howl.ogg", 2),
    "death_cri_frame":  20,
    "death_cri_fin":    28,
    "death_fall":       _pool_sons(_B + "death body fall.ogg", 2),
    "death_fall_frame": 30,
    # SPIN : whoosh + le MEME HURLEMENT que sa mort (rage), cale sur le sprite : bouche
    # ouverte f9 (1re frame de rotation), coupe a l'outro f23 (il arrete de tourner).
    # JUMP : whoosh seul. spin_hit = impact DEDIE de leurs coups (hors check_collision).
    "spin":     _pool_sons(_B + "spin whoosh (jump and spin).ogg", 2),
    "spin_cri": _pool_sons(_B + "death howl.ogg", 2),
    "spin_cri_debut": 9,
    "spin_cri_fin":   23,
    "spin_hit": _pool_sons(_B + "spin hit sound (jump and spin).ogg", 2),
    # SLAM : le marteau est lance (cri + whoosh pendant son VOL), frappe le sol, puis il
    # FONCE dessus (2e cri + son de dash a ruee_frame).
    "hammer_throw":  _pool_sons(_B + "hammer throw.ogg", 2),
    "hammer_impact": _pool_sons(_B + "hammer hit.ogg", 2),
    "ruee_dash":     _pool_sons(_B + "jump attack hammer throw dash.ogg", 2),
    "ruee_frame": 26,
}

# Les VOIX sont enregistrees plus bas que les autres sons -> on les normalise (chaque
# voix amplifiee pour atteindre ~le niveau max) afin qu'elles ressortent en combat.
for _perso in SONS_PERSO.values():
    for _cle in ("voice_attack", "voice_hurt", "jump", "spear_charge_voice", "spear_throw_voice"):
        if isinstance(_perso.get(_cle), list):
            _perso[_cle] = _normaliser(_perso[_cle])


def _joue(pool, gain=GAIN_COMBAT):
    if pool:
        return jouer_sfx(pool, gain, cat="perso")   # renvoie le Channel
    return None


def _cle_attaque(f):
    """Identite du coup en cours (1/2/3 ou n d'arme), STABLE pendant toute l'attaque.
    Les Fighter de base remettent attack_type a 0 apres la 1re frame -> on retombe sur
    l'ACTION (attack1/attack2 du dico, ou constantes ATTACK1/2/3)."""
    cle = getattr(f, "attack_type", 0) or getattr(f, "attack_action", 0) or getattr(f, "current_weapon", 0)
    if not cle and getattr(f, "attacking", False):
        a = getattr(f, "action", None)
        if hasattr(f, "actions"):                       # Kenshi / Lysandra
            if a == f.actions.get("attack1"):
                cle = 1
            elif a == f.actions.get("attack2"):
                cle = 2
        else:                                           # persos a constantes (Arinya/Stormr)
            for k in (1, 2, 3):
                if a == getattr(f, "ATTACK%d" % k, -999):
                    cle = k
                    break
    return cle


def detecter_sons_combat(f, o):
    """Compare l'etat du combattant 'f' (adversaire 'o') a la frame precedente et
    joue les bruitages : bouclier (generique) + sons propres au perso (swing/choose
    d'arme, voix d'attaque, impact qui touche, douleur, mort, saut, pas)."""
    # ----- BOUCLIER (generique) -----
    blk = getattr(f, "block", False)
    if blk and not f._snd_block:
        jouer_son_combat("shield_up")
    elif not blk and f._snd_block:
        jouer_son_combat("shield_down")
    f._snd_block = blk
    bh = getattr(f, "block_health", 0)
    if bh > f._snd_bh:
        jouer_son_combat("shield_hit")
        _bm = classes.BOUCLIER_MAX
        if any(f._snd_bh < s <= bh for s in (_bm * 0.33, _bm * 0.66)):
            jouer_son_combat("shield_crack")
    f._snd_bh = bh
    bcd = getattr(f, "block_cd", 0)
    if bcd > 0 and f._snd_bcd == 0:
        jouer_son_combat("shield_break")
    f._snd_bcd = bcd
    # PARADE PARFAITE (marquee par riposte_parade cote classes) : son de clang
    pf = getattr(f, "_fx_parade", 0)
    if pf and pf != f._snd_parade:
        jouer_son_combat("shield_hit")
        f._snd_parade = pf
    # usure du bouclier qui se RESORBE (regen hors blocage) -> particules de reparation
    if bh > 0 and not getattr(f, "block", False):
        f._fx_rep_t -= 1
        if f._fx_rep_t <= 0:
            f._fx_rep_t = 14
            spawn_particule("shield_repair",
                            f.rect.centerx + random.randint(-f.rect.width // 3, f.rect.width // 3),
                            f.rect.centery + random.randint(-f.rect.height // 4, f.rect.height // 4),
                            f.flip, 48)
    # garde BRISEE en recharge -> particules de reforge SUPERPOSEES au perso (facon sparks)
    if bcd > 0:
        f._fx_rel_t -= 1
        if f._fx_rel_t <= 0:
            f._fx_rel_t = 8
            spawn_particule("shield_reload",
                            f.rect.centerx + random.randint(-f.rect.width // 3, f.rect.width // 3),
                            f.rect.centery + random.randint(-f.rect.height // 3, f.rect.height // 3),
                            f.flip, 55)

    perso = SONS_PERSO.get(getattr(f, "_nom", None))    # sons de f (None si pas encore de sons)

    # ----- Attaque : cri au DEBUT ; swing a la frame du slash (swing_frame, 1-indexed,
    # par coup) sinon des le debut. Gere les combos. -----
    atk = getattr(f, "attacking", False)
    cle = _cle_attaque(f)
    nouveau_coup = atk and perso and (not f._snd_atk or cle != f._snd_cle)
    if nouveau_coup:
        # cri a CHAQUE coup, enchainements de combo inclus (avant : seulement au 1er coup)
        _joue(perso.get("voice_attack"), perso.get("gain_voix", GAIN_VOIX))
        _joue(perso.get("lightning"))                         # epee electrique (Stormr) sur l'attaque
        f._snd_swing_ok = False                               # swing de ce coup pas encore joue
    if atk and perso and not f._snd_swing_ok:
        sf = perso.get("swing_frame")
        sf = sf.get(cle) if isinstance(sf, dict) else sf      # frame (1-indexed) du slash, ou None = direct
        if sf is None or (getattr(f, "frame_index", 0) + 1) >= sf:
            sw = perso.get("swing")
            _joue(sw.get(cle) if isinstance(sw, dict) else sw)
            f._snd_swing_ok = True
    f._snd_atk = atk
    f._snd_cle = cle if atk else None
    # ----- Lysandra : l'abattee de M2 FRAPPE LE SOL -> l'impact sonne MEME dans le vide
    # (sinon le coup est plat, comme le marteau de Barrion). Pas de doublon : si le coup
    # a connecte (damage_dealt), le son de hit normal / bouclier joue deja. -----
    cfg_f = getattr(f, "config", None)
    if (atk and perso and cfg_f and cfg_f.get("seisme")
            and getattr(f, "action", None) == getattr(f, "actions", {}).get("attack2")
            and not getattr(f, "_snd_sol_ok", False)
            # 1 frame AVANT la frame de degats : c'est la que la lame touche le sol
            # visuellement (au frame de degats, le son arrivait en retard). JAMAIS
            # pendant le GEL de charge (la lame est en l'air, figee a cette frame !).
            and not getattr(f, "_seisme_gel", False)
            and getattr(f, "frame_index", 0) >= cfg_f["attacks"][2]["frame"] - 1
            and not getattr(f, "damage_dealt", False)):
        h = perso.get("hit")
        _joue(h.get(2) if isinstance(h, dict) else h)
        f._snd_sol_ok = True
    if not atk:
        f._snd_sol_ok = False
    # ----- Lysandra : CHARGE SISMIQUE -- voix + grondement au DEBUT du gel, coupes a la
    # RELACHE (+ voix), et EXPLOSION uniquement a PLEINE puissance (meme dans le vide). -----
    if perso and cfg_f and cfg_f.get("seisme"):
        gel = getattr(f, "_seisme_gel", False)
        if gel and not getattr(f, "_snd_gel", False):     # la charge DEMARRE
            _joue(perso.get("voice_attack"), perso.get("gain_voix", GAIN_VOIX))
            f._canal_seisme = _joue(perso.get("seisme_charge"))
        if not gel and getattr(f, "_snd_gel", False):     # elle RELACHE
            if getattr(f, "_canal_seisme", None) is not None:
                f._canal_seisme.fadeout(90)               # coupe le grondement NET
                f._canal_seisme = None
            _joue(perso.get("voice_attack"), perso.get("gain_voix", GAIN_VOIX))
            if getattr(f, "_seisme_perce", False):        # PLEINE charge -> le sol explose
                _joue(perso.get("seisme_explosion"))
        f._snd_gel = gel
    # ----- Kenshi : FLOW -- gain de stack (aleatoire), plein regime (3e), stack perdu. -----
    fl = getattr(f, "flow", None)
    if fl is not None and perso and perso.get("flow_stack"):
        prev_fl = getattr(f, "_snd_flow", 0)
        if fl > prev_fl:
            _joue(perso.get("flow_max") if fl >= Kenshi.FLOW_MAX else perso.get("flow_stack"))
        elif fl < prev_fl:
            _joue(perso.get("flow_lose"))
        f._snd_flow = fl
    # ----- Changement d'arme (Konrad) : 'choose' -----
    wpn = getattr(f, "current_weapon", None)
    if wpn is not None and wpn != f._snd_wpn:
        if perso and isinstance(perso.get("choose"), dict):
            _joue(perso["choose"].get(wpn))
        f._snd_wpn = wpn
    # ----- Saut : voix au DECOLLAGE, pas a l'ATTERRISSAGE -----
    jmp = getattr(f, "jump", False)
    if perso:
        if jmp and not f._snd_jump:
            _joue(perso.get("jump"), perso.get("gain_voix", GAIN_VOIX))   # decolle (grognement = voix)
        elif not jmp and f._snd_jump:
            _joue(perso.get("footstep"))    # retombe au sol -> un pas
    f._snd_jump = jmp
    # ----- Prend un coup NON bloque : f crie + impact corps ; l'ATTAQUANT (o) joue son weapon
    # hit. Un coup AMORTI (grace post-stun / super armor : pas de stun) joue QUAND MEME tous
    # les sons d'impact (+ un clang de bouclier) -> le feedback reste percutant. -----
    hit = getattr(f, "hit", False)
    amorti = getattr(f, "_hit_amorti", False)
    if (hit and not f._snd_hit) or amorti:
        if perso:
            _joue(perso.get("voice_hurt"), perso.get("gain_voix", GAIN_VOIX))
            _joue(perso.get("body_hit"))
        po = SONS_PERSO.get(getattr(o, "_nom", None))
        if po and getattr(o, "attacking", False):
            h = po.get("hit")
            if isinstance(h, dict):   # par arme / par coup
                _joue(h.get(_cle_attaque(o)))
            else:                     # Stormr : pool simple (epee electrique)
                _joue(h)
            if getattr(o, "_lame_derniere", 0) == 2:      # PASSE-LAME (Kenshi) : slice de
                _joue(po.get("backstab"))                 # backstab SIMULTANE au hit
        if po and (getattr(o, "spinning", False) or getattr(o, "jumping_attack", False)):
            _joue(po.get("spin_hit"))  # impact DEDIE des coups du spin/jump (Barrion)
        if amorti:
            jouer_son_combat("shield_hit")   # clang d'absorption (coup encaisse sans stun)
        f._hit_amorti = False
    f._snd_hit = hit
    # ----- Mort (death2 = 2e son SUPERPOSE, ex : la dissolution de lumiere d'Oswald).
    # Mort SEQUENCEE (ex Barrion) : "death" a la transition (il LACHE ses armes), puis
    # "death_cri" a death_cri_frame (il HURLE), puis "death_fall" a death_fall_frame
    # (il S'EFFONDRE au sol). Chaque etape est optionnelle (presence de la cle). -----
    alive = getattr(f, "alive", True)
    if not alive and f._snd_alive and perso:
        if "death_frame" not in perso:                    # sinon "death" joue a death_frame
            _joue(perso.get("death"))
        _joue(perso.get("death2"))
        f._snd_death0 = "death_frame" in perso
        f._snd_death_cri = False
        f._snd_death_fall = False
    f._snd_alive = alive
    if not alive and perso:
        fi_d = getattr(f, "frame_index", 0)
        if f._snd_death0 and fi_d >= perso.get("death_frame", 0):
            _joue(perso.get("death"))                     # ex Barrion : ses armes TOMBENT (f11)
            f._snd_death0 = False
        if ("death_cri" in perso and not f._snd_death_cri
                and fi_d >= perso.get("death_cri_frame", 5)):
            f._canal_death_cri = _joue(perso.get("death_cri"), perso.get("gain_voix", GAIN_VOIX))
            f._snd_death_cri = True
        if getattr(f, "_canal_death_cri", None) and fi_d >= perso.get("death_cri_fin", 10 ** 9):
            f._canal_death_cri.fadeout(150)               # bouche FERMEE -> le hurlement s'arrete
            f._canal_death_cri = None
        if ("death_fall" in perso and not f._snd_death_fall
                and fi_d >= perso.get("death_fall_frame", 10)):
            _joue(perso.get("death_fall"))
            if getattr(f, "_canal_death_cri", None):      # securite : coupe si encore actif
                f._canal_death_cri.fadeout(150)
                f._canal_death_cri = None
            f._snd_death_fall = True
    # ----- Barrion : SPIN (whoosh + HURLEMENT) / JUMP-ATTACK (whoosh seul) / SLAM (cri au
    # lancer du marteau + cri quand il FONCE dessus + impact au sol) -----
    if perso and "spin" in perso:
        # Les sons LONGS (whoosh, hurlement) sont gardes en CANAL et COUPES en fondu des
        # que l'etat qui les portait s'arrete (fin/interruption d'anim) -> pas de son qui
        # continue bouche fermee / apres la fin du spin ou du saut.
        sp = getattr(f, "spinning", False)
        if sp and not f._snd_spin:
            f._canal_spin = _joue(perso.get("spin"))      # whoosh du SPIN (immediat)
            f._snd_spin_cri = False
        elif not sp and f._snd_spin:                      # fin/interruption -> on coupe tout
            for c in (f._canal_spin, f._canal_spin_cri):
                if c:
                    c.fadeout(120)
            f._canal_spin = f._canal_spin_cri = None
        f._snd_spin = sp
        # HURLEMENT cale sur le SPRITE : demarre quand la bouche s'ouvre (spin_cri_debut,
        # 1re frame de rotation), coupe quand il arrete de tourner (spin_cri_fin, l'outro).
        if sp:
            fi_sp = getattr(f, "frame_index", 0)
            if not f._snd_spin_cri and fi_sp >= perso.get("spin_cri_debut", 0):
                f._canal_spin_cri = _joue(perso.get("spin_cri"), perso.get("gain_voix", GAIN_VOIX))
                f._snd_spin_cri = True
            if f._canal_spin_cri and fi_sp >= perso.get("spin_cri_fin", 10 ** 9):
                f._canal_spin_cri.fadeout(120)
                f._canal_spin_cri = None
        # JUMP-ATTACK : whoosh au DECOLLAGE seulement (pas pendant l'accroupissement), et
        # a l'ATTERRISSAGE le marteau frappe le sol -> son d'impact (sauf slam : il a le sien).
        ja_actif = getattr(f, "jumping_attack", False) and not getattr(f, "hit_down", False)
        envol = ja_actif and getattr(f, "jump", False)
        if envol and not f._snd_jwhoosh:
            f._canal_jwhoosh = _joue(perso.get("spin"))
        if f._snd_jwhoosh and not envol:
            if f._canal_jwhoosh:                          # atterrissage OU slam caste -> coupe
                f._canal_jwhoosh.fadeout(100)
                f._canal_jwhoosh = None
            if ja_actif:
                _joue(perso.get("sol_hit"))
        f._snd_jwhoosh = envol
        # le MARTEAU frappe le SOL a la frame d'impact d'attack2 -> son SYSTEMATIQUE
        # (touche ou pas ; attack2 est exclu de "hit" -> pas de double lecture).
        if (getattr(f, "attacking", False)
                and getattr(f, "attack_type", 0) in perso.get("sol_hit_coups", ())):
            atkc = getattr(f, "config", {}).get("attacks", {}).get(f.attack_type, {})
            if not f._snd_sol and getattr(f, "frame_index", 0) >= atkc.get("frame", 0):
                _joue(perso.get("sol_hit"))
                f._snd_sol = True
        else:
            f._snd_sol = False
        hd = getattr(f, "hit_down", False)
        if hd and not f._snd_hthrow:
            _joue(perso.get("hammer_throw"))              # le marteau est LANCE...
            _joue(perso.get("voice_attack"), perso.get("gain_voix", GAIN_VOIX))   # ... avec un cri
            f._canal_hwhoosh = _joue(perso.get("spin"))   # ... et le whoosh du marteau EN VOL
            f._snd_himpact = False
            f._snd_ruee = False
        f._snd_hthrow = hd
        if hd:
            ja = getattr(f, "config", {}).get("jump_attack", {})
            fi_s = getattr(f, "frame_index", 0)
            if not f._snd_himpact and fi_s >= ja.get("hammer_from", 20):
                _joue(perso.get("hammer_impact"))         # il FRAPPE le sol (onde de choc)
                if f._canal_hwhoosh:                      # le marteau a atterri -> fin du whoosh
                    f._canal_hwhoosh.fadeout(100)
                    f._canal_hwhoosh = None
                f._snd_himpact = True
            if not f._snd_ruee and fi_s >= perso.get("ruee_frame", 26):
                _joue(perso.get("voice_attack"), perso.get("gain_voix", GAIN_VOIX))   # il FONCE
                _joue(perso.get("ruee_dash"))             # ... avec son bruit de dash
                f._snd_ruee = True
    # ----- Stormr : crepitement de charge sur l'ennemi (BOUCLE aleatoire, cadence selon
    # la charge, comme les particules) + foudre du finisher -----
    if perso and "spark" in perso:
        ch = getattr(f, "enemy_charge", 0)
        cmax = getattr(f, "CHARGE_MAX", 100)
        if ch > 3:
            f._snd_spark_t -= 1
            if f._snd_spark_t <= 0:
                jouer_sfx(perso["spark"], GAIN_SPARK, cat="perso")   # 1/2/3 au hasard
                ratio = min(1.0, ch / cmax)                      # plus charge -> plus rapide
                f._snd_spark_t = max(3, int(SPARK_LENT - (SPARK_LENT - SPARK_RAPIDE) * ratio))
        else:
            f._snd_spark_t = 0
    if perso and "thunder" in perso:
        a_foudre = getattr(f, "lightning", None) is not None
        if a_foudre and not f._snd_light:
            _joue(perso.get("thunder"))
        f._snd_light = a_foudre
    # ----- Oswald : TELEPORT (au blink) + SWORD OF JUSTICE (quand l'epee s'abat) -----
    if perso and "teleport" in perso:
        tp = getattr(f, "teleporting", False)
        if tp and not f._snd_tp:
            _joue(perso.get("teleport"))
        f._snd_tp = tp
        sw = getattr(f, "sword", None)
        imp = bool(sw and sw.get("dmg"))               # "dmg" passe True pile a l'impact
        if imp and not f._snd_sword:
            _joue(perso.get("sword_impact"))
        f._snd_sword = imp
    # ----- Arinya : LANCE (charge / lancer / vol / impact / plantage / rebond /
    # ramassage) + DASH -----
    if perso and "spear_throw" in perso:
        chg = getattr(f, "charging", False)
        if chg and not f._snd_charging:
            f._canal_charge = _joue(perso.get("spear_charge_voice"), perso.get("gain_voix", GAIN_VOIX))
        f._snd_charging = chg
        sp = getattr(f, "spear", None)
        if sp is not None and not f._snd_spear:           # la lance vient d'etre lancee
            if f._canal_charge is not None:               # coupe la voix de charge si pas finie
                f._canal_charge.fadeout(60)
                f._canal_charge = None
            _joue(perso.get("spear_throw"))
            _joue(perso.get("spear_throw_voice"), perso.get("gain_voix", GAIN_VOIX))
            if f._canal_flight is not None:               # securite : coupe un eventuel ancien sifflement
                f._canal_flight.stop()
            f._canal_flight = jouer_sfx(perso.get("spear_flight"), GAIN_COMBAT,
                                        cat="perso", loops=-1)   # sifflement EN BOUCLE
            f._snd_spear_hd = f._snd_spear_gr = f._snd_spear_bo = False
        f._snd_spear = sp is not None
        if sp is not None:                                # etats de la lance en vol
            hd, gr, bo = sp.get("hit_done", False), sp.get("grounded", False), sp.get("bounced", False)
            if hd and not f._snd_spear_hd:
                _joue(perso.get("spear_impale"))
            if gr and not f._snd_spear_gr:                # touche le sol -> stop le sifflement
                _joue(perso.get("spear_ground"))
                if f._canal_flight is not None:
                    f._canal_flight.fadeout(80)
                    f._canal_flight = None
            if bo and not f._snd_spear_bo:
                _joue(perso.get("spear_bounce"))
            f._snd_spear_hd, f._snd_spear_gr, f._snd_spear_bo = hd, gr, bo
        elif f._canal_flight is not None:                 # lance disparue sans toucher le sol -> coupe
            f._canal_flight.fadeout(80)
            f._canal_flight = None
        hs = getattr(f, "has_spear", True)
        if hs and not f._snd_has_spear:                   # ramassage
            _joue(perso.get("spear_pickup"))
        f._snd_has_spear = hs
    # ----- Dash : son au DEPART (generique : double-tap d'Arinya, TRAVERSEE de Kenshi) -----
    dsh = getattr(f, "dashing", False)
    if dsh and not getattr(f, "_snd_dashing", False) and perso:
        _joue(perso.get("dash"))
    f._snd_dashing = dsh
    # ----- Pas : synchro sur les frames de contact des pieds de l'anim de COURSE -----
    fr = getattr(f, "frame_index", -1)
    run_acts = set()                               # plusieurs actions de course possibles
    for _attr in ("RUN", "RUN_WS"):                #   (Arinya : avec / sans lance)
        if hasattr(f, _attr):
            run_acts.add(getattr(f, _attr))
    if not run_acts and hasattr(f, "actions"):     # Kenshi/Lysandra : dico d'actions
        run_acts.add(f.actions.get("run"))
    en_course = getattr(f, "action", None) in run_acts and not jmp
    if perso and en_course and fr != f._snd_frame:
        if (fr + 1) in perso.get("foot_frames", ()):
            _joue(perso.get("footstep"))
        if (fr + 1) in perso.get("drag_frames", ()):
            # l'epee de Barrion racle le sol (pitch varie, volume reduit via drag_gain)
            _joue(perso.get("drag"), perso.get("drag_gain", GAIN_COMBAT))
    f._snd_frame = fr

# Couleurs
GOLD = (205, 170, 69)
RED_BROWN = (75, 61, 61)
NOIR = (28, 28, 46)
WHITE = (255, 255, 255)
GRAY = (150, 150, 150)
GOLD_DARK = (150, 120, 40)

# États du jeu
MENU_ACCUEIL = "menu_accueil"
SELECTION = "selection"
SELECTION_MAP = "selection_map"
MULTIJOUEUR = "multijoueur"
JEU = "jeu"
PAUSE = "pause"
BATTLE_MENU = "battle_menu"   # sous-menu de "Enter the Battle" (Training / Local / LAN)
TRAINING = "training"         # mode entrainement (mannequin sur le temple day)

# Registre des personnages jouables : nom affiché -> classe.
# Pour ajouter un perso au jeu et au menu, il suffit d'ajouter une ligne ici.
PERSONNAGES = {
    "Kenshi": Kenshi,
    "Lysandra": Lysandra,
    "Konrad": KonradForgeval,
    "Arinya": Arinya,
    "Stormr": Stormr,
    "Oswald": Oswald,
    "Barrion": Barrion,
}
NOMS_PERSOS = list(PERSONNAGES.keys())

# Son de "choose" par perso (joue a la selection). Fichier "<Nom> choose.mp3" dans ui.
# Sans variation de pitch (n=1).
SFX_CHOOSE_PERSO = {nom: _charger_sfx_pitch("assets/audio/ui/%s choose.mp3" % nom, n=1)
                    for nom in NOMS_PERSOS}

# Effet "trail" des barres de vie (façon jeu de combat) :
TRAIL_DELAY = 30   # frames d'attente après un coup avant que le rouge se résorbe (~1 s)
TRAIL_SPEED = 3    # vitesse de résorption du rouge (HP par frame)

ANIMATION_FOLDER = "assets/BACKGROUND/map/Grimgate Chapel"     # dossier de frames par defaut (fallback)
ANIMATION_SPEED = 5            # cadence par defaut, en frames de jeu (5/30 = 0,167 s/frame)
anim_speed = ANIMATION_SPEED   # cadence ACTIVE (frames de jeu) ; reglee par jeu() selon la map
ANIMATION_FRAMES_LIST = ["1.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg", "6.jpg", "7.jpg", "8.jpg"]

# Polices
font_title = pygame.font.Font("assets/fonts/OldLondon.ttf", 130)
font_title1 = pygame.font.Font("assets/fonts/OldLondon.ttf", 75)
font_title2 = pygame.font.Font("assets/fonts/OldLondon.ttf", 150)
font_small = pygame.font.Font("assets/fonts/OldLondon.ttf", 50)
font_medium = pygame.font.Font("assets/fonts/OldLondon.ttf", 70)
_f_version = pygame.font.SysFont("segoeui,arial", 20)   # version (police CLASSIQUE, coin du menu)


# ----------------------------------------------------------------------
#  FOND DU MENU PRINCIPAL
#  Ciel en PARALLAX (suit la souris) + 1er plan FIXE + BROUILLARD procedural
#  anime (2 nappes molles qui derivent en sens opposes + ondulent = flottement).
# ----------------------------------------------------------------------
PARALLAX_MX, PARALLAX_MY = 32, 12   # amplitude max du parallax, en px (vertical tres reduit)
try:
    _sky = pygame.image.load("assets/BACKGROUND/main menu/sky.png").convert()
    SKY_IMG = pygame.transform.smoothscale(
        _sky, (SCREEN_WIDTH + 2 * PARALLAX_MX, SCREEN_HEIGHT + 2 * PARALLAX_MY))
    _fg = pygame.image.load("assets/BACKGROUND/main menu/castle and ground.png").convert_alpha()
    FG_IMG = pygame.transform.smoothscale(_fg, (SCREEN_WIDTH, SCREEN_HEIGHT))
except Exception:
    SKY_IMG = FG_IMG = None


def _blob_brouillard(r, couleur, a_max):
    """Une nappe molle (disque a bords degrades) servant de 'bouffee' de brume."""
    s = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)
    n = max(8, r // 3)
    for i in range(n):
        a = int(a_max * (i / n) ** 2)           # alpha croit vers le centre (bords doux)
        rad = int(r * (1 - i / n))
        if rad > 0:
            pygame.draw.circle(s, (couleur[0], couleur[1], couleur[2], a), (r, r), rad)
    return s


def _couche_brouillard(largeur, hauteur, n, bande_y, bande_h, couleur, a_max, rmin, rmax, graine):
    """Couche de brume TILEABLE horizontalement (les blobs sont reproduits aux
    bords -> defilement sans couture). Deterministe (meme rendu a chaque lancement)."""
    surf = pygame.Surface((largeur, hauteur), pygame.SRCALPHA)
    rng = random.Random(graine)
    for _ in range(n):
        r = rng.randint(rmin, rmax)
        x = rng.randint(0, largeur)
        y = bande_y + rng.randint(-bande_h // 2, bande_h // 2)
        b = _blob_brouillard(r, couleur, rng.randint(a_max // 2, a_max))
        for ox in (0, -largeur, largeur):
            surf.blit(b, (x + ox - r, y - r))
    return surf


if SKY_IMG is not None:
    FOG_W = SCREEN_WIDTH
    # Surface plus HAUTE que l'ecran : la brume deborde sous le bas de l'ecran,
    # donc son bord inferieur n'apparait jamais, meme quand l'ondulation la fait monter.
    FOG_H = SCREEN_HEIGHT + 220
    FOG_BACK = _couche_brouillard(FOG_W, FOG_H, 40, int(SCREEN_HEIGHT * 0.66), 200,
                                  (152, 158, 172), 64, 120, 250, 1)
    FOG_FRONT = _couche_brouillard(FOG_W, FOG_H, 38, int(SCREEN_HEIGHT * 0.96), 340,
                                   (180, 184, 198), 56, 160, 300, 2)
    VEIL = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
    for _yy in range(SCREEN_HEIGHT):
        _a = int(135 * (_yy / SCREEN_HEIGHT) ** 2)   # assombrit vers le bas (boutons + ambiance)
        pygame.draw.line(VEIL, (8, 8, 12, _a), (0, _yy), (SCREEN_WIDTH, _yy))
else:
    FOG_BACK = FOG_FRONT = VEIL = None

# Fond du menu OPTIONS (depuis l'accueil) : la forge + une petite brume qui derive.
try:
    _fb = pygame.image.load("assets/BACKGROUND/Options/forge.png").convert()
    FORGE_BG = pygame.transform.smoothscale(_fb, (SCREEN_WIDTH, SCREEN_HEIGHT))
    FOG_OPT = _couche_brouillard(SCREEN_WIDTH, SCREEN_HEIGHT, 34, int(SCREEN_HEIGHT * 0.62), 440,
                                 (152, 154, 164), 58, 150, 320, 5)
except Exception:
    FORGE_BG = FOG_OPT = None

# Fond du menu CHOOSE (selection) : nouveau fond + une brume qui derive (comme Options).
try:
    _cb = pygame.image.load("assets/BACKGROUND/choose/choose bg.png").convert()
    CHOOSE_BG = pygame.transform.smoothscale(_cb, (SCREEN_WIDTH, SCREEN_HEIGHT))
    FOG_CHOOSE = _couche_brouillard(SCREEN_WIDTH, SCREEN_HEIGHT, 34, int(SCREEN_HEIGHT * 0.62), 440,
                                    (152, 154, 164), 58, 150, 320, 7)
except Exception:
    CHOOSE_BG = FOG_CHOOSE = None

# Fond DEDIE du choix de perso avant l'entrainement (sinon on retombe sur CHOOSE_BG).
try:
    _ctb = pygame.image.load("assets/BACKGROUND/choose training/choose training bg.png").convert()
    CHOOSE_TRAIN_BG = pygame.transform.smoothscale(_ctb, (SCREEN_WIDTH, SCREEN_HEIGHT))
except Exception:
    CHOOSE_TRAIN_BG = None


def rendu_fond_principal(par_dx, par_dy, t):
    """Fond du menu : ciel parallax + brume arriere + 1er plan fixe + brume avant.
    par_dx/par_dy = decalage parallax deja lisse ; t = compteur de frames."""
    if SKY_IMG is None:
        draw_background_animation(0)
        return
    screen.blit(SKY_IMG, (-PARALLAX_MX + par_dx, -PARALLAX_MY + par_dy))
    # Brume ARRIERE (entre ciel et 1er plan), derive nette vers la droite + ondulation
    off1 = int((t * 1.1) % FOG_W)
    y1 = int(math.sin(t * 0.030) * 20)
    screen.blit(FOG_BACK, (off1 - FOG_W, y1)); screen.blit(FOG_BACK, (off1, y1))
    # 1er plan FIXE (chateau + sol)
    screen.blit(FG_IMG, (0, 0))
    # Brume AVANT (devant le 1er plan), derive vers la gauche, plus rapide + grosse ondulation
    off2 = int((t * 0.8) % FOG_W)
    y2 = int(math.sin(t * 0.024 + 1.6) * 26)
    screen.blit(FOG_FRONT, (-off2, y2)); screen.blit(FOG_FRONT, (FOG_W - off2, y2))
    screen.blit(VEIL, (0, 0))


# Police "acier" des boutons du menu principal (assortie au logo)
font_bouton = pygame.font.Font("assets/fonts/OldLondon.ttf", 46)

_polices_acier = {}   # cache des polices Old London par taille


def police_acier(taille):
    """Police Old London a la taille demandee (mise en cache)."""
    taille = max(10, int(taille))
    if taille not in _polices_acier:
        _polices_acier[taille] = pygame.font.Font("assets/fonts/OldLondon.ttf", taille)
    return _polices_acier[taille]


def police_bouton(rect, texte):
    """Police calee sur la HAUTEUR du bouton (texte d'autant plus imposant que le
    bouton est grand), reduite si le texte deborde en largeur."""
    taille = int(rect.height * 0.72)
    f = police_acier(taille)
    while taille > 16 and f.size(texte)[0] > rect.width - 46:
        taille -= 2
        f = police_acier(taille)
    return f


def dessiner_bouton_metal(surf, rect, texte, hover, police=None):
    """Bouton 'plaque de fer abimee' : biseau, rivets, rayures, texte grave.
    Le fer reprend la PALETTE CHAUDE de l'epee du logo (acier beige/taupe).
    Au survol : la plaque s'eclaircit et la bordure + le texte virent a la braise."""
    x, y, w, h = rect.x, rect.y, rect.width, rect.height
    base = (140, 129, 110) if hover else (110, 102, 87)                 # fer chaud (cf epee du logo)
    clair = (min(255, base[0] + 34), min(255, base[1] + 32), min(255, base[2] + 28))
    fonce = (int(base[0] * 0.6), int(base[1] * 0.6), int(base[2] * 0.6))
    pygame.draw.rect(surf, (15, 14, 12), rect.inflate(8, 8), 0, 7)      # ombre portee / cadre
    pygame.draw.rect(surf, base, rect, 0, 6)                            # plaque
    pygame.draw.rect(surf, clair, (x + 4, y + 3, w - 8, 6), 0, 4)       # reflet haut
    pygame.draw.rect(surf, fonce, (x + 4, y + h - 9, w - 8, 6), 0, 4)   # ombre bas
    bord = (200, 124, 50) if hover else (171, 156, 134)                 # braise au survol, acier sinon
    pygame.draw.rect(surf, (26, 23, 18), rect, 3, 6)
    pygame.draw.rect(surf, bord, rect, 2, 6)
    for rx, ry in ((x + 14, y + 14), (x + w - 14, y + 14),
                   (x + 14, y + h - 14), (x + w - 14, y + h - 14)):     # rivets
        pygame.draw.circle(surf, (70, 63, 52), (rx, ry), 4)
        pygame.draw.circle(surf, (176, 162, 138), (rx, ry), 4, 1)
    pygame.draw.line(surf, (190, 177, 153), (x + 24, y + h - 16), (x + 52, y + 12), 1)   # rayures
    pygame.draw.line(surf, (54, 48, 40), (x + w - 56, y + h - 13), (x + w - 26, y + 15), 1)
    coul = (236, 218, 180) if hover else (214, 202, 176)               # texte grave (reflet de l'epee)
    if police is None:
        police = police_bouton(rect, texte)
    ombre = police.render(texte, True, (26, 22, 16))
    haut = police.render(texte, True, coul)
    # Centrage OPTIQUE : boite d'ENCRE des lettres, pas la surface (Old London
    # a beaucoup de vide au-dessus des glyphes -> sinon le texte tombe trop bas).
    bb = haut.get_bounding_rect()
    px = rect.centerx - bb.w // 2 - bb.x
    py = rect.centery - bb.h // 2 - bb.y
    surf.blit(ombre, (px + 2, py + 3))
    surf.blit(haut, (px, py))


# ----------------------------------------------------------------------
#  CURSEUR PERSONNALISE : main tenant une lanterne + halo de lumiere chaud
#  (blit ADDITIF) qui semble emaner de la lanterne et VACILLE legerement.
# ----------------------------------------------------------------------
try:
    _ptr = pygame.image.load("assets/ui assets/pointer.png").convert_alpha()
    _ptr = _ptr.subsurface(_ptr.get_bounding_rect()).copy()   # recadre sur le visible
    _CUR_H = 92
    _CUR_W = max(1, round(_ptr.get_width() * _CUR_H / _ptr.get_height()))
    # Miroir horizontal : la main passe a DROITE (lanterne en bas a gauche)
    CURSEUR = pygame.transform.flip(pygame.transform.smoothscale(_ptr, (_CUR_W, _CUR_H)), True, False)
    CURSEUR_HOT = (int(_CUR_W * 0.45), int(_CUR_H * 0.20))    # bout des doigts (X inverse) = point de clic
    CURSEUR_GLOW = (int(_CUR_W * 0.38), int(_CUR_H * 0.53))   # centre de la lanterne (X inverse)
    _GR = 120
    GLOW = pygame.Surface((2 * _GR, 2 * _GR), pygame.SRCALPHA)
    for _rad in range(_GR, 0, -1):
        _f = (1 - _rad / _GR) ** 2.2                          # lumiere qui s'eteint vers les bords
        pygame.draw.circle(GLOW, (int(208 * _f), int(134 * _f), int(58 * _f)), (_GR, _GR), _rad)
    pygame.mouse.set_visible(False)
except Exception:
    CURSEUR = GLOW = None

_cur_t = 0


def dessiner_curseur(surf):
    """Halo de lanterne (additif, vacillant) + curseur, a la position de la souris.
    A appeler en DERNIER (juste avant flip) dans les ecrans de menu."""
    global _cur_t
    if CURSEUR is None:
        return
    _cur_t += 1
    mx, my = pygame.mouse.get_pos()
    gx = mx - CURSEUR_HOT[0] + CURSEUR_GLOW[0]
    gy = my - CURSEUR_HOT[1] + CURSEUR_GLOW[1]
    f = 1.0 + 0.08 * math.sin(_cur_t * 0.22) + random.uniform(-0.05, 0.05)   # vacillement
    gw = max(1, int(GLOW.get_width() * f))
    halo = pygame.transform.smoothscale(GLOW, (gw, gw))
    surf.blit(halo, halo.get_rect(center=(gx, gy)), special_flags=pygame.BLEND_RGB_ADD)
    surf.blit(CURSEUR, (mx - CURSEUR_HOT[0], my - CURSEUR_HOT[1]))


def blit_alpha(surf, image, pos, alpha):
    """Blit une image (a alpha par pixel) en la fondant globalement a 'alpha' (0..255)."""
    alpha = max(0, min(255, int(alpha)))
    if alpha >= 255:
        surf.blit(image, pos)
    elif alpha > 0:
        tmp = image.copy()
        tmp.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(tmp, pos)


# ----------------------------------------------------------------------
#  TRANSITION ENTRE ECRANS : une brume ENVAHIT l'ecran (couvrir) puis SE
#  DISPERSE pour reveler le nouvel ecran.
# ----------------------------------------------------------------------
BRUME_DISPERSE = False
_canal_trans = None   # canal du whoosh en cours (pour fondre cape -> wind -> ecran)
try:
    # Texture de NUAGE PLUS GRANDE que l'ecran (ses bords restent hors-champ
    # meme a l'echelle mini -> on ne voit jamais le rectangle de l'image).
    _CW, _CH = 1780, 1060
    CLOUD = pygame.Surface((_CW, _CH), pygame.SRCALPHA)
    _rng = random.Random(11)
    for _ in range(72):
        _r = _rng.randint(120, 300)
        _br = _rng.randint(224, 248)
        _b = _blob_brouillard(_r, (_br, _br, min(255, _br + 6)), _rng.randint(80, 175))
        CLOUD.blit(_b, (_rng.randint(0, _CW) - _r, _rng.randint(0, _CH) - _r))
    # Voile (legerement plus gris que les nuages -> les bouffees ressortent).
    VEIL_TRANS = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    VEIL_TRANS.fill((198, 204, 216))
except Exception:
    CLOUD = VEIL_TRANS = None


def _draw_nuage(scale, a_cloud, a_veil):
    """Voile + texture nuage ZOOMEE (centree) -> effet 'on plonge dans le nuage'.
    A l'echelle 1.0 la texture (1780x1060) couvre deja l'ecran avec de la marge."""
    if a_veil > 0:
        VEIL_TRANS.set_alpha(min(255, a_veil))
        screen.blit(VEIL_TRANS, (0, 0))
    if a_cloud > 8:
        big = pygame.transform.smoothscale(
            CLOUD, (int(CLOUD.get_width() * scale), int(CLOUD.get_height() * scale)))
        blit_alpha(screen, big, big.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)),
                   min(255, a_cloud))


def transition_couvrir():
    """On PLONGE dans les nuages : ils grossissent depuis le centre et envahissent."""
    global _canal_trans
    if CLOUD is None:
        return
    # Les DEUX whoosh (cape + vent) joues ENSEMBLE, superposes, sur toute la transition (~5 s).
    _canal_trans = [c for c in (jouer_sfx(SFX_TRANS_COUVRIR, GAIN_TRANSITION),
                                jouer_sfx(SFX_TRANS_DISPERSER, GAIN_TRANSITION)) if c is not None]
    avant = screen.copy()
    N = 18   # ~0,6 s (couvrir + disperser ≈ 1,3 s, transition raccourcie)
    for i in range(1, N + 1):
        clock.tick(FPS)
        pygame.event.pump()
        p = i / N
        screen.blit(avant, (0, 0))
        _draw_nuage(1.0 + 0.55 * p, int(255 * min(1.0, p * 1.5)), int(255 * p ** 1.7))
        pygame.display.flip()


def transition_disperser():
    """Les nuages CONTINUENT de defiler vers nous puis SE DISSIPENT (revele l'ecran)."""
    global _canal_trans
    if CLOUD is None:
        return
    apres = screen.copy()
    N = 20   # ~0,7 s (suite de "couvrir" ; transition totale ≈ 1,3 s)
    for j in range(N + 1):
        clock.tick(FPS)
        pygame.event.pump()
        p = j / N
        screen.blit(apres, (0, 0))
        _draw_nuage(1.55 + 0.6 * p, int(255 * (1 - p)), int(255 * (1 - p) ** 1.3))
        pygame.display.flip()
    screen.blit(apres, (0, 0))
    for ch in (_canal_trans or []):    # fondu en fin de transition (coupe douce des whoosh)
        if ch is not None:
            ch.fadeout(450)
    _canal_trans = []


def disperser_si_besoin():
    """A appeler dans un ecran juste avant le flip : disperse la brume une seule
    fois, si une transition vient de couvrir l'ecran."""
    global BRUME_DISPERSE
    if BRUME_DISPERSE:
        BRUME_DISPERSE = False
        transition_disperser()


# Logo du STUDIO (splash de lancement) : trace clair, s'affiche sur fond noir.
try:
    _stu = pygame.image.load("assets/ui assets/Ehyria Studio logo.png").convert_alpha()
    _stu = _stu.subsurface(_stu.get_bounding_rect()).copy()   # recadre sur le visible
    _stu_w = 780
    _stu_h = max(1, round(_stu.get_height() * _stu_w / _stu.get_width()))
    STUDIO_LOGO = pygame.transform.smoothscale(_stu, (_stu_w, _stu_h))
except Exception:
    STUDIO_LOGO = None


def splash_studio():
    """Animation de lancement : logo du studio Ephyria sur fond noir
    (fondu entrant -> pause -> fondu sortant). Cliquer/toucher une touche saute
    a la sortie. Renvoie 'quitter' si on ferme la fenetre, sinon None."""
    if STUDIO_LOGO is None:
        return None
    jouer_musique_intro()
    cx = SCREEN_WIDTH // 2
    pos = STUDIO_LOGO.get_rect(center=(cx, 440))
    _pol = pygame.font.Font("assets/fonts/OldLondon.ttf", 124)
    txt_an = _pol.render("an", True, (236, 236, 236))
    txt_game = _pol.render("game", True, (236, 236, 236))
    pos_an = txt_an.get_rect(center=(cx, pos.top - 80))      # "an" AU-DESSUS du logo
    pos_game = txt_game.get_rect(center=(cx, pos.bottom + 80))  # "game" EN DESSOUS
    # Le logo couvre TOUTE la musique (~5,9 s) en fondu, puis l'ecran reste noir
    # quelques secondes de silence avant de passer au menu.
    f_in, hold, f_out, silence = 42, 87, 48, 75   # logo ~5,9 s (= la musique) + 2,5 s de noir
    total = f_in + hold + f_out
    saute = False
    t = 0
    while t <= total + silence:
        clock.tick(FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                try:
                    pygame.mixer.music.fadeout(300)
                except Exception:
                    pass
                return "quitter"
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                saute = True       # une touche / un clic saute toute l'intro
        if saute:
            break
        if t < f_in:
            a = 255 * t / f_in
        elif t < f_in + hold:
            a = 255
        elif t < total:
            a = 255 * (1 - (t - f_in - hold) / f_out)
        else:
            a = 0                  # silence final : ecran noir
        screen.fill((0, 0, 0))
        if a > 0:
            blit_alpha(screen, STUDIO_LOGO, pos, a)
            blit_alpha(screen, txt_an, pos_an, a)
            blit_alpha(screen, txt_game, pos_game, a)
        pygame.display.flip()
        t += 1
    try:
        pygame.mixer.music.fadeout(500)   # coupe proprement (saut ou fin)
    except Exception:
        pass
    screen.fill((0, 0, 0))
    pygame.display.flip()
    return None


def load_animation_frames(folder_path, frame_list):
    "permet l'animation du background"
    frames = []
    for filename in frame_list:
        path = os.path.join(folder_path, filename)
        image = pygame.image.load(path).convert_alpha()
        image = pygame.transform.scale(image, (SCREEN_WIDTH, SCREEN_HEIGHT))
        frames.append(image)
    return frames


_FRAMES_MAPS = {}   # id map -> frames du decor (chargees 1x depuis le dossier de la map)


def frames_de_map(map_id):
    """Frames animees du decor d'une map (cache). Le nombre de frames est AUTO-DETECTE :
    on prend tous les fichiers N.jpg/N.png du dossier, tries 1..N (gere 8, 27, etc.)."""
    frames = _FRAMES_MAPS.get(map_id)
    if frames is None:
        dossier = MAPS.get(map_id, {}).get("frames", ANIMATION_FOLDER)
        noms = [f for f in os.listdir(dossier)
                if os.path.splitext(f)[0].isdigit()
                and f.lower().endswith((".jpg", ".jpeg", ".png"))]
        noms.sort(key=lambda n: int(os.path.splitext(n)[0]))   # tri NUMERIQUE
        frames = load_animation_frames(dossier, noms)
        _FRAMES_MAPS[map_id] = frames
    return frames


_PROPS_MAPS = {}   # id map -> props (calque avant) plein ecran (charge 1x)


def props_de_map(map_id):
    """Props (calque avant) d'une map, mis a l'echelle plein ecran (cache).
    Renvoie une surface transparente si la map n'a pas de props."""
    pr = _PROPS_MAPS.get(map_id)
    if pr is None:
        chemin = MAPS.get(map_id, {}).get("props")
        try:
            img = pygame.image.load(chemin).convert_alpha()
            pr = pygame.transform.scale(img, (SCREEN_WIDTH, SCREEN_HEIGHT))
        except Exception:
            pr = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        _PROPS_MAPS[map_id] = pr
    return pr


# Decor + props actifs (mis a jour par jeu() selon la map). Au demarrage : la map par defaut.
animation_frames = frames_de_map(MAP_ACTUELLE)
frame_nbr = len(animation_frames)
PROPS_SCALED = props_de_map(MAP_ACTUELLE)


# ----------------------------------------------------------------------
#  PARTICULES (poussiere de saut / de dash) : petites animations "one-shot"
#  jouees a un endroit puis qui disparaissent. Chaque fichier est une bande
#  horizontale de frames carrees (64 px). Rendues un peu transparentes.
# ----------------------------------------------------------------------
PARTICLE_ALPHA = 160        # opacite des particules (un peu transparent)
PARTICLE_SPEED = 0.7        # frames d'animation par frame de jeu
PARTICLE_FEET_FACTOR = 1.7  # taille de la particule = ecart des pieds x ce facteur


def charger_particule(chemin, ancrage="bas"):
    """Bande de frames carrees -> (frames semi-transparentes, ratio vertical du
    point d'ancrage dans la frame : "bas" = bas du contenu, "centre" = centre
    du contenu). Non agrandies : la taille est fixee au spawn.
    PERMISSIF : un fichier manquant/supprime ne crash PAS le jeu -> particule vide
    (spawn_particule l'ignore), le reste du jeu tourne normalement."""
    try:
        sheet = pygame.image.load(chemin).convert_alpha()
    except Exception:
        return [], 1.0
    h = sheet.get_height()
    n = sheet.get_width() // h
    frames = []
    haut, bas = h, 0
    for i in range(n):
        f = sheet.subsurface(i * h, 0, h, h).copy()
        bb = f.get_bounding_rect()
        if bb.height:
            haut = min(haut, bb.top)
            bas = max(bas, bb.bottom)
        # multiplie l'alpha de chaque pixel par PARTICLE_ALPHA/255 -> semi-transparent
        f.fill((255, 255, 255, PARTICLE_ALPHA), special_flags=pygame.BLEND_RGBA_MULT)
        frames.append(f)   # teinte nuit appliquee au DRAW (conditionnee a la map)
    if not bas:
        return frames, 1.0
    ratio = ((haut + bas) / 2) / h if ancrage == "centre" else bas / h
    return frames, ratio


PARTICULES_ANIM = {
    # le saut : anneau de poussiere POSE au sol -> ancrage par le centre
    "jump": charger_particule("assets/particles/jump.png", "centre"),
    # les dash : poussiere dont le bas colle aux pieds -> ancrage par le bas
    "dash": charger_particule("assets/particles/dash.png", "bas"),
    "dash_air": charger_particule("assets/particles/dash in air.png", "bas"),
    # BOUCLIER : usure qui se resorbe (sur le perso), garde brisee en recharge (sur le
    # perso). (Pas de particule sur coup bloque/parade : le son suffit, choix utilisateur.)
    "shield_repair": charger_particule("assets/particles/shield repairing.png", "centre"),
    "shield_reload": charger_particule("assets/particles/shield break in reload.png", "centre"),
}
particules = []   # particules actives : {kind, x, y, size, frame, flip}


_BBOX_CACHE = {}   # frame FIXE -> sa bounding rect (le scan d'une image 1080x1080 coute ~11 ms)


def _bbox_cache(img):
    r = _BBOX_CACHE.get(img)
    if r is None:
        r = img.get_bounding_rect()
        _BBOX_CACHE[img] = r
    return r


def feet_info(fighter):
    """Centre horizontal des PIEDS (a l'ecran) et largeur de l'ecart des pieds,
    mesures sur le sprite reellement affiche (et non au centre du rect).

    Optimise : on mesure sur l'image NON flippee (frame fixe -> bbox + alpha cachables)
    et on miroite le resultat si le perso regarde a gauche. Evite de flipper et de
    re-scanner toute l'image 1080x1080 a chaque saut/dash."""
    # Persos MULTI-SHEET (Fighter2 : Oswald/Barrion/dummy) : le corps est ancre au CENTROIDE / au
    # pivot -> centre sur le rect. On prend donc rect.centerx (le calcul par offset ci-dessous
    # suppose self.offset, faux pour ces persos -> poussiere decalee, surtout de dos/en jump).
    if hasattr(fighter, "_off_arc"):
        return fighter.rect.centerx, max(50, int(fighter.rect.width * 0.55))
    u = fighter.image                       # frame d'origine (non flippee) = surface fixe
    bb = _bbox_cache(u)
    if bb.width == 0:
        return fighter.rect.centerx, fighter.rect.width // 2
    e = getattr(fighter, "echelle", 1.0)    # MEME offset que le dessin (offset x echelle)
    blit_x = fighter.rect.x - fighter.offset[0] * e
    W = u.get_width()
    bande = max(3, bb.height // 8)           # bas ~12 % de la silhouette = les pieds
    y0 = bb.bottom - bande
    if _np is not None:
        # On ne lit l'alpha QUE de la bande des pieds (~0,2 ms vs ~18 ms l'image entiere).
        bande_alpha = pygame.surfarray.array_alpha(
            u.subsurface((bb.left, y0, bb.width, bb.bottom - y0)))     # (bb.width, bande)
        cols = (bande_alpha > 20).sum(axis=1).astype(_np.float64)
        total = float(cols.sum())
        if total == 0:
            return fighter.rect.centerx, fighter.rect.width // 2
        xs = _np.arange(bb.left, bb.left + cols.shape[0])
        cu = float((xs * cols).sum()) / total                          # centroid (non flippe)
        nz = _np.nonzero(cols)[0]
        minx, maxx = bb.left + int(nz[0]), bb.left + int(nz[-1])
    else:
        somme_x = 0; total = 0; minx, maxx = bb.right, bb.left
        for x in range(bb.left, bb.right):
            cnt = sum(1 for y in range(y0, bb.bottom) if u.get_at((x, y))[3] > 20)
            if cnt:
                somme_x += x * cnt; total += cnt
                minx = min(minx, x); maxx = max(maxx, x)
        if total == 0:
            return fighter.rect.centerx, fighter.rect.width // 2
        cu = somme_x / total
    # centre de MASSE de la bande (les pieds denses l'emportent sur une arme fine qui
    # depasse). Si le perso est flippe, le pixel non-flippe cu s'affiche en (W-1-cu).
    centre_x = (W - 1 - cu) if fighter.flip else cu
    w = maxx - minx                          # ecart des pieds (invariant par flip)
    w = max(int(fighter.rect.width * 0.35), min(w, int(fighter.rect.width * 0.7)))
    return blit_x + centre_x, w


def spawn_particule(kind, x, y, flip, feet_w):
    # taille un peu plus grande que l'ecart des pieds ; (x, y) = centre des pieds
    anim = PARTICULES_ANIM.get(kind)
    if not anim or not anim[0]:            # kind inconnu ou texture supprimee -> no-op
        return
    size = max(50, feet_w * PARTICLE_FEET_FACTOR)
    particules.append({"kind": kind, "x": x, "y": y, "size": size,
                       "frame": 0.0, "flip": flip})


def maj_particules(avance=True):
    """Dessine/avance les particules (poussiere de saut/dash) : centrees en x sur
    les pieds, bas du contenu au niveau des pieds (y). Teintees par le filtre nuit."""
    for part in particules[:]:
        frames, ratio = PARTICULES_ANIM[part["kind"]]
        idx = int(part["frame"])
        if idx >= len(frames):
            particules.remove(part)
            continue
        s = int(part["size"])
        img = pygame.transform.scale(frames[idx], (s, s))
        if part["flip"]:
            img = pygame.transform.flip(img, True, False)
        # teinte nuit seulement sur les maps nocturnes (assombrir_nuit = no-op si filtre off).
        # Les particules de BOUCLIER restent a PLEINE lumiere (magiques : elles brillent la nuit).
        if not part["kind"].startswith("shield"):
            img = assombrir_nuit(img)
        screen.blit(img, (int(part["x"] - s / 2), int(part["y"] - s * ratio)))
        if avance:
            part["frame"] += PARTICLE_SPEED


def dessiner_ombre(surface, fighter):
    """Ombre ovale au sol sous un combattant (ancree au sol courant, retrecit/s'estompe
    quand il saute). Suit la taille (rect deja a l'echelle). A dessiner AVANT le perso."""
    sol = classes.SOL                                     # niveau du sol courant (par map)
    air = max(0, sol - fighter.rect.bottom)               # hauteur des pieds au-dessus du sol
    f = max(0.4, 1.0 - air / 650.0)                       # plus petite/claire en l'air
    larg = max(8, int(fighter.rect.width * 1.5 * f))
    haut = max(6, int(larg * 0.22))
    sh = pygame.Surface((larg, haut), pygame.SRCALPHA)
    pygame.draw.ellipse(sh, (0, 0, 0, int(120 * f)), sh.get_rect())
    surface.blit(sh, sh.get_rect(center=(fighter.rect.centerx, sol)))


# --- DEV : affichage des damage box (F1 pour basculer). Sert a caler les frames
#     d'impact et la taille des hitbox des nouveaux persos. Vert = corps, orange =
#     hitbox d'attaque, ROUGE plein = frame d'impact (le coup touche a cette frame).
_DEV_HITBOX = [True]
_f_dev = pygame.font.SysFont("consolas,arial", 20, bold=True)


def dessiner_hitbox_dev(surface, f):
    e = getattr(f, "echelle", 1.0)
    pygame.draw.rect(surface, (60, 210, 90), f.rect, 2)          # corps
    cfg = getattr(f, "config", None)
    atks = cfg.get("attacks") if isinstance(cfg, dict) else None
    if atks and getattr(f, "attacking", False):
        # Deduire l'attaque de l'ACTION (attack_type est remis a 0 apres le declenchement,
        # sinon la box ne s'affichait qu'une frame). -> box visible toute l'anim d'attaque.
        at = next((k for k in atks if f.action == f.actions.get("attack%d" % k)), None)
        atk = atks.get(at)
        if atk:
            specs = atk["hitboxes_right"] if not f.flip else atk["hitboxes_left"]
            live = (f.frame_index == atk.get("frame"))
            for (dx, dy, w, h) in specs:
                height = f.rect.height if h is None else int(h * e)
                r = pygame.Rect(int(f.rect.centerx + dx * e), int(f.rect.y + dy * e), int(w * e), height)
                if live:
                    ov = pygame.Surface(r.size, pygame.SRCALPHA); ov.fill((255, 50, 50, 110))
                    surface.blit(ov, r.topleft)
                pygame.draw.rect(surface, (255, 50, 50) if live else (255, 170, 60), r, 3)
            nb = len(f.animation_list[f.action])
            txt = "atk%d  f%d/%d  impact@%s" % (at, f.frame_index, nb - 1, atk.get("frame"))
            t = _f_dev.render(txt, True, (255, 235, 130))
            surface.blit(t, (f.rect.centerx - t.get_width() // 2, f.rect.top - 62))


def draw_healthbar(max_health, health, trail_health, x, y):
    "permet l'affichage de la barre de vie + l'effet de barre de degats (trail)"
    ratio = health / max_health
    trail_ratio = trail_health / max_health
    pygame.draw.rect(screen, RED_BROWN, (x - 2, y - 2, 400, 30))   # bordure
    pygame.draw.rect(screen, NOIR, (x, y, 396, 26))                # fond
    # barre de degats (rouge) : s'etend jusqu'aux HP d'avant les degats
    pygame.draw.rect(screen, (200, 40, 40), (x, y, 396 * trail_ratio, 26))
    # barre de vie restante (or) par-dessus
    pygame.draw.rect(screen, GOLD, (x, y, 396 * ratio, 26))
    

def draw_background_animation(index_frame):
    """gères le fond et son animation"""
    current_frame = animation_frames[index_frame]
    screen.blit(current_frame, (0, 0))

def apply_blur(surface, amount=10):
    """Applique un effet de flou à une surface pygame"""
    scale = 0.25
    surf_size = surface.get_size()
    small_size = (int(surf_size[0] * scale), int(surf_size[1] * scale))
    small = pygame.transform.smoothscale(surface, small_size)
    return pygame.transform.smoothscale(small, surf_size)

def titre_parchemin(surf, texte, police, centre, couleur=(216, 204, 178)):
    """Titre de menu en ACIER grave : ombre sombre + acier clair (assorti aux
    boutons / a l'epee du logo)."""
    ombre = police.render(texte, True, (24, 21, 16))
    haut = police.render(texte, True, couleur)
    r = haut.get_rect(center=centre)
    surf.blit(ombre, (r.x + 3, r.y + 4))
    surf.blit(haut, r)


def plaque_metal(surf, rect):
    """Plaque de fer (biseau, rivets, bordure) SANS texte -> panneaux de dialogue."""
    x, y, w, h = rect.x, rect.y, rect.width, rect.height
    base = (104, 96, 82)
    clair = (base[0] + 30, base[1] + 28, base[2] + 24)
    fonce = (int(base[0] * 0.58), int(base[1] * 0.58), int(base[2] * 0.58))
    pygame.draw.rect(surf, (15, 14, 12), rect.inflate(10, 10), 0, 10)
    pygame.draw.rect(surf, base, rect, 0, 8)
    pygame.draw.rect(surf, clair, (x + 6, y + 5, w - 12, 8), 0, 5)
    pygame.draw.rect(surf, fonce, (x + 6, y + h - 13, w - 12, 8), 0, 5)
    pygame.draw.rect(surf, (26, 23, 18), rect, 4, 8)
    pygame.draw.rect(surf, (171, 156, 134), rect, 2, 8)
    for rx, ry in ((x + 22, y + 22), (x + w - 22, y + 22),
                   (x + 22, y + h - 22), (x + w - 22, y + h - 22)):
        pygame.draw.circle(surf, (70, 63, 52), (rx, ry), 5)
        pygame.draw.circle(surf, (176, 162, 138), (rx, ry), 5, 1)


def confirmer(message, background_surface):
    """Boîte de confirmation Oui/Non par-dessus l'écran courant.

    Renvoie True si l'utilisateur confirme, False sinon.
    background_surface = capture de l'écran à flouter derrière la boîte.
    """
    button_oui = Button(SCREEN_WIDTH // 2 - 230, 460, "Yes", 200)
    button_non = Button(SCREEN_WIDTH // 2 + 30, 460, "No", 200)

    # Fond flouté + assombri, calculé une seule fois (l'arrière-plan est figé)
    fond = apply_blur(background_surface)
    voile = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    voile.set_alpha(140)
    voile.fill(NOIR)

    # Panneau opaque qui contient le message et les boutons (cache le menu dessous)
    panneau = pygame.Rect(SCREEN_WIDTH // 2 - 350, 300, 700, 290)

    # Police du message adaptee : on REDUIT si le texte deborde du panneau (les
    # messages longs comme "Reset all settings?" depassaient en font_title1).
    police_msg = font_title1
    largeur_max = panneau.width - 80
    if police_msg.size(message)[0] > largeur_max:
        taille = max(32, int(75 * largeur_max / police_msg.size(message)[0]))
        police_msg = pygame.font.Font("assets/fonts/OldLondon.ttf", taille)

    while True:
        clock.tick(FPS)
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    mouse_clicked = True

        # Fond flouté + voile sombre
        screen.blit(fond, (0, 0))
        screen.blit(voile, (0, 0))

        # Panneau = plaque de fer
        plaque_metal(screen, panneau)

        # Message sur parchemin
        titre_parchemin(screen, message, police_msg, (SCREEN_WIDTH // 2, 378))

        button_oui.check_hover(mouse_pos)
        button_non.check_hover(mouse_pos)
        button_oui.draw(screen)
        button_non.draw(screen)

        if button_oui.is_clicked(mouse_pos, mouse_clicked):
            return True
        if button_non.is_clicked(mouse_pos, mouse_clicked):
            return False

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


def menu_difficulte(perso1, perso2):
    """Choix de la difficulte de l'IA (mode Solo). Renvoie 'facile'/'normal'/'difficile'
    ou None (Back/Echap). Meme habillage que le sous-menu Battle (parallax + brume)."""
    H, GAP = 84, 22
    libelles = ["Easy", "Normal", "Hard"]                 # AFFICHE (anglais)
    niveaux = ["facile", "normal", "difficile"]           # cles INTERNES (IA.NIVEAUX) -- ne pas traduire
    fbtn = Button._police(int(H * 0.72))
    W = max(fbtn.size(txt)[0] for txt in libelles) + 118
    n = len(libelles)
    PANEL_W = W + 96
    PANEL_H = 46 + (n * H + (n - 1) * GAP) + 92
    panel = pygame.Rect(0, 0, PANEL_W, PANEL_H)
    panel.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 62)
    x = SCREEN_WIDTH // 2 - W // 2
    top = panel.top + 46
    btns = [Button(x, top + k * (H + GAP), lib, W, H) for k, lib in enumerate(libelles)]
    fback = Button._police(int(52 * 0.72))
    wback = fback.size("Back")[0] + 76
    b_back = Button(SCREEN_WIDTH // 2 - wback // 2, panel.bottom - 68, "Back", wback, 52)
    par_dx = par_dy = 0.0
    t = 0
    while True:
        clock.tick(FPS)
        mouse = pygame.mouse.get_pos()
        clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return None
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        mx, my = mouse
        tgt_dx = (0.5 - mx / SCREEN_WIDTH) * 2 * PARALLAX_MX
        tgt_dy = (0.5 - my / SCREEN_HEIGHT) * 2 * PARALLAX_MY
        par_dx += (tgt_dx - par_dx) * 0.025
        par_dy += (tgt_dy - par_dy) * 0.025
        t += 1
        rendu_fond_principal(par_dx, par_dy, t)
        plaque_metal(screen, panel)
        titre_parchemin(screen, "Difficulty", font_title1, (SCREEN_WIDTH // 2, 104))
        for b in btns + [b_back]:
            b.check_hover(mouse)
            b.draw(screen)
        disperser_si_besoin()
        for b, niv in zip(btns, niveaux):
            if b.is_clicked(mouse, clic):
                return niv
        if b_back.is_clicked(mouse, clic):
            return None
        dessiner_curseur(screen)
        pygame.display.flip()


_FONT_CARTE_T = None   # titre gothique des cartes (cache)
_FONT_CARTE_D = None   # description serif des cartes (cache)


# --- EMBLEMES des modes (dessines a la volee, palette metal chaud) : c = centre, r = rayon utile ---
def _emb_solo(s, c, r, col):
    """Une SEULE epee dressee (Solo) : lame PLEINE effilee + garde + poignee + pommeau."""
    cx, cy = c
    gy = cy + r - 15                                              # niveau de la garde
    gw = int(r * 0.52)                                            # DEMI-largeur de la garde (resserree)
    pygame.draw.polygon(s, col, [(cx, cy - r), (cx - 5, cy - r + 13),   # lame pleine (losange effile)
                                 (cx - 3, gy), (cx + 3, gy), (cx + 5, cy - r + 13)])
    pygame.draw.line(s, (54, 48, 40), (cx, cy - r + 9), (cx, gy - 1), 1)   # rainure (fuller)
    pygame.draw.line(s, col, (cx - gw, gy), (cx + gw, gy), 4)               # garde (courte)
    pygame.draw.rect(s, col, (cx - 2, gy, 4, 12))                           # poignee
    pygame.draw.circle(s, col, (cx, gy + 15), 4)                            # pommeau


def _emb_local(s, c, r, col):
    """Deux epees CROISEES (duel 2 joueurs) : lames en X, garde PERPENDICULAIRE a chaque lame."""
    cx, cy = c
    a = r - 2
    for dx in (-1, 1):
        bx, by = cx + dx * a, cy + a          # bas (cote pommeau)
        tx, ty = cx - dx * a, cy - a          # haut (pointe)
        pygame.draw.line(s, col, (bx, by), (tx, ty), 4)      # lame
        pygame.draw.circle(s, col, (bx, by), 3)              # pommeau
        vx, vy = tx - bx, ty - by
        L = math.hypot(vx, vy) or 1
        px, py = -vy / L, vx / L                              # perpendiculaire unitaire
        gx, gy = bx + vx * 0.26, by + vy * 0.26              # position de la garde (pres du pommeau)
        pygame.draw.line(s, col, (gx - px * 9, gy - py * 9), (gx + px * 9, gy + py * 9), 3)


def _emb_lan(s, c, r, col):
    """TRIQUETRA (noeud celtique medieval) : trois anneaux ENTRELACES = interconnexion (joueurs
    relies). Symbole mystique/medieval plutot qu'un logo 'internet'."""
    cx, cy = c
    R = int(r * 0.60)                                             # rayon de chaque anneau
    d = int(r * 0.42)                                             # distance centre -> chaque anneau
    for k in range(3):
        ang = -math.pi / 2 + k * 2 * math.pi / 3                  # 3 anneaux a 120deg
        ax, ay = int(cx + d * math.cos(ang)), int(cy + d * math.sin(ang))
        pygame.draw.circle(s, col, (ax, ay), R, 2)


def _emb_train(s, c, r, col):
    """Cible concentrique (entrainement)."""
    for rr in (r, int(r * 0.63), int(r * 0.28)):
        pygame.draw.circle(s, col, c, rr, 2)
    pygame.draw.circle(s, col, c, 2)


def _carte_mode(surface, rect, titre, desc, emb, hover):
    """Carte de mode = plaque de fer + MEDAILLON-embleme a gauche + titre gothique + description
    serif. Au survol : plaque eclaircie, bordure braise, embleme illumine."""
    global _FONT_CARTE_T, _FONT_CARTE_D
    if _FONT_CARTE_T is None:
        _FONT_CARTE_T = police_acier(38)
        _FONT_CARTE_D = pygame.font.SysFont("georgia,timesnewroman,serif", 19, italic=True)
    x, y, w, h = rect.x, rect.y, rect.width, rect.height
    base = (150, 138, 118) if hover else (104, 96, 82)
    clair = tuple(min(255, c + 34) for c in base)
    fonce = tuple(int(c * 0.58) for c in base)
    pygame.draw.rect(surface, (14, 13, 11), rect.inflate(8, 8), 0, 9)         # ombre
    pygame.draw.rect(surface, base, rect, 0, 8)                                # plaque
    pygame.draw.rect(surface, clair, (x + 5, y + 4, w - 10, 7), 0, 5)          # reflet haut
    pygame.draw.rect(surface, fonce, (x + 5, y + h - 11, w - 10, 7), 0, 5)     # ombre bas
    pygame.draw.rect(surface, (24, 21, 16), rect, 3, 8)
    pygame.draw.rect(surface, (212, 134, 54) if hover else (168, 152, 130), rect, 2, 8)  # braise au survol
    # medaillon a gauche
    mc = (x + 66, y + h // 2)
    mr = 37
    pygame.draw.circle(surface, (32, 28, 23), mc, mr + 3)
    pygame.draw.circle(surface, (80, 68, 53) if hover else (58, 53, 45), mc, mr)
    pygame.draw.circle(surface, (204, 156, 88) if hover else (126, 113, 94), mc, mr, 2)
    for k in range(8):                                                          # rivets sur l'anneau
        ang = k * math.pi / 4
        rx, ry = int(mc[0] + (mr) * math.cos(ang)), int(mc[1] + (mr) * math.sin(ang))
        pygame.draw.circle(surface, (150, 120, 74) if hover else (96, 86, 72), (rx, ry), 2)
    emb(surface, mc, mr - 12, (242, 214, 158) if hover else (198, 184, 156))
    # titre + description a droite du medaillon
    tx = x + 128
    tcol = (240, 224, 188) if hover else (216, 204, 178)
    surface.blit(_FONT_CARTE_T.render(titre, True, (22, 18, 13)), (tx + 2, y + 18 + 2))  # ombre
    surface.blit(_FONT_CARTE_T.render(titre, True, tcol), (tx, y + 18))
    dcol = (208, 194, 168) if hover else (160, 150, 132)
    surface.blit(_FONT_CARTE_D.render(desc, True, dcol), (tx, y + h - 36))


def menu_battle(perso1, perso2):
    """Sous-menu 'Enter the Battle' REDESIGNE en CARTES DE MODE (medaillon-embleme + titre + desc),
    groupe COMBAT (Solo/Local/LAN) puis PRATIQUE (Training) separes par un liseré ornemental.
    Solo -> difficulte puis SELECTION (IA armee) ; les autres remettent IA_NIVEAU=None."""
    global IA_NIVEAU
    # (libelle, description, action, embleme). Les 3 premiers = COMBAT ; le dernier = PRATIQUE.
    modes = [
        ("Solo",     "Face the CPU on your own  —  pick a difficulty", "solo",  _emb_solo),
        ("Local",    "Two players share a single keyboard",            "local", _emb_local),
        ("LAN",      "Two players duel over the local network",        "lan",   _emb_lan),
        ("Training", "Practice your moves freely against a dummy",     "train", _emb_train),
    ]
    H, GAP, SEP = 100, 16, 26        # SEP = espace SUPP avant Training (groupe pratique)
    W = 560
    ys, yy = [], 0
    for i in range(len(modes)):
        ys.append(yy)
        yy += H + GAP + (SEP if i == len(modes) - 2 else 0)
    liste_h = ys[-1] + H
    PANEL_W = W + 120
    PANEL_H = 40 + liste_h + 20 + 56
    panel = pygame.Rect(0, 0, PANEL_W, PANEL_H)
    panel.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 58)
    x = SCREEN_WIDTH // 2 - W // 2
    top = panel.top + 40
    # Button = LOGIQUE (hover/clic + SFX) ; le rendu est fait par _carte_mode (visuel des cartes).
    cartes = [(Button(x, top + ys[i], lab, W, H), lab, desc, act, emb)
              for i, (lab, desc, act, emb) in enumerate(modes)]
    fback = Button._police(int(44 * 0.52))
    wback = fback.size("Back")[0] + 72
    b_back = Button(SCREEN_WIDTH // 2 - wback // 2, panel.bottom - 54, "Back", wback, 44)
    par_dx = par_dy = 0.0
    t = 0
    while True:
        clock.tick(FPS)
        mouse = pygame.mouse.get_pos()
        clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return ("quitter", perso1, perso2)
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                return (MENU_ACCUEIL, perso1, perso2)
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        # Fond IDENTIQUE au menu principal : ciel parallax + brume animee.
        mx, my = mouse
        tgt_dx = (0.5 - mx / SCREEN_WIDTH) * 2 * PARALLAX_MX
        tgt_dy = (0.5 - my / SCREEN_HEIGHT) * 2 * PARALLAX_MY
        par_dx += (tgt_dx - par_dx) * 0.025
        par_dy += (tgt_dy - par_dy) * 0.025
        t += 1
        rendu_fond_principal(par_dx, par_dy, t)
        plaque_metal(screen, panel)                       # panneau plaque de fer (theme du jeu)
        titre_parchemin(screen, "Choose a Mode", font_title1, (SCREEN_WIDTH // 2, 104))
        for b, lab, desc, act, emb in cartes:
            b.check_hover(mouse)                          # hover + SFX (via Button)
            _carte_mode(screen, b.rect, lab, desc, emb, b.is_hovered)
        # separateur ornemental entre le groupe COMBAT (Solo/Local/LAN) et PRATIQUE (Training)
        sep_y = top + ys[-2] + H + (GAP + SEP) // 2
        pygame.draw.line(screen, (84, 75, 61), (x + 82, sep_y), (SCREEN_WIDTH // 2 - 16, sep_y), 2)
        pygame.draw.line(screen, (84, 75, 61), (SCREEN_WIDTH // 2 + 16, sep_y), (x + W - 82, sep_y), 2)
        pygame.draw.polygon(screen, (168, 132, 74),
                            [(SCREEN_WIDTH // 2, sep_y - 6), (SCREEN_WIDTH // 2 + 7, sep_y),
                             (SCREEN_WIDTH // 2, sep_y + 6), (SCREEN_WIDTH // 2 - 7, sep_y)])
        b_back.check_hover(mouse)
        b_back.draw(screen)
        disperser_si_besoin()
        for b, lab, desc, act, emb in cartes:
            if b.is_clicked(mouse, clic):
                if act == "solo":
                    niv = menu_difficulte(perso1, perso2)  # Facile/Normal/Difficile ou None (Back)
                    if niv is not None:
                        IA_NIVEAU = niv                    # J2 = CPU pour le prochain match
                        return (SELECTION, perso1, perso2)
                else:
                    IA_NIVEAU = None                       # 2 humains
                    return ({"local": SELECTION, "lan": MULTIJOUEUR, "train": TRAINING}[act],
                            perso1, perso2)
        if b_back.is_clicked(mouse, clic):
            return (MENU_ACCUEIL, perso1, perso2)
        dessiner_curseur(screen)
        pygame.display.flip()


def _dessiner_popup_maj(mouse_pos):
    """Petit panneau 'Update available' en bas-gauche du menu principal.
    Dessine et renvoie (rect_update, rect_later) pour la gestion du clic."""
    W, H = 380, 158
    px, py = 18, SCREEN_HEIGHT - H - 12
    panneau = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.rect(panneau, (16, 13, 10, 232), panneau.get_rect(), border_radius=10)
    pygame.draw.rect(panneau, (188, 154, 84, 220), panneau.get_rect(), 2, border_radius=10)
    screen.blit(panneau, (px, py))
    f_titre = police_acier(30)
    titre = f_titre.render("Update available", True, (234, 210, 160))
    screen.blit(titre, (px + 20, py + 14))
    detail = _f_version.render("%s is out  (you have %s)" % (MAJ_INFO["tag"], VERSION),
                               True, (214, 206, 190))
    screen.blit(detail, (px + 20, py + 58))
    r_upd = pygame.Rect(px + 20, py + H - 62, 170, 46)
    r_later = pygame.Rect(px + W - 150, py + H - 62, 130, 46)
    dessiner_bouton_metal(screen, r_upd, "Update", r_upd.collidepoint(mouse_pos))
    dessiner_bouton_metal(screen, r_later, "Later", r_later.collidepoint(mouse_pos))
    return r_upd, r_later


def menu_accueil(fade_in=False, perso1="Kenshi", perso2="Lysandra"):
    """Écran d'accueil"""
    discord_rp.maj("In the menus")
    ambiance_menu(True)        # vent de fond
    jouer_musique(MUSIQUE_MENU)   # musique du menu principal (boucle)
    # Hierarchie visuelle : "Enter the Battle" = bouton HEROS (grand, texte imposant),
    # Options / Desert = secondaires, plus petits, sur une rangee en dessous.
    _HERO_H, _SEC_H, _gap = 112, 60, 30
    _fh = police_acier(int(_HERO_H * 0.72))
    _hw = _fh.size("Enter the Battle")[0] + 140
    hero_rect = pygame.Rect((SCREEN_WIDTH - _hw) // 2, 696, _hw, _HERO_H)
    boutons_menu = [(hero_rect, "Enter the Battle", BATTLE_MENU)]   # -> sous-menu (Training/Local/LAN)
    _sec = [("Options", "options"), ("Desert...", "quit")]
    _fs = police_acier(int(_SEC_H * 0.72))
    _sw = [_fs.size(txt)[0] + 84 for txt, _ in _sec]
    _sx = (SCREEN_WIDTH - (sum(_sw) + _gap)) // 2
    for (txt, act), w in zip(_sec, _sw):
        boutons_menu.append((pygame.Rect(_sx, 826, w, _SEC_H), txt, act))
        _sx += w + _gap
    survols = [False] * len(boutons_menu)   # etat de survol (pour le SFX au front montant)

    par_dx = par_dy = 0.0   # decalage parallax lisse (suit la souris)
    t = 0                   # compteur de frames pour l'animation de la brume
    intro = fade_in         # intro echelonnee SEULEMENT au 1er lancement (pas au retour)
    intro_t = 0

    menu_running = True
    while menu_running:
        clock.tick(FPS)
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return ("quitter", perso1, perso2)
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    fond = screen.copy()
                    if confirmer("Are you sure?", fond):
                        return ("quitter", perso1, perso2)
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    mouse_clicked = True

        # Fond : ciel en parallax (souris) + brume animee + 1er plan fixe
        mx, my = mouse_pos
        tgt_dx = (0.5 - mx / SCREEN_WIDTH) * 2 * PARALLAX_MX
        tgt_dy = (0.5 - my / SCREEN_HEIGHT) * 2 * PARALLAX_MY
        par_dx += (tgt_dx - par_dx) * 0.025     # lissage tres lent (ciel qui glisse mollement)
        par_dy += (tgt_dy - par_dy) * 0.025
        t += 1
        rendu_fond_principal(par_dx, par_dy, t)

        # Intro echelonnee (1er lancement) : le FOND apparait, puis le LOGO,
        # puis les BOUTONS. En dehors de l'intro : tout est visible direct.
        if intro:
            veil_a = max(0, 255 - intro_t * 255 // 50)             # voile noir sur le fond (~1,7 s)
            logo_a = max(0, min(255, (intro_t - 38) * 255 // 40))   # logo un peu apres
            btn_a = max(0, min(255, (intro_t - 88) * 255 // 36))    # boutons un chouille apres
        else:
            veil_a, logo_a, btn_a = 0, 255, 255

        if veil_a > 0:
            voile = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            voile.fill((0, 0, 0)); voile.set_alpha(veil_a)
            screen.blit(voile, (0, 0))

        # Logo : plus grand, centre, un peu haut (apparait en fondu)
        if LOGO_MENU is not None:
            blit_alpha(screen, LOGO_MENU,
                       LOGO_MENU.get_rect(center=(SCREEN_WIDTH // 2, 215)), logo_a)
        else:
            title_text2 = font_title2.render("Grimgate", True, GOLD)
            screen.blit(title_text2, title_text2.get_rect(center=(SCREEN_WIDTH // 2, 150)))

        # Boutons metalliques en bas (clics actifs seulement une fois affiches)
        if btn_a >= 255:
            # On dessine d'ABORD tous les boutons, on agit APRES : sinon un return au
            # milieu de la boucle laisserait les boutons suivants non dessines sur la
            # frame que la transition (screen.copy) capture -> ils "disparaissent".
            action_clic = None
            for i, (rect, txt, act) in enumerate(boutons_menu):
                hover = rect.collidepoint(mouse_pos)
                if hover and not survols[i]:   # le curseur vient d'entrer sur le bouton
                    jouer_sfx_hover()
                survols[i] = hover
                dessiner_bouton_metal(screen, rect, txt, hover)
                if hover and mouse_clicked:
                    action_clic = act
            if action_clic is not None:
                jouer_sfx_click()
                if action_clic == BATTLE_MENU:
                    return (BATTLE_MENU, perso1, perso2)
                elif action_clic == "options":
                    return ("options", MENU_ACCUEIL, perso1, perso2)
                elif action_clic == "quit":
                    fond = screen.copy()
                    if confirmer("Are you sure?", fond):
                        return ("quitter", perso1, perso2)
        elif btn_a > 0:
            calque = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            for rect, txt, act in boutons_menu:
                dessiner_bouton_metal(calque, rect, txt, False)
            blit_alpha(screen, calque, (0, 0), btn_a)

        if intro:
            intro_t += 1
            if intro_t > 128:
                intro = False

        # Numero de version : petit, discret (coin bas-droit, police classique). OMBRE portee pour
        # rester LISIBLE sur le fond charge (sinon ca se noie), et alpha modere (pas criard).
        _vw, _vh = _f_version.size(VERSION)
        _vx, _vy = SCREEN_WIDTH - _vw - 18, SCREEN_HEIGHT - _vh - 12
        _vo = _f_version.render(VERSION, True, (10, 8, 6))        # ombre sombre
        _vt = _f_version.render(VERSION, True, (234, 226, 210))   # texte clair
        blit_alpha(screen, _vo, (_vx + 1, _vy + 2), 170)
        blit_alpha(screen, _vt, (_vx, _vy), 190)

        # Popup MAJ passive : le thread du haut du fichier a trouve une release
        # plus recente -> panneau bas-gauche (une fois les boutons affiches).
        # "Update" lance l'updater et quitte le jeu ; "Later" masque pour la session.
        if MAJ_INFO["etat"] == "dispo" and not MAJ_INFO["cache"] and btn_a >= 255:
            r_upd, r_later = _dessiner_popup_maj(mouse_pos)
            if mouse_clicked and r_upd.collidepoint(mouse_pos):
                jouer_sfx_click()
                if lancer_update():
                    return ("quitter", perso1, perso2)
                MAJ_INFO["cache"] = True   # updater introuvable : on n'insiste pas
            elif mouse_clicked and r_later.collidepoint(mouse_pos):
                jouer_sfx_click()
                MAJ_INFO["cache"] = True

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()

    return ("quitter", perso1, perso2)


def screen_options(choix_fullscreen, background_surface=None):
    """Sous-menu d'affichage : choisit plein ecran / fenetre (choix EN ATTENTE,
    applique par le bouton Apply du menu Options). Renvoie (choix, quitter)."""
    button_fullscreen = Button(SCREEN_WIDTH // 2 - 340, 380, "Fullscreen", 330)
    button_windowed = Button(SCREEN_WIDTH // 2 + 10, 380, "Windowed", 330)
    button_retour = Button(SCREEN_WIDTH // 2 - 90, 560, "Back", 180)

    while True:
        clock.tick(FPS)
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return (choix_fullscreen, True)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return (choix_fullscreen, False)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_clicked = True

        if background_surface is not None:
            screen.blit(background_surface, (0, 0))
        else:
            draw_background_animation(0)
        overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        overlay.set_alpha(100)
        overlay.fill(NOIR)
        screen.blit(overlay, (0, 0))

        titre_parchemin(screen, "Screen", font_title, (SCREEN_WIDTH // 2, 150))
        sub = font_small.render("Display mode", True, (206, 196, 174))
        screen.blit(sub, sub.get_rect(center=(SCREEN_WIDTH // 2, 300)))

        for bouton, est_fs in ((button_fullscreen, True), (button_windowed, False)):
            bouton.check_hover(mouse_pos)
            bouton.draw(screen)
            if choix_fullscreen == est_fs:   # mode actif : liseré braise arrondi
                pygame.draw.rect(screen, (210, 132, 56), bouton.rect.inflate(14, 14), 3, 9)
        button_retour.check_hover(mouse_pos)
        button_retour.draw(screen)

        if button_fullscreen.is_clicked(mouse_pos, mouse_clicked):
            choix_fullscreen = True
        if button_windowed.is_clicked(mouse_pos, mouse_clicked):
            choix_fullscreen = False
        if button_retour.is_clicked(mouse_pos, mouse_clicked):
            return (choix_fullscreen, False)

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


def nom_touche(code):
    """Nom lisible d'une touche pygame (ex: 'Q', 'Left', ';', '!').
    code == None -> action non liee."""
    if code is None:
        return "—"
    n = pygame.key.name(code)
    return n.upper() if len(n) == 1 else n.capitalize()


def keybinds_menu(background_surface=None):
    """Menu des touches PERSONNALISABLES, en 2 colonnes Player 1 / Player 2.
    Schema UNIFIE (memes touches pour tous les persos) : Move 1 = attaque
    principale, Move 2 = action secondaire. On clique une action puis on appuie
    sur la nouvelle touche. Chaque changement est applique a KEYBINDS et
    SAUVEGARDE immediatement (pas besoin d'Apply). Renvoie True si on quitte."""
    font_lbl = pygame.font.SysFont("segoeui,arial", 26)
    font_key = pygame.font.SysFont("segoeui,arial", 26, bold=True)
    font_hint = pygame.font.SysFont("segoeui,arial", 22)

    ACTIONS = [("left", "Left"), ("right", "Right"), ("up", "Up"),
               ("block", "Block"), ("move1", "Move 1"), ("move2", "Move 2")]
    COLONNES = [("Left", "Player 1", SCREEN_WIDTH // 4),
                ("Right", "Player 2", SCREEN_WIDTH * 3 // 4)]
    ROW_Y0, ROW_H, ROW_W = 300, 76, 520

    button_retour = Button(SCREEN_WIDTH // 2 - 200, 805, "Back", 180)
    button_reset = Button(SCREEN_WIDTH // 2 + 20, 805, "Reset", 180)

    rebind = None   # (cote, action) en cours de reassignation, ou None

    while True:
        clock.tick(FPS)
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
            if event.type == pygame.KEYDOWN:
                if rebind is not None:
                    if event.key != pygame.K_ESCAPE:   # Echap = annuler la saisie
                        cote, action = rebind
                        # Une touche ne peut servir qu'a UNE action : si elle est
                        # deja utilisee ailleurs (meme par l'autre joueur), on
                        # delie l'ancienne action (-> None) avant d'assigner.
                        for c in KEYBINDS:
                            for a in KEYBINDS[c]:
                                if KEYBINDS[c][a] == event.key and (c, a) != (cote, action):
                                    KEYBINDS[c][a] = None
                        KEYBINDS[cote][action] = event.key
                        sauver_settings()      # persistance immediate
                    rebind = None
                elif event.key == pygame.K_ESCAPE:
                    return False
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_clicked = True

        if background_surface is not None:
            screen.blit(background_surface, (0, 0))
        else:
            draw_background_animation(0)
        overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        overlay.set_alpha(120)
        overlay.fill(NOIR)
        screen.blit(overlay, (0, 0))

        titre_parchemin(screen, "Keybinds", font_title, (SCREEN_WIDTH // 2, 90))
        hint = "Click an action, then press the new key  (Esc to cancel)"
        h = font_hint.render(hint, True, GRAY)
        screen.blit(h, h.get_rect(center=(SCREEN_WIDTH // 2, 165)))

        # Si on clique en cours de rebind (hors saisie clavier), on annule.
        if rebind is not None and mouse_clicked:
            rebind = None
            mouse_clicked = False

        BRAISE = (210, 132, 56)
        pygame.draw.line(screen, (140, 128, 110), (SCREEN_WIDTH // 2, 220), (SCREEN_WIDTH // 2, 770), 2)
        for cote, titre, cx in COLONNES:
            t = font_medium.render(titre, True, (216, 204, 178))
            screen.blit(t, t.get_rect(center=(cx, 245)))
            for i, (action, label) in enumerate(ACTIONS):
                rect = pygame.Rect(cx - ROW_W // 2, ROW_Y0 + i * ROW_H, ROW_W, ROW_H - 14)
                en_cours = (rebind == (cote, action))
                survol = rect.collidepoint(mouse_pos)
                fond = (66, 54, 30) if en_cours else ((64, 60, 52) if survol else (42, 40, 35))
                pygame.draw.rect(screen, fond, rect, 0, 10)
                pygame.draw.rect(screen, BRAISE if en_cours else (104, 96, 82), rect, 2, 10)
                # libelle a gauche
                lbl = font_lbl.render(label, True, (226, 220, 206))
                screen.blit(lbl, lbl.get_rect(midleft=(rect.left + 22, rect.centery)))
                # touche a droite (dans une pastille)
                texte = "..." if en_cours else nom_touche(KEYBINDS[cote][action])
                kt = font_key.render(texte, True, BRAISE if en_cours else (224, 216, 198))
                pastille = pygame.Rect(0, 0, max(64, kt.get_width() + 34), 44)
                pastille.midright = (rect.right - 18, rect.centery)
                pygame.draw.rect(screen, (24, 21, 17), pastille, 0, 8)
                pygame.draw.rect(screen, BRAISE if en_cours else (150, 138, 118), pastille, 2, 8)
                screen.blit(kt, kt.get_rect(center=pastille.center))
                # clic -> debuter le rebind de cette action
                if mouse_clicked and survol and rebind is None:
                    rebind = (cote, action)

        button_retour.check_hover(mouse_pos)
        button_reset.check_hover(mouse_pos)
        button_retour.draw(screen)
        button_reset.draw(screen)

        if button_reset.is_clicked(mouse_pos, mouse_clicked):
            for cote in KEYBINDS:
                KEYBINDS[cote].update(KEYBINDS_DEFAULT[cote])
            sauver_settings()      # persistance immediate
        if button_retour.is_clicked(mouse_pos, mouse_clicked):
            return False

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


def audio_options(background_surface=None):
    """Sous-menu AUDIO : volumes Master / Music / Sound FX / Ambience (sliders).
    Applique en direct et sauvegarde. Renvoie True si on ferme la fenetre."""
    lignes = [("Master", "vol_master"), ("Music", "vol_music"),
              ("Sound FX", "vol_sfx"), ("Ambience", "vol_ambience")]
    TX, TW = 660, 460          # piste du slider : x de depart, largeur
    Y0, DY = 330, 110
    button_retour = Button(SCREEN_WIDTH // 2 - 90, 800, "Back", 180)
    font_lbl = pygame.font.SysFont("segoeui,arial", 30, bold=True)
    font_val = pygame.font.SysFont("segoeui,arial", 26, bold=True)
    drag = None                # index du slider en cours de glissement

    while True:
        clock.tick(FPS)
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                sauver_settings()
                return False
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_clicked = True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                drag = None

        # Debut de glissement : clic sur la piste ou la poignee d'un slider
        if mouse_clicked:
            for i in range(len(lignes)):
                cy = Y0 + i * DY
                if TX - 20 <= mouse_pos[0] <= TX + TW + 20 and abs(mouse_pos[1] - cy) <= 26:
                    drag = i
        if drag is not None:
            SETTINGS[lignes[drag][1]] = round(max(0.0, min(1.0, (mouse_pos[0] - TX) / TW)), 2)
            appliquer_volumes()

        if background_surface is not None:
            screen.blit(background_surface, (0, 0))
        elif FORGE_BG is not None:
            screen.blit(FORGE_BG, (0, 0))
        else:
            screen.fill(NOIR)
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        ov.set_alpha(115)
        ov.fill(NOIR)
        screen.blit(ov, (0, 0))

        titre_parchemin(screen, "Audio", font_title, (SCREEN_WIDTH // 2, 150))

        for i, (label, key) in enumerate(lignes):
            cy = Y0 + i * DY
            val = SETTINGS.get(key, 0.7)
            lab = font_lbl.render(label, True, (226, 218, 202))
            screen.blit(lab, lab.get_rect(midright=(TX - 40, cy)))
            pygame.draw.rect(screen, (40, 38, 34), (TX, cy - 6, TW, 12), 0, 6)
            pygame.draw.rect(screen, (210, 132, 56), (TX, cy - 6, int(TW * val), 12), 0, 6)
            pygame.draw.rect(screen, (120, 112, 98), (TX, cy - 6, TW, 12), 2, 6)
            kx = TX + int(TW * val)
            survol = drag == i or (abs(mouse_pos[0] - kx) <= 16 and abs(mouse_pos[1] - cy) <= 18)
            pygame.draw.circle(screen, (200, 124, 50) if survol else (152, 140, 120), (kx, cy), 14)
            pygame.draw.circle(screen, (26, 23, 18), (kx, cy), 14, 3)
            v = font_val.render("%d%%" % round(val * 100), True, (224, 216, 198))
            screen.blit(v, v.get_rect(midleft=(TX + TW + 28, cy)))

        button_retour.check_hover(mouse_pos)
        button_retour.draw(screen)
        if button_retour.is_clicked(mouse_pos, mouse_clicked):
            sauver_settings()
            return False

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


def options(retour_vers, perso1="Kenshi", perso2="Lysandra", background_surface=None):
    """Menu des options : Display (mode d'affichage + cases Debug), Keybinds
    (touches personnalisables, SAUVEGARDEES immediatement), Apply (applique le
    mode d'affichage + sauvegarde), Back."""
    global screen, FULLSCREEN_MODE
    choix_fullscreen = FULLSCREEN_MODE
    if background_surface is None:   # depuis l'accueil -> on garde le vent de fond
        ambiance_menu(True)

    onglet = "Display"  # categorie ouverte par defaut
    rebind = None       # Keybinds : action en cours de reassignation
    drag = None         # Audio : slider en cours de glissement
    BRAISE = (210, 132, 56)

    # Onglets de categorie (haut, horizontal)
    CATS = ["Display", "Keybinds", "Audio", "Credits"]
    TAB_W, TAB_GAP, TAB_Y = 244, 22, 128
    _tx = SCREEN_WIDTH // 2 - (TAB_W * len(CATS) + TAB_GAP * (len(CATS) - 1)) // 2
    onglets = {}
    for cat in CATS:
        onglets[cat] = Button(_tx, TAB_Y, cat, TAB_W)
        _tx += TAB_W + TAB_GAP
    button_reset = Button(SCREEN_WIDTH - 200, TAB_Y + 12, "Reset", 150, 56)   # onglet Keybinds : plus petit, centre aligne aux onglets
    button_apply = Button(SCREEN_WIDTH // 2 - 250, 790, "Apply", 230)     # bas (remontes du bord)
    button_retour = Button(SCREEN_WIDTH // 2 + 20, 790, "Back", 230)
    button_reset_all = Button(90, 802, "Reset settings", 220, 56)         # remise a zero TOTALE : plus petit, centre aligne
    # Changelog (bas-droit, miroir de Reset settings) : ouvre les notes de version
    # GitHub dans le navigateur (l'historique complet des maj du jeu).
    button_changelog = Button(SCREEN_WIDTH - 90 - 220, 802, "Changelog", 220, 56)
    # contenu Display
    button_fs = Button(SCREEN_WIDTH // 2 - 350, 400, "Fullscreen", 330)
    button_win = Button(SCREEN_WIDTH // 2 + 20, 400, "Windowed", 330)
    # contenu Display > Debug : cases a cocher (DECOCHEES par defaut) -> classes.DEBUG_AFFICHAGE.
    # PERMISSIF : tout code futur qui enregistre ses boites via classes.debug_box (hitbox /
    # damage box) ou lit DEBUG_AFFICHAGE["cd"] est automatiquement couvert par ces cases.
    D_ITEMS = [("Show hitboxes", "hitbox"), ("Show damage boxes", "damage box"),
               ("Show cooldowns", "cd")]
    # contenu Keybinds
    K_ACTIONS = [("left", "Left"), ("right", "Right"), ("up", "Up"),
                 ("block", "Block"), ("move1", "Move 1"), ("move2", "Move 2")]
    K_COLS = [("Left", "Player 1", SCREEN_WIDTH // 4), ("Right", "Player 2", SCREEN_WIDTH * 3 // 4)]
    K_ROW_Y0, K_ROW_H, K_ROW_W = 300, 72, 520
    f_lbl = pygame.font.SysFont("segoeui,arial", 26)
    f_key = pygame.font.SysFont("segoeui,arial", 26, bold=True)
    # contenu Audio
    A_LIGNES = [("Master", "vol_master"), ("Music", "vol_music"), ("Sound FX", "vol_sfx"),
                ("Characters", "vol_perso"), ("Ambience", "vol_ambience")]
    A_TX, A_TW, A_Y0, A_DY = 660, 460, 300, 95
    f_albl = pygame.font.SysFont("segoeui,arial", 30, bold=True)
    f_aval = pygame.font.SysFont("segoeui,arial", 26, bold=True)
    # contenu Credits (categorie + creditee ; police lisible pour les noms propres)
    CREDITS = [("Character sprites", "LuizMelo  &  OcO"),
               ("Sound effects", "Epidemic Sound"),
               ("Voices", "Fish Audio"),
               ("Map backgrounds", "Imgur  ·  Reddit  ·  Pinterest"),
               ("Fonts", "Dieter Steffmann"),
               ("Development", "Skakay")]
    f_cred_cat = pygame.font.SysFont("georgia,timesnewroman,serif", 24, italic=True)
    f_cred_nom = pygame.font.SysFont("segoeui,arial", 34, bold=True)

    t_opt = 0
    while True:
        clock.tick(FPS)
        t_opt += 1
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return ("quitter", perso1, perso2)
            if event.type == pygame.KEYDOWN:
                if onglet == "Keybinds" and rebind is not None:
                    if event.key != pygame.K_ESCAPE:           # Echap = annuler la saisie
                        cote, action = rebind
                        for c in KEYBINDS:
                            for a in KEYBINDS[c]:
                                if KEYBINDS[c][a] == event.key and (c, a) != (cote, action):
                                    KEYBINDS[c][a] = None       # une touche ne sert qu'a 1 action
                        KEYBINDS[cote][action] = event.key
                        sauver_settings()
                    rebind = None
                elif event.key == pygame.K_ESCAPE:
                    sauver_settings()
                    return (retour_vers, perso1, perso2)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_clicked = True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                drag = None

        # --- Fond : forge + brume (depuis l'accueil) / fond fige (depuis la pause) ---
        if background_surface is not None:
            screen.blit(background_surface, (0, 0))
        elif FORGE_BG is not None:
            screen.blit(FORGE_BG, (0, 0))
            off = int((t_opt * 0.45) % SCREEN_WIDTH)
            yb = int(math.sin(t_opt * 0.02) * 7)
            screen.blit(FOG_OPT, (off - SCREEN_WIDTH, yb))
            screen.blit(FOG_OPT, (off, yb))
        else:
            draw_background_animation(0)
            screen.blit(apply_blur(screen.copy()), (0, 0))
        overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        overlay.set_alpha(92)
        overlay.fill(NOIR)
        screen.blit(overlay, (0, 0))

        titre_parchemin(screen, "Options", font_title1, (SCREEN_WIDTH // 2, 64))

        # --- Onglets (categorie active = liseré braise) ---
        for cat, b in onglets.items():
            b.check_hover(mouse_pos)
            b.draw(screen)
            if cat == onglet:
                pygame.draw.rect(screen, BRAISE, b.rect.inflate(12, 12), 3, 9)
            if b.is_clicked(mouse_pos, mouse_clicked):
                onglet, rebind, drag = cat, None, None

        # --- Contenu de l'onglet actif ---
        if onglet == "Display":
            sub = font_small.render("Display mode", True, (206, 196, 174))
            screen.blit(sub, sub.get_rect(center=(SCREEN_WIDTH // 2, 330)))
            for bouton, est_fs in ((button_fs, True), (button_win, False)):
                bouton.check_hover(mouse_pos)
                bouton.draw(screen)
                if choix_fullscreen == est_fs:
                    pygame.draw.rect(screen, BRAISE, bouton.rect.inflate(14, 14), 3, 9)
                if bouton.is_clicked(mouse_pos, mouse_clicked):
                    choix_fullscreen = est_fs
            # --- Debug : cases a cocher (voir D_ITEMS) ; etat vif dans classes.DEBUG_AFFICHAGE,
            # lu partout (hitbox/damage box via debug_box, barres de cd) -> effet immediat.
            sub2 = font_small.render("Debug", True, (206, 196, 174))
            screen.blit(sub2, sub2.get_rect(center=(SCREEN_WIDTH // 2, 570)))
            for i, (label, cle) in enumerate(D_ITEMS):
                cy = 628 + i * 56
                case = pygame.Rect(SCREEN_WIDTH // 2 - 220, cy - 17, 34, 34)
                zone = pygame.Rect(case.x, case.y, 470, 34)   # case + label cliquables
                actif = classes.DEBUG_AFFICHAGE[cle]
                pygame.draw.rect(screen, (42, 40, 35), case, 0, 6)
                pygame.draw.rect(screen, BRAISE if actif else (120, 112, 98), case, 3, 6)
                if actif:                                     # coche = carre braise plein
                    pygame.draw.rect(screen, BRAISE, case.inflate(-16, -16), 0, 3)
                lab = f_lbl.render(label, True, (226, 216, 196))
                screen.blit(lab, (case.right + 16, cy - lab.get_height() // 2))
                if mouse_clicked and zone.collidepoint(mouse_pos):
                    classes.DEBUG_AFFICHAGE[cle] = not actif

        elif onglet == "Keybinds":
            if rebind is not None and mouse_clicked:   # clic ailleurs = annule la saisie
                rebind, mouse_clicked = None, False
            pygame.draw.line(screen, (140, 128, 110), (SCREEN_WIDTH // 2, 270),
                             (SCREEN_WIDTH // 2, 762), 2)
            for cote, titre, cx in K_COLS:
                tt = font_medium.render(titre, True, (216, 204, 178))
                screen.blit(tt, tt.get_rect(center=(cx, 248)))
                for i, (action, label) in enumerate(K_ACTIONS):
                    rect = pygame.Rect(cx - K_ROW_W // 2, K_ROW_Y0 + i * K_ROW_H, K_ROW_W, K_ROW_H - 14)
                    en_cours = (rebind == (cote, action))
                    survol = rect.collidepoint(mouse_pos)
                    fond = (66, 54, 30) if en_cours else ((64, 60, 52) if survol else (42, 40, 35))
                    pygame.draw.rect(screen, fond, rect, 0, 10)
                    pygame.draw.rect(screen, BRAISE if en_cours else (104, 96, 82), rect, 2, 10)
                    lbl = f_lbl.render(label, True, (226, 220, 206))
                    screen.blit(lbl, lbl.get_rect(midleft=(rect.left + 22, rect.centery)))
                    texte = "..." if en_cours else nom_touche(KEYBINDS[cote][action])
                    kt = f_key.render(texte, True, BRAISE if en_cours else (224, 216, 198))
                    pastille = pygame.Rect(0, 0, max(64, kt.get_width() + 34), 42)
                    pastille.midright = (rect.right - 18, rect.centery)
                    pygame.draw.rect(screen, (24, 21, 17), pastille, 0, 8)
                    pygame.draw.rect(screen, BRAISE if en_cours else (150, 138, 118), pastille, 2, 8)
                    screen.blit(kt, kt.get_rect(center=pastille.center))
                    if mouse_clicked and survol and rebind is None:
                        rebind = (cote, action)
            button_reset.check_hover(mouse_pos)
            button_reset.draw(screen)
            if button_reset.is_clicked(mouse_pos, mouse_clicked):
                for c in KEYBINDS:
                    KEYBINDS[c].update(KEYBINDS_DEFAULT[c])
                sauver_settings()

        elif onglet == "Audio":
            if mouse_clicked:
                for i in range(len(A_LIGNES)):
                    cy = A_Y0 + i * A_DY
                    if A_TX - 20 <= mouse_pos[0] <= A_TX + A_TW + 20 and abs(mouse_pos[1] - cy) <= 26:
                        drag = i
            if drag is not None:
                SETTINGS[A_LIGNES[drag][1]] = round(max(0.0, min(1.0, (mouse_pos[0] - A_TX) / A_TW)), 2)
                appliquer_volumes()
            for i, (label, key) in enumerate(A_LIGNES):
                cy = A_Y0 + i * A_DY
                val = SETTINGS.get(key, 0.7)
                lab = f_albl.render(label, True, (226, 218, 202))
                screen.blit(lab, lab.get_rect(midright=(A_TX - 40, cy)))
                pygame.draw.rect(screen, (40, 38, 34), (A_TX, cy - 6, A_TW, 12), 0, 6)
                pygame.draw.rect(screen, BRAISE, (A_TX, cy - 6, int(A_TW * val), 12), 0, 6)
                pygame.draw.rect(screen, (120, 112, 98), (A_TX, cy - 6, A_TW, 12), 2, 6)
                kx = A_TX + int(A_TW * val)
                survol = drag == i or (abs(mouse_pos[0] - kx) <= 16 and abs(mouse_pos[1] - cy) <= 18)
                pygame.draw.circle(screen, (200, 124, 50) if survol else (152, 140, 120), (kx, cy), 14)
                pygame.draw.circle(screen, (26, 23, 18), (kx, cy), 14, 3)
                v = f_aval.render("%d%%" % round(val * 100), True, (224, 216, 198))
                screen.blit(v, v.get_rect(midleft=(A_TX + A_TW + 28, cy)))

        elif onglet == "Credits":
            for i, (cat, qui) in enumerate(CREDITS):
                cy = 312 + i * 88
                c = f_cred_cat.render(cat, True, (172, 164, 148))          # categorie (discrete)
                screen.blit(c, c.get_rect(center=(SCREEN_WIDTH // 2, cy - 18)))
                n = f_cred_nom.render(qui, True, (226, 204, 152))          # credite (dore, lisible)
                screen.blit(n, n.get_rect(center=(SCREEN_WIDTH // 2, cy + 18)))
            merci = f_cred_cat.render("The Siege of Grimgate  -  thanks for playing !", True, (150, 142, 126))
            screen.blit(merci, merci.get_rect(center=(SCREEN_WIDTH // 2, 312 + len(CREDITS) * 88 + 6)))

        # --- Apply / Back (bas) + Reset settings (bas-gauche) + Changelog (bas-droit) ---
        button_apply.check_hover(mouse_pos)
        button_retour.check_hover(mouse_pos)
        button_reset_all.check_hover(mouse_pos)
        button_changelog.check_hover(mouse_pos)
        button_apply.draw(screen)
        button_retour.draw(screen)
        button_reset_all.draw(screen)
        button_changelog.draw(screen)
        if button_changelog.is_clicked(mouse_pos, mouse_clicked):
            import webbrowser
            webbrowser.open("https://github.com/%s/releases" % GITHUB_REPO)
        if button_apply.is_clicked(mouse_pos, mouse_clicked):
            if choix_fullscreen != FULLSCREEN_MODE:
                FULLSCREEN_MODE = choix_fullscreen
                appliquer_affichage(FULLSCREEN_MODE)
            SETTINGS["fullscreen"] = FULLSCREEN_MODE
            sauver_settings()
        if button_reset_all.is_clicked(mouse_pos, mouse_clicked):
            fond = screen.copy()
            if confirmer("Reset all settings?", fond):
                # Remise a zero TOTALE : audio, affichage, touches ET le popup
                # de raccourci du lancement (raccourci_ne_plus_demander -> False).
                for cle in DEFAULT_SETTINGS:
                    SETTINGS[cle] = DEFAULT_SETTINGS[cle]
                for c in KEYBINDS:
                    KEYBINDS[c].update(KEYBINDS_DEFAULT[c])
                appliquer_volumes()
                choix_fullscreen = DEFAULT_SETTINGS["fullscreen"]
                if FULLSCREEN_MODE != choix_fullscreen:
                    FULLSCREEN_MODE = choix_fullscreen
                    appliquer_affichage(FULLSCREEN_MODE)
                rebind, drag = None, None
                sauver_settings()
        if button_retour.is_clicked(mouse_pos, mouse_clicked):
            sauver_settings()
            return (retour_vers, perso1, perso2)

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


# Titres "lore" (univers Ephyria) affiches sous chaque combattant a la selection.
NOMS_TITRES = {
    "Kenshi": "The Wandering Blade",
    "Lysandra": "Shield of the Vanguard",
    "Konrad": "The Armswain of the New Crown",
    "Arinya": "Huntress of the Wilds",
    "Stormr": "Storm-Caller of the South",
    "Oswald": "The Radiant King",
    "Barrion": "Old Knight of the Crown",
}
_apercu_cache = {}


def _apercu_idx(frames, tick, duree_ticks=48):
    """Index de frame pour un apercu anime, a DUREE de boucle ~constante quel que soit
    le nombre de frames (sinon un idle a 18 frames defile beaucoup trop lentement)."""
    n = len(frames)
    return (tick // max(1, round(duree_ticks / n))) % n


def _centre_corps(img):
    """x du CORPS = mediane des colonnes PONDEREE par leur nombre de pixels opaques.
    Robuste : une arme fine tendue d'un cote (peu de pixels) ne deplace pas le centre,
    et c'est STABLE d'une frame a l'autre (pas de glissement comme avec les pieds)."""
    if _np is None:
        return img.get_width() / 2.0
    try:
        a = pygame.surfarray.array_alpha(img)               # (w, h)
        poids = (a > 40).sum(axis=1).astype(_np.float64)    # nb de pixels opaques par colonne
        tot = poids.sum()
        if tot > 0:
            return float(_np.searchsorted(_np.cumsum(poids), tot / 2.0))   # mediane ponderee
    except Exception:
        pass
    return img.get_width() / 2.0


def apercu_frames(nom, hauteur=390):
    """Frames d'idle d'un perso pour l'apercu anime de la selection. Renvoie une liste
    de (image, dx) ou dx = decalage du corps / centre de l'image (pour recentrer). Mis
    en cache.

    Anti-glissement : toutes les frames sont recadrees sur la MEME bbox (l'union des
    bbox de l'idle), donc le corps garde une position FIXE d'une frame a l'autre, et on
    applique UN SEUL offset (mediane) -> les effets (sabre electrique) animent par-dessus
    sans deplacer le corps."""
    if nom in _apercu_cache:
        return _apercu_cache[nom]
    inst = PERSONNAGES[nom]("Left", False)
    idle = inst.actions["idle"] if hasattr(inst, "actions") else inst.IDLE
    frames = inst.animation_list[idle]
    union = None                                  # bbox commune a toutes les frames
    for fr in frames:
        bb = fr.get_bounding_rect()
        if bb.width >= 2 and bb.height >= 2:
            union = bb if union is None else union.union(bb)
    if union is None:
        out = [(pygame.Surface((10, hauteur), pygame.SRCALPHA), 0.0)]
        _apercu_cache[nom] = out
        return out
    sc = hauteur / union.height
    imgs = [pygame.transform.smoothscale(fr.subsurface(union).copy(),
                                         (max(1, int(union.width * sc)), hauteur))
            for fr in frames]
    dxs = [_centre_corps(im) - im.get_width() / 2.0 for im in imgs]
    dx = float(_np.median(dxs)) if _np is not None else sum(dxs) / len(dxs)
    out = [(im, dx) for im in imgs]              # meme offset pour toutes -> aucun glissement
    _apercu_cache[nom] = out
    return out


# PRECHARGEMENT des apercus de TOUS les persos AU LANCEMENT (~2-3 s, dans la phase de
# chargement du jeu) : sinon le PREMIER survol d'une icone instancie le perso complet
# (sheets scalees, pivots, tracking du slam...) -> petit FREEZE au premier hover.
# Paye une fois au boot -> tous les survols sont instantanes ensuite.
for _nom in PERSONNAGES:
    apercu_frames(_nom)


def choose(perso1="Kenshi", perso2="Lysandra", presel=False):
    """Écran de sélection des personnages.

    Une SEULE liste où chaque perso n'apparaît qu'une fois. On choisit
    d'abord le combattant de GAUCHE (joueur 1), puis celui de DROITE
    (joueur 2). Le bouton "Back" revient d'une étape en arrière pour changer
    la sélection (et, à la 1re étape, retourne au menu principal).
    Les boutons sont construits à partir de NOMS_PERSOS : ajouter un perso au
    registre PERSONNAGES l'ajoute automatiquement à ce menu.
    """
    ambiance_menu(False)   # coupe le vent des menus
    jouer_musique(MUSIQUE_MENU)   # (re)lance la musique du menu (cas "New Battle" depuis la victoire)
    # On GARDE la musique du menu principal mais on la baisse EN DOUCEUR pendant le choix
    # (le retour au menu la remonte via ambiance_menu -> appliquer_volumes).
    _vol_plein = SETTINGS.get("vol_master", 0.8) * SETTINGS.get("vol_music", 0.7)
    vol_mus = _vol_plein            # volume musique courant : part du plein, descend vers la cible
    # Selection vierge en entree normale ; RESTAUREE si on revient du choix de map (presel).
    sel_p1 = perso1 if presel else None
    sel_p2 = perso2 if presel else None
    survole_prev = None   # perso survole a la frame precedente (pour le son de hover)

    COL_P1 = (210, 70, 70)      # joueur gauche = rouge
    COL_P2 = (80, 120, 220)     # joueur droite = bleu
    LEFT_CX, RIGHT_CX = 430, 1170
    BASELINE = 560              # niveau des pieds des combattants (dans le cadre)
    FRAME_W, FRAME_H, FRAME_CY, FRAME_MARGE = 300, 450, 358, 16   # cadre metal des previews
    font_lore = pygame.font.Font("assets/fonts/OldLondon.ttf", 34)

    # Roster d'icones en bas (tuiles d'acier). Button -> hover/clic ; dessin custom.
    TILE, ICON_PX, gap = 100, 78, 30
    icones = {nom: pygame.transform.scale(
        pygame.image.load(PERSONNAGES[nom].ICON).convert_alpha(), (ICON_PX, ICON_PX))
        for nom in NOMS_PERSOS}
    total = TILE * len(NOMS_PERSOS) + gap * (len(NOMS_PERSOS) - 1)
    x = SCREEN_WIDTH // 2 - total // 2
    boutons = {}
    for nom in NOMS_PERSOS:
        boutons[nom] = Button(x, 690, nom, TILE, TILE)                      # roster remonte
        boutons[nom].muet = True   # SFX hover/clic specifiques a venir -> pas le son generique
        x += TILE + gap

    button_back = Button(50, 806, "Back", 190, 62)                          # bas gauche (SOUS le roster)
    button_map = Button(SCREEN_WIDTH - 360, 800, "Choose Map", 300, 78)     # bas droite (SOUS le roster)
    tick = 0

    while True:
        clock.tick(FPS)
        tick += 1
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return ("quitter", perso1, perso2)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return (MENU_ACCUEIL, perso1, perso2)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_clicked = True

        for nom, b in boutons.items():
            b.check_hover(mouse_pos)
        survole = next((n for n, b in boutons.items() if b.is_hovered), None)
        if survole is not None and survole != survole_prev:   # on entre sur une nouvelle tuile
            jouer_sfx(SFX_HOVER_PERSO, 0.8)
        survole_prev = survole

        # Étape courante : 1 = choix gauche, 2 = choix droite, 3 = prêt
        etape = 1 if sel_p1 is None else (2 if sel_p2 is None else 3)
        # Combattant affiche dans chaque slot (selectionne, sinon apercu du survol)
        gauche = sel_p1 or (survole if etape == 1 else None)
        droite = sel_p2 or (survole if etape == 2 else None)

        # --- Musique du menu baissee en douceur (lissage vers 40% du plein) ---
        vol_mus += (_vol_plein * 0.40 - vol_mus) * 0.10
        try:
            pygame.mixer.music.set_volume(min(1.0, vol_mus))
        except Exception:
            pass

        # --- Fond dedie + brume qui derive (comme Options) ---
        if CHOOSE_BG is not None:
            screen.blit(CHOOSE_BG, (0, 0))
            off = int((tick * 0.45) % SCREEN_WIDTH)
            yb = int(math.sin(tick * 0.02) * 7)
            screen.blit(FOG_CHOOSE, (off - SCREEN_WIDTH, yb))
            screen.blit(FOG_CHOOSE, (off, yb))
        else:
            draw_background_animation(0)
            screen.blit(apply_blur(screen.copy()), (0, 0))
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); ov.set_alpha(110); ov.fill(NOIR)
        screen.blit(ov, (0, 0))

        # Titre
        titre_parchemin(screen, "Choose Your Warriors", font_title1, (SCREEN_WIDTH // 2, 56))

        # Bandeaux PLAYER 1 / PLAYER 2 (le joueur actif clignote/brille)
        for cx, lab, col, actif in ((LEFT_CX, "Player 1", COL_P1, etape == 1),
                                    (RIGHT_CX, "Player 2", COL_P2, etape == 2)):
            c = col if (not actif or (tick // 8) % 2 == 0) else (255, 255, 255)
            pl = font_small.render(lab, True, c)
            screen.blit(pl, pl.get_rect(center=(cx, 104)))   # au-dessus du cadre

        # Cadres METALLIQUES (style plaque) + combattants animes CLIPPES au cadre
        for cx, nom, flip, bord in ((LEFT_CX, gauche, False, COL_P1),
                                    (RIGHT_CX, droite, True, COL_P2)):
            outer = pygame.Rect(0, 0, FRAME_W, FRAME_H); outer.center = (cx, FRAME_CY)
            plaque_metal(screen, outer)
            inner = outer.inflate(-2 * FRAME_MARGE, -2 * FRAME_MARGE)
            pygame.draw.rect(screen, (22, 20, 28), inner, 0, 6)        # renfoncement sombre
            if nom:
                frames = apercu_frames(nom)
                img, fdx = frames[_apercu_idx(frames, tick)]
                if flip:
                    img = pygame.transform.flip(img, True, False)
                    fdx = -fdx
                prev_clip = screen.get_clip()
                screen.set_clip(inner)                                  # le perso ne deborde PAS du cadre
                sh = pygame.Surface((220, 44), pygame.SRCALPHA)
                pygame.draw.ellipse(sh, (0, 0, 0, 120), sh.get_rect())
                screen.blit(sh, sh.get_rect(center=(cx, BASELINE + 4)))
                # centre les PIEDS (donc le corps) sur cx, pas la bbox
                screen.blit(img, img.get_rect(midbottom=(int(cx - fdx), BASELINE)))
                screen.set_clip(prev_clip)
            else:   # slot vide : "?" fantome
                q = font_title.render("?", True, (70, 70, 84))
                screen.blit(q, q.get_rect(center=inner.center))
            pygame.draw.rect(screen, (8, 7, 11), inner, 3, 6)          # ombre interieure (creux)
            pygame.draw.rect(screen, bord, inner, 2, 6)                # lisere couleur joueur

        # Noms + titres lore sous chaque combattant
        for cx, nom, col in ((LEFT_CX, gauche, COL_P1), (RIGHT_CX, droite, COL_P2)):
            if nom:
                nm = font_medium.render(nom, True, (236, 224, 200))
                screen.blit(nm, nm.get_rect(center=(cx, 622)))     # sous le cadre
                lo = font_lore.render(NOMS_TITRES.get(nom, ""), True, col)
                screen.blit(lo, lo.get_rect(center=(cx, 666)))

        # "VS" au centre
        vs = font_title.render("VS", True, (210, 200, 178))
        vso = font_title.render("VS", True, (20, 18, 14))
        screen.blit(vso, vso.get_rect(center=(SCREEN_WIDTH // 2 + 3, 393)))
        screen.blit(vs, vs.get_rect(center=(SCREEN_WIDTH // 2, 390)))

        # Roster (tuiles acier + cadres de selection rouge/bleu)
        for nom, b in boutons.items():
            r = b.rect
            if sel_p2 == nom:
                pygame.draw.rect(screen, COL_P2, r.inflate(22, 22), 4, 6)
            if sel_p1 == nom:
                pygame.draw.rect(screen, COL_P1, r.inflate(10, 10), 4, 6)
            base = (92, 86, 73) if b.is_hovered else (54, 50, 43)
            pygame.draw.rect(screen, (15, 14, 12), r.inflate(6, 6), 0, 6)
            pygame.draw.rect(screen, base, r, 0, 5)
            pygame.draw.rect(screen, (200, 124, 50) if b.is_hovered else (120, 112, 98), r, 2, 5)
            screen.blit(icones[nom], icones[nom].get_rect(center=r.center))
            if b.is_clicked(mouse_pos, mouse_clicked) and etape in (1, 2):
                if etape == 1:
                    sel_p1 = nom
                else:
                    sel_p2 = nom
                jouer_sfx(SFX_CHOOSE_PERSO.get(nom), 0.9)   # son de "choose" du perso

        # Boutons Back (toujours) + Choose Map (etape 3)
        button_back.check_hover(mouse_pos)
        button_back.draw(screen)
        if etape == 3:
            button_map.check_hover(mouse_pos)
            button_map.draw(screen)

        if button_back.is_clicked(mouse_pos, mouse_clicked):
            if etape == 1:
                return (MENU_ACCUEIL, perso1, perso2)
            elif etape == 2:
                sel_p1 = None
            else:
                sel_p2 = None
        if etape == 3 and button_map.is_clicked(mouse_pos, mouse_clicked):
            return (SELECTION_MAP, sel_p1, sel_p2)   # -> ecran de choix de la map

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


# Fond de l'ecran de choix de la map (charge 1x).
try:
    _mbg = pygame.image.load("assets/BACKGROUND/map/map bg.png").convert()
    MAP_BG = pygame.transform.smoothscale(_mbg, (SCREEN_WIDTH, SCREEN_HEIGHT))
except Exception:
    MAP_BG = None

_apercu_map_cache = {}   # id map -> miniature (apercu de la map) pour le cadre


def _apercu_map(map_id, taille):
    """Miniature d'une map (apercu = le decor de combat) pour son cadre, mise en cache."""
    cle = (map_id, taille)
    th = _apercu_map_cache.get(cle)
    if th is None:
        src = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        src.blit(frames_de_map(map_id)[0], (0, 0))   # decor PROPRE a cette map
        src.blit(props_de_map(map_id), (0, 0))       # props PROPRES a cette map
        th = pygame.transform.smoothscale(src, taille)
        _apercu_map_cache[cle] = th
    return th


def choose_map(perso1, perso2):
    """Choix du champ de bataille (apres la selection des persos). Un cadre par map,
    positionne sur la carte (MAPS[...]['pos']). Une fois une map choisie -> bouton Fight."""
    global MAP_ACTUELLE
    TH_W, TH_H, M = 100, 60, 7                     # petit cadre-marqueur (miniature + marge)
    font_tip = pygame.font.Font("assets/fonts/OldLondon.ttf", 32)   # nom au survol
    sel_map = MAP_ACTUELLE if MAP_ACTUELLE in MAPS else None
    survol_prev = None
    button_back = Button(50, 800, "Back", 190, 64)
    button_fight = Button(SCREEN_WIDTH - 390, 770, "Fight !", 330, 100)
    tick = 0

    while True:
        clock.tick(FPS)
        tick += 1
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return ("quitter", perso1, perso2)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return (MENU_ACCUEIL, perso1, perso2)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_clicked = True

        # Fond (la carte) + leger voile pour la lisibilite
        if MAP_BG is not None:
            screen.blit(MAP_BG, (0, 0))
        else:
            draw_background_animation(0)
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); ov.set_alpha(70); ov.fill(NOIR)
        screen.blit(ov, (0, 0))

        titre_parchemin(screen, "Choose the Battlefield", font_title1, (SCREEN_WIDTH // 2, 70))

        # Un cadre (metal) par map, a sa position sur la carte
        survol = None
        for mid, data in MAPS.items():
            if data.get("cache_roster"):     # ex: temple_day (entrainement) -> pas dans le roster
                continue
            outer = pygame.Rect(0, 0, TH_W + 2 * M, TH_H + 2 * M)
            outer.center = data.get("pos", (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2))
            hovered = outer.collidepoint(mouse_pos)
            if hovered:
                survol = mid
            plaque_metal(screen, outer)
            inner = outer.inflate(-2 * M, -2 * M)
            screen.blit(_apercu_map(mid, (TH_W, TH_H)), inner.topleft)
            # bordure : or si selectionnee, claire au survol, sinon acier
            bord = GOLD if sel_map == mid else ((228, 198, 120) if hovered else (120, 112, 98))
            pygame.draw.rect(screen, bord, inner, 4 if sel_map == mid else 3, 4)
            # nom : UNIQUEMENT au survol, en petit cartouche sous le cadre
            if hovered:
                lab = font_tip.render(data.get("label", mid), True, (238, 226, 202))
                lr = lab.get_rect(midtop=(outer.centerx, outer.bottom + 7))
                fond = lr.inflate(22, 12)
                tip = pygame.Surface(fond.size, pygame.SRCALPHA)
                pygame.draw.rect(tip, (10, 9, 14, 205), tip.get_rect(), 0, 8)
                pygame.draw.rect(tip, (205, 170, 69), tip.get_rect(), 1, 8)
                screen.blit(tip, fond.topleft)
                screen.blit(lab, lr)
            if hovered and mouse_clicked:
                sel_map = mid
                jouer_sfx(SFX_CLICK, GAIN_CLICK)

        if survol is not None and survol != survol_prev:
            jouer_sfx(SFX_HOVER, GAIN_HOVER)
        survol_prev = survol

        # Boutons : Back (toujours) + Fight (une fois une map choisie)
        button_back.check_hover(mouse_pos)
        button_back.draw(screen)
        if sel_map is not None:
            button_fight.check_hover(mouse_pos)
            button_fight.draw(screen)

        if button_back.is_clicked(mouse_pos, mouse_clicked):
            return (SELECTION, perso1, perso2)            # retour au choix des persos (gardes)
        if sel_map is not None and button_fight.is_clicked(mouse_pos, mouse_clicked):
            MAP_ACTUELLE = sel_map
            arreter_musique()                             # coupe la musique du menu avant le combat
            return (JEU, perso1, perso2)

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


def menu_pause(perso1, perso2):
    stop_sfx_combat()   # coupe les sons de combat qui trainent (surtout les longs) a l'ouverture
    button_reprendre = Button(SCREEN_WIDTH // 2 - 250, 300, "Keep Fighting !", 500, 104)  # primaire
    button_replis  = Button(SCREEN_WIDTH // 2 - 150, 440, "Retreat", 300, 68)   # -> choix des persos
    button_menu    = Button(SCREEN_WIDTH // 2 - 150, 520, "Surrender", 300, 68)  # -> menu principal
    button_options = Button(SCREEN_WIDTH // 2 - 125, 600, "Options", 250, 68)

    # Capturer l'arrière-plan une seule fois
    background = screen.copy()
    blurred_bg = apply_blur(background)
    
    pause_running = True
    while pause_running:
        clock.tick(FPS)
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return ("quitter", perso1, perso2)
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return (JEU, perso1, perso2)
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    mouse_clicked = True
        
        # Assombrir l'écran
        screen.blit(blurred_bg, (0, 0))
        overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        overlay.set_alpha(100)
        overlay.fill(NOIR)
        screen.blit(overlay, (0, 0))
        
        # Titre
        titre_parchemin(screen, "Pause", font_title, (SCREEN_WIDTH // 2, 150))
        
        # Boutons
        for b in (button_reprendre, button_replis, button_menu, button_options):
            b.check_hover(mouse_pos)
            b.draw(screen)

        # Vérifier les clics
        if button_reprendre.is_clicked(mouse_pos, mouse_clicked):
            return (JEU, perso1, perso2)
        if button_replis.is_clicked(mouse_pos, mouse_clicked):
            # RETRAITE : retour au choix des persos en gardant le mode (solo IA ou local :
            # IA_NIVEAU persiste -> le prochain match repart dans le meme mode).
            return (SELECTION, perso1, perso2)
        if button_menu.is_clicked(mouse_pos, mouse_clicked):
            fond = screen.copy()
            if confirmer("Are you sure?", fond):
                return (MENU_ACCUEIL, perso1, perso2)
        if button_options.is_clicked(mouse_pos, mouse_clicked):
            resultat = options(PAUSE, perso1, perso2, blurred_bg)
            if resultat[0] != PAUSE:
                return resultat
        
        # Instructions
        info_text = font_small.render("Press ESC to continue", True, GRAY)
        info_rect = info_text.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT - 50))
        screen.blit(info_text, info_rect)

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()
    
    return ("quitter", perso1, perso2)


_GLOW_CACHE = {}


def _glow_radial(rayon, couleur, intensite=0.6):
    """Halo radial doux (degrade vers le bord), mis en cache, pour magnifier le
    vainqueur. Le degrade est bake dans le RGB (blit additif -> BLEND_RGB_ADD ignore
    l'alpha) : noir aux bords (n'ajoute rien), couleur du joueur au centre."""
    cle = (rayon, couleur, intensite)
    g = _GLOW_CACHE.get(cle)
    if g is None:
        g = pygame.Surface((2 * rayon, 2 * rayon))   # fond noir : invisible en additif
        for r in range(rayon, 0, -2):
            f = (1 - r / rayon) ** 2 * intensite      # 0 au bord -> intensite au centre
            col = (int(couleur[0] * f), int(couleur[1] * f), int(couleur[2] * f))
            pygame.draw.circle(g, col, (rayon, rayon), r)
        _GLOW_CACHE[cle] = g
    return g


def afficher_victoire(perso1, perso2, gagnant, background_surface):
    """Écran de fin de MATCH. gagnant = 0 (Joueur 1) ou 1 (Joueur 2)."""
    button_rejouer = Button(SCREEN_WIDTH // 2 - 220, 730, "New Battle", 440, 92)    # primaire
    button_menu = Button(SCREEN_WIDTH // 2 - 165, 834, "Back to Menu", 330, 54)     # secondaire

    winner = perso2 if gagnant == 1 else perso1
    col_joueur = (88, 132, 230) if gagnant == 1 else (214, 78, 78)   # bleu J2 / rouge J1
    joueur_lbl = "Player 2" if gagnant == 1 else "Player 1"
    win_text = "Defeated the Besiegers !" if gagnant == 1 else "Has Besieged Grimgate !"
    sous_titre = NOMS_TITRES.get(winner, "")
    flip = (gagnant == 1)              # le J2 regarde a gauche (comme en combat)
    # Sprite du vainqueur, pre-mis a l'echelle plus petit (place pour le nom + la phrase).
    _fr = apercu_frames(winner)
    _sc = 320 / _fr[0][0].get_height()
    frames = [(pygame.transform.smoothscale(im, (max(1, int(im.get_width() * _sc)), 320)), dxv * _sc)
              for im, dxv in _fr]
    BASELINE = 602                     # pieds du vainqueur
    glow = _glow_radial(300, col_joueur)
    font_flavor = pygame.font.Font("assets/fonts/OldLondon.ttf", 48)
    font_sub = pygame.font.Font("assets/fonts/OldLondon.ttf", 34)

    # Fond PROPRE = la map seule (floutee), SANS le HUD du combat (K.O., barres de vie,
    # titres des persos) qui surchargeait l'ecran. On n'utilise donc pas la frame finale.
    try:
        _fond = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        _fond.blit(animation_frames[0], (0, 0))
        _fond.blit(PROPS_SCALED, (0, 0))
        blurred_bg = apply_blur(_fond)
    except Exception:
        blurred_bg = apply_blur(background_surface)

    # Annonce vocale "[Vainqueur] Win !" : le NOM du perso puis "Win !" juste apres.
    jouer_sfx(SFX_CHOOSE_PERSO.get(winner), 1.0)
    try:
        _delai_win = int(SFX_CHOOSE_PERSO[winner][0].get_length() * FPS)
    except Exception:
        _delai_win = 16
    _win_joue = False

    tick = 0
    while True:
        clock.tick(FPS)
        tick += 1
        if not _win_joue and tick >= _delai_win:   # "Win !" enchaine apres le nom
            jouer_sfx(SFX_WIN, 1.0)
            _win_joue = True
        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return ("quitter", perso1, perso2)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return (MENU_ACCUEIL, perso1, perso2)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_clicked = True

        # Fond floute + voile sombre
        screen.blit(blurred_bg, (0, 0))
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); ov.set_alpha(150); ov.fill(NOIR)
        screen.blit(ov, (0, 0))

        # Lueur + ombre au sol + sprite anime du vainqueur
        screen.blit(glow, glow.get_rect(center=(SCREEN_WIDTH // 2, 408)),
                    special_flags=pygame.BLEND_RGB_ADD)
        sh = pygame.Surface((260, 50), pygame.SRCALPHA)
        pygame.draw.ellipse(sh, (0, 0, 0, 130), sh.get_rect())
        screen.blit(sh, sh.get_rect(center=(SCREEN_WIDTH // 2, BASELINE + 6)))
        img, dx = frames[_apercu_idx(frames, tick)]
        if flip:
            img = pygame.transform.flip(img, True, False); dx = -dx
        screen.blit(img, img.get_rect(midbottom=(int(SCREEN_WIDTH // 2 - dx), BASELINE)))

        # En-tete : NOM du vainqueur (gros, grave) puis la phrase qui le SUIT.
        titre_parchemin(screen, winner, font_title, (SCREEN_WIDTH // 2, 76), GOLD)
        wt = font_flavor.render(win_text, True, (224, 212, 188))
        screen.blit(wt, wt.get_rect(center=(SCREEN_WIDTH // 2, 158)))

        # Bas : plaque + camp vainqueur selon le COTE (rouge J1 = Nouvelle Couronne,
        # bleu J2 = Couronne d'Eruvia).
        camp = "Victory for the New Crown" if gagnant == 0 else "Victory for Eruvia's Crown"
        st = font_sub.render(camp, True, col_joueur)
        plaque = pygame.Surface((st.get_width() + 80, 64), pygame.SRCALPHA)
        pygame.draw.rect(plaque, (10, 9, 14, 175), plaque.get_rect(), 0, 14)
        pygame.draw.rect(plaque, col_joueur + (200,), plaque.get_rect(), 2, 14)
        screen.blit(plaque, plaque.get_rect(center=(SCREEN_WIDTH // 2, 656)))
        screen.blit(st, st.get_rect(center=(SCREEN_WIDTH // 2, 656)))

        # Boutons
        button_rejouer.check_hover(mouse_pos)
        button_menu.check_hover(mouse_pos)
        button_rejouer.draw(screen)
        button_menu.draw(screen)
        if button_rejouer.is_clicked(mouse_pos, mouse_clicked):
            return (SELECTION, perso1, perso2)
        if button_menu.is_clicked(mouse_pos, mouse_clicked):
            return (MENU_ACCUEIL, perso1, perso2)

        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


def anim_titre_intro(title, dest, tt, dur, big=2.8, hold=0.35):
    """Dessine un nom qui apparaît GRAND au centre de l'écran, y reste un
    instant (hold), puis glisse/rétrécit jusqu'à sa position normale (dest)."""
    p = min(1.0, tt / dur)
    if p <= hold:
        ease = 0.0                              # reste grand au centre
    else:
        q = (p - hold) / (1 - hold)
        ease = 1 - (1 - q) ** 3                 # ease-out vers sa place
    scale = big * (1 - ease) + 1.0 * ease       # de big -> 1
    w = max(1, int(title.get_width() * scale))
    h = max(1, int(title.get_height() * scale))
    img = pygame.transform.smoothscale(title, (w, h))
    centre = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    dest_centre = (dest[0] + title.get_width() // 2, dest[1] + title.get_height() // 2)
    cx = centre[0] * (1 - ease) + dest_centre[0] * ease
    cy = centre[1] * (1 - ease) + dest_centre[1] * ease
    screen.blit(img, img.get_rect(center=(int(cx), int(cy))).topleft)


def draw_compte(label, local, dur):
    """Affiche un élément du décompte (3/2/1/FIGHT) avec un effet de pop."""
    p = local / dur
    if p < 0.35:
        scale = 1.6 - 0.6 * (p / 0.35)          # entre en rétrécissant un peu
    elif p > 0.8:
        scale = 1.0 + 0.9 * ((p - 0.8) / 0.2)   # sort en grossissant
    else:
        scale = 1.0
    couleur = (220, 80, 60) if "FIGHT" in label else (216, 204, 178)
    txt = font_title2.render(label, True, couleur)
    txt = pygame.transform.rotozoom(txt, 0, scale)
    rect = txt.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 40))
    screen.blit(txt, rect)


def _action_idx(f, name):
    """Indice de l'animation 'idle' / 'run' quel que soit le type de perso
    (Fighter expose self.actions ; Konrad/Arinya ont des constantes IDLE/RUN)."""
    if hasattr(f, "actions"):
        return f.actions[name]
    return getattr(f, name.upper())


def intro_combat(fighter_1, fighter_2):
    """Intro d'un combat : les persos arrivent LENTEMENT EN MARCHANT sur le
    terrain et, PENDANT leur arrivée, leurs noms s'affichent (grand au centre
    puis glissent à leur place) et le décompte 3-2-1-FIGHT se déroule.
    Renvoie True si on doit quitter."""
    fin_x1, fin_x2 = fighter_1.rect.x, fighter_2.rect.x
    start_x1 = -fighter_1.rect.width - 40       # hors écran à gauche
    start_x2 = SCREEN_WIDTH + 40                # hors écran à droite
    fighter_1.title_hidden = True               # on masque les noms statiques
    fighter_2.title_hidden = True
    dest1, dest2 = fighter_1.title_dest(), fighter_2.title_dest()
    idle1, run1 = _action_idx(fighter_1, "idle"), _action_idx(fighter_1, "run")
    idle2, run2 = _action_idx(fighter_2, "idle"), _action_idx(fighter_2, "run")

    # Tout se chevauche pendant la marche (durées en frames @30fps).
    WALK = 96                       # arrivée lente en marchant
    NL, NR, NDUR = 8, 28, 30        # noms : départ gauche, départ droite, durée
    CD_START, CD, FIGHT = 50, 16, 26
    fin = max(WALK, CD_START + 3 * CD + FIGHT)

    index_frame_actu = 0
    bg_timer = 0
    compte_prev = -1       # dernier segment de decompte joue (annonceur audio)
    t = 0
    jouer_sfx(SFX_ROUND[0], 1.0)   # annonce vocale "Round 1" (debut de match)
    while t < fin:
        clock.tick(FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                fighter_1.title_hidden = False
                fighter_2.title_hidden = False
                return True

        # Fond animé plus lent (avance moins souvent)
        bg_timer += 1
        if bg_timer >= anim_speed:
            bg_timer -= anim_speed                    # cadence de la map (intro/annonce)
            index_frame_actu = (index_frame_actu + 1) % frame_nbr
        draw_background_animation(index_frame_actu)

        # Arrivée en marchant : position linéaire + animation de course, puis idle
        if t < WALK:
            p = t / WALK
            fighter_1.rect.x = int(start_x1 + (fin_x1 - start_x1) * p)
            fighter_2.rect.x = int(start_x2 + (fin_x2 - start_x2) * p)
            fighter_1.action, fighter_2.action = run1, run2
            fighter_1.frame_index = (t // 4) % len(fighter_1.animation_list[run1])
            fighter_2.frame_index = (t // 4) % len(fighter_2.animation_list[run2])
        else:
            fighter_1.rect.x, fighter_2.rect.x = fin_x1, fin_x2
            fighter_1.action, fighter_2.action = idle1, idle2
            fighter_1.frame_index = ((t - WALK) // 6) % len(fighter_1.animation_list[idle1])
            fighter_2.frame_index = ((t - WALK) // 6) % len(fighter_2.animation_list[idle2])

        fighter_1.image = fighter_1.animation_list[fighter_1.action][fighter_1.frame_index]
        fighter_2.image = fighter_2.animation_list[fighter_2.action][fighter_2.frame_index]
        # Bruitages pendant l'arrivee : surtout les PAS de marche (les autres
        # evenements ne se declenchent pas pendant l'intro).
        detecter_sons_combat(fighter_1, fighter_2)
        detecter_sons_combat(fighter_2, fighter_1)
        dessiner_ombre(screen, fighter_1)
        dessiner_ombre(screen, fighter_2)
        fighter_1.draw(screen)
        fighter_2.draw(screen)
        classes.debug_draw(screen)

        draw_healthbar(fighter_1.max_health, fighter_1.health, fighter_1.health, 20, 20)
        draw_healthbar(fighter_2.max_health, fighter_2.health, fighter_2.health, SCREEN_WIDTH - 420, 20)
        screen.blit(PROPS_SCALED, (0, 0))

        # Noms (apparaissent pendant l'arrivée), puis restent à leur place
        if t >= NL:
            if t < NL + NDUR:
                anim_titre_intro(fighter_1.title, dest1, t - NL, NDUR)
            else:
                screen.blit(fighter_1.title, dest1)
        if t >= NR:
            if t < NR + NDUR:
                anim_titre_intro(fighter_2.title, dest2, t - NR, NDUR)
            else:
                screen.blit(fighter_2.title, dest2)

        # Décompte 3 - 2 - 1 - FIGHT ! (pendant l'arrivée) + annonceur audio
        if t >= CD_START:
            tc = t - CD_START
            acc = 0
            for i, (label, dur) in enumerate((("3", CD), ("2", CD), ("1", CD), ("FIGHT !", FIGHT))):
                if tc < acc + dur:
                    if i != compte_prev:
                        jouer_sfx(SFX_COMPTE[i], 0.95)
                        compte_prev = i
                    draw_compte(label, tc - acc, dur)
                    break
                acc += dur

        disperser_si_besoin()
        pygame.display.flip()
        t += 1

    fighter_1.title_hidden = False
    fighter_2.title_hidden = False
    return False


_chrono_cache = {}   # secondes (int) -> texte rendu (ne change qu'a la seconde, pas chaque frame)


def round_hud(scores, rounds_to_win, secondes):
    """Chrono (centre, en haut) + pips des rounds gagnes par chaque joueur."""
    cx = SCREEN_WIDTH // 2
    ACIER = (216, 204, 178)     # couleur "acier" (assortie aux boutons)
    # Chrono dans une boite
    box = pygame.Rect(0, 0, 120, 62)
    box.center = (cx, 46)
    pygame.draw.rect(screen, NOIR, box, 0, 10)
    pygame.draw.rect(screen, ACIER, box, 3, 10)
    sec = max(0, secondes)
    txt = _chrono_cache.get(sec)
    if txt is None:
        txt = font_medium.render(str(sec), True, ACIER)
        _chrono_cache[sec] = txt
    screen.blit(txt, txt.get_rect(center=(cx, 44)))
    # Pips de rounds (Joueur 1 a gauche du chrono, Joueur 2 a droite)
    rayon = 11
    for i in range(rounds_to_win):
        x1 = cx - 82 - i * 30
        pygame.draw.circle(screen, ACIER if scores[0] > i else (55, 55, 75), (x1, 46), rayon)
        pygame.draw.circle(screen, ACIER, (x1, 46), rayon, 2)
        x2 = cx + 82 + i * 30
        pygame.draw.circle(screen, ACIER if scores[1] > i else (55, 55, 75), (x2, 46), rayon)
        pygame.draw.circle(screen, ACIER, (x2, 46), rayon, 2)


_NET_COUL = {"ok": (92, 202, 96), "lag": (232, 198, 74), "bad": (222, 84, 60)}
_f_net = None


def indicateur_reseau(session):
    """Pastille de SANTE reseau (coin haut-gauche) pendant un match LAN : point VERT (fluide) /
    JAUNE (lag) / ROUGE (decroche) + attente moyenne en ms (temps passe a attendre le pair)."""
    global _f_net
    if _f_net is None:
        _f_net = pygame.font.SysFont("consolas,segoeui,arial", 20, bold=True)
    if getattr(session, "desync", False):                 # divergence detectee -> alerte rouge
        coul = _NET_COUL["bad"]
        txt = _f_net.render("DESYNC", True, (240, 170, 160))
    else:
        coul = _NET_COUL.get(session.sante(), _NET_COUL["ok"])
        txt = _f_net.render("NET  %dms" % int(round(max(session.attente_ms, 0))), True, (224, 216, 198))
    box = pygame.Rect(18, 18, 40 + txt.get_width(), 34)
    pygame.draw.rect(screen, (18, 16, 22), box, 0, 8)
    pygame.draw.rect(screen, (120, 110, 92), box, 1, 8)
    pygame.draw.circle(screen, coul, (box.x + 18, box.centery), 7)
    pygame.draw.circle(screen, (20, 18, 14), (box.x + 18, box.centery), 7, 1)
    screen.blit(txt, (box.x + 32, box.centery - txt.get_height() // 2))


def hash_etat(f1, f2):
    """Hash (FNV-1a 32 bits) de l'etat DETERMINISTE des 2 combattants -> garde anti-desync du
    reseau. On melange position, PV (au centieme, pour capter une divergence fractionnaire),
    action/frame affichee, cd d'attaque, usure du bouclier, stun. Doit etre IDENTIQUE des 2 cotes
    a chaque frame ; toute difference = bug de determinisme (a corriger)."""
    h = 2166136261
    for f in (f1, f2):
        vals = (f.rect.x, f.rect.y, int(round(f.health * 100)), int(getattr(f, "action", 0) or 0),
                getattr(f, "frame_index", 0), getattr(f, "attack_cd", 0),
                int(round(getattr(f, "block_health", 0) * 100)), 1 if getattr(f, "hit", False) else 0)
        for v in vals:
            h = ((h ^ (int(v) & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    return h


def annonce_round(fighter_1, fighter_2, numero_round):
    """Annonce 'ROUND X' puis decompte 3-2-1-FIGHT (persos en place, sans
    arrivee). Renvoie True si on doit quitter."""
    idle1 = _action_idx(fighter_1, "idle")
    idle2 = _action_idx(fighter_2, "idle")
    ROUND_TXT, CD, FIGHT = 30, 16, 22
    fin = ROUND_TXT + CD * 3 + FIGHT
    index_frame_actu = 0
    bg_timer = 0
    compte_prev = -1       # dernier segment de decompte joue (annonceur audio)
    t = 0
    jouer_sfx(SFX_ROUND[min(numero_round, 3) - 1], 1.0)   # annonce vocale "Round X" (clamp Round 3)
    while t < fin:
        clock.tick(FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
        bg_timer += 1
        if bg_timer >= anim_speed:
            bg_timer -= anim_speed                    # cadence de la map (intro/annonce)
            index_frame_actu = (index_frame_actu + 1) % frame_nbr
        draw_background_animation(index_frame_actu)
        for f, idle in ((fighter_1, idle1), (fighter_2, idle2)):
            f.action = idle
            f.frame_index = (t // 6) % len(f.animation_list[idle])
            f.image = f.animation_list[f.action][f.frame_index]
            dessiner_ombre(screen, f)
            f.draw(screen)
        classes.debug_draw(screen)
        draw_healthbar(fighter_1.max_health, fighter_1.health, fighter_1.health, 20, 20)
        draw_healthbar(fighter_2.max_health, fighter_2.health, fighter_2.health, SCREEN_WIDTH - 420, 20)
        screen.blit(PROPS_SCALED, (0, 0))
        if t < ROUND_TXT:
            txt = font_title.render("ROUND %d" % numero_round, True, GOLD)
            screen.blit(txt, txt.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 40)))
        else:
            tc = t - ROUND_TXT
            acc = 0
            for i, (label, dur) in enumerate((("3", CD), ("2", CD), ("1", CD), ("FIGHT !", FIGHT))):
                if tc < acc + dur:
                    if i != compte_prev:
                        jouer_sfx(SFX_COMPTE[i], 0.95)
                        compte_prev = i
                    draw_compte(label, tc - acc, dur)
                    break
                acc += dur
        pygame.display.flip()
        t += 1
    return False


def _init_etats_combat(f):
    """Initialise sur un combattant TOUS les etats suivis par la boucle de combat : trails de PV,
    age de blocage (parade de lance), et les _snd_* de detecter_sons_combat. A appeler apres
    creation (round normal ET entrainement) -> sinon detecter_sons_combat plante (AttributeError)."""
    f.trail_health = f.health
    f.trail_delay = 0
    f.last_health = f.health
    f.block_age = 999      # frames depuis que le bouclier est leve (parade de lance)
    f._block_prev = False
    # etats suivis pour les bruitages de combat (cf detecter_sons_combat)
    f._snd_block = False
    f._snd_bh = getattr(f, "block_health", 0)
    f._snd_bcd = getattr(f, "block_cd", 0)
    f._snd_atk = False
    f._snd_swing_ok = True
    f._snd_wpn = getattr(f, "current_weapon", None)
    f._snd_jump = getattr(f, "jump", False)
    f._snd_hit = getattr(f, "hit", False)
    f._snd_alive = getattr(f, "alive", True)
    f._snd_frame = getattr(f, "frame_index", -1)
    f._snd_cle = None
    f._snd_light = getattr(f, "lightning", None) is not None
    f._snd_spark_t = 0
    f._snd_charging = getattr(f, "charging", False)
    f._canal_charge = None
    f._canal_flight = None
    f._snd_spear = getattr(f, "spear", None) is not None
    f._snd_spear_hd = f._snd_spear_gr = f._snd_spear_bo = False
    f._snd_has_spear = getattr(f, "has_spear", True)
    f._snd_dashing = getattr(f, "dashing", False)
    f._snd_tp = getattr(f, "teleporting", False)
    f._snd_sword = False
    f._hit_amorti = False  # coup encaisse sans stun (grace/armor) -> sons d'impact a jouer
    f._snd_death_cri = True    # etapes de la mort sequencee deja jouees
    f._snd_death_fall = True   # (resettees a False a la transition de mort)
    f._snd_death0 = False      # "death" retarde (death_frame) en attente de lecture
    f._snd_spin = False    # spin en cours (whoosh)
    f._snd_spin_cri = False    # hurlement du spin joue
    f._snd_jwhoosh = False     # jump-attack en vol (whoosh au decollage, impact a l'atterrissage)
    f._snd_sol = False         # impact au sol d'attack2 deja joue pour ce coup
    # canaux des sons LONGS (coupes en fondu des que l'etat qui les portait s'arrete)
    f._canal_spin = None; f._canal_spin_cri = None
    f._canal_jwhoosh = None; f._canal_hwhoosh = None; f._canal_death_cri = None
    f._snd_hthrow = False  # marteau lance (slam)
    f._snd_himpact = True  # impact au sol du marteau deja joue
    f._snd_ruee = True     # cri de la ruee vers le marteau deja joue
    f._snd_parade = 0      # derniere parade parfaite signalee (flash + son joues)
    f._fx_rep_t = 0        # cadence des particules de reparation du bouclier
    f._fx_rel_t = 0        # cadence des particules de garde brisee en recharge


def jouer_round(personnage_1, personnage_2, scores, numero_round, rounds_to_win, round_time, net=None):
    """Joue UN round. Renvoie l'indice du gagnant (0/1 ; -1 = egalite), ou un
    tuple d'etat si on quitte (fenetre fermee ou pause -> menu)."""
    reset_caches_combat()   # vide les caches de teinte/eclairs (memoire bornee au round)
    reset_horloge()         # horloge de simulation a zero (timing deterministe du round)
    reset_horloge_active()  # horloge des combos (figee en hitstop) a zero
    fighter_1 = PERSONNAGES[personnage_1]("Left", False)
    fighter_2 = PERSONNAGES[personnage_2]("Right", True)
    mettre_a_echelle(fighter_1, MAP_ECHELLE)   # taille des persos selon la map
    mettre_a_echelle(fighter_2, MAP_ECHELLE)
    fighter_1.rect.bottom = classes.SOL        # poser les pieds au sol de la map (des l'intro)
    fighter_2.rect.bottom = classes.SOL
    for f in (fighter_1, fighter_2):
        _init_etats_combat(f)
    fighter_1._nom = personnage_1
    fighter_2._nom = personnage_2
    # MODE SOLO : le J2 est pilote par l'IA (sauf en reseau). L'IA se branche comme un
    # clavier -> fighter_2.inputs = ia.decide(...) dans la boucle. None en 2-joueurs/LAN.
    ia = classes.IA(IA_NIVEAU) if (IA_NIVEAU is not None and net is None) else None

    # 1er round : arrivee + noms ; rounds suivants : simple annonce "ROUND X"
    if numero_round == 1:
        quitter = intro_combat(fighter_1, fighter_2)
    else:
        quitter = annonce_round(fighter_1, fighter_2, numero_round)
    if quitter:
        return ("quitter", personnage_1, personnage_2)

    index_frame_actu = 0
    animation_timer = 0
    timer_frames = round_time * FPS
    round_over = False
    mort_subite = False        # apres le chrono, si les deux vivent : drain des PV
    fin_delay = 0
    FIN_DELAY_MAX = 85
    gagnant = None
    hitstop = 0               # frames de gel d'impact restantes
    shake = 0                 # frames de tremblement d'ecran restantes
    HITSTOP_SEUIL = 5         # degats min pour declencher un hitstop
    particules.clear()
    prev_jump = [fighter_1.jump, fighter_2.jump]
    prev_dash = [getattr(fighter_1, "dashing", False), getattr(fighter_2, "dashing", False)]

    while True:
        clock.tick(FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return ("quitter", personnage_1, personnage_2)
            if event.type == pygame.KEYDOWN and not round_over:
                if event.key == pygame.K_ESCAPE:
                    if net is not None:
                        return ("reseau_quitte", personnage_1, personnage_2)   # pas de pause en reseau
                    resultat = menu_pause(personnage_1, personnage_2)
                    if resultat[0] != JEU:
                        return resultat

        # HITSTOP : au moment d'un impact, tout se fige quelques frames (persos,
        # chrono, fond) pour donner du poids au coup. On continue a dessiner.
        # L'horloge avance a CHAQUE frame (hitstop compris) : le timing des combos compte
        # le temps reel ecoule comme avant (sinon le gel d'impact decalait les fenetres).
        # Reste deterministe (la duree du hitstop est identique des 2 cotes en lockstep).
        avancer_horloge()
        en_hitstop = hitstop > 0
        if en_hitstop:
            hitstop -= 1
        else:
            avancer_horloge_active()   # horloge des combos : n'avance PAS pendant le hitstop
            animation_timer += 1
            if animation_timer >= anim_speed:
                animation_timer -= anim_speed        # garde le reste -> cadence exacte
                index_frame_actu = (index_frame_actu + 1) % frame_nbr

            # Chrono : a 0, si les deux sont en vie -> MORT SUBITE (drain des PV)
            if not round_over and not mort_subite and timer_frames > 0:
                timer_frames -= 1
            if not round_over and not mort_subite and timer_frames <= 0:
                if fighter_1.health > 0 and fighter_2.health > 0:
                    mort_subite = True
                    jouer_sfx(SFX_SUDDEN, 1.0)        # annonce vocale "Sudden Death !"

            # Round fini : controles coupes, mais la physique continue
            if round_over:
                fighter_1.controls_enabled = False
                fighter_2.controls_enabled = False

            hp_av1, hp_av2 = fighter_1.health, fighter_2.health
            # Inputs : en LOCAL, les 2 claviers ; en RESEAU (lockstep), on echange notre
            # input local (touches Joueur 1) et on recoit celui du pair.
            if net is not None:
                # Touches du joueur LOCAL en LAN = profil choisi (Options/menu multi : Left ou Right).
                _cl = SETTINGS.get("touches_lan", "Left")
                _cl = _cl if _cl in ("Left", "Right") else "Left"
                # on joint le HASH de l'etat courant (garde anti-desync) : les 2 cotes hashent le
                # meme etat deterministe -> si ca diverge, un bug de determinisme est detecte.
                il, ir = net.echanger(lire_inputs(_cl), hash_etat(fighter_1, fighter_2))
                if not net.alive:
                    return ("reseau_perdu", personnage_1, personnage_2)
                if net.desync:
                    return ("reseau_desync", personnage_1, personnage_2)
                fighter_1.inputs = il
                fighter_2.inputs = ir
            else:
                fighter_1.inputs = lire_inputs("Left")
                # J2 : IA en mode solo, sinon 2e clavier
                fighter_2.inputs = ia.decide(fighter_2, fighter_1) if ia else lire_inputs("Right")
            fighter_1.mvmt(screen, fighter_2)
            fighter_2.mvmt(screen, fighter_1)
            # Age du blocage (apres mvmt = bouclier pose, avant update = collision lance) :
            # 0 a la frame ou on leve le bouclier, +1 ensuite -> sert a la parade de lance.
            for f in (fighter_1, fighter_2):
                f.block_age = 0 if (f.block and not f._block_prev) else (f.block_age + 1 if f.block else 999)
                f._block_prev = f.block
            fighter_1.update(screen, fighter_2)
            fighter_2.update(screen, fighter_1)
            # Bruitages de combat (bouclier generique + sons perso), apres maj de l'etat
            detecter_sons_combat(fighter_1, fighter_2)
            detecter_sons_combat(fighter_2, fighter_1)

            # Mort subite : les deux PV fondent (8% du max/s) jusqu'a une mort
            if mort_subite and not round_over:
                for f in (fighter_1, fighter_2):
                    f.health -= 0.08 * f.max_health / FPS
                    if f.health < 0:
                        f.health = 0

            # Un coup vient de passer -> HITSTOP (+ screen shake si gros coup).
            # On retire le drain de la mort subite pour ne compter que les coups.
            drain = (0.08 * (fighter_1.max_health + fighter_2.max_health) / FPS) if (mort_subite and not round_over) else 0
            degats = (hp_av1 - fighter_1.health) + (hp_av2 - fighter_2.health) - drain
            if degats > HITSTOP_SEUIL:
                hitstop = 2 + int(min(degats, 40) * 0.10)   # ~2 a 6 frames selon les degats
                if degats >= 18:                            # gros coup -> tremblement
                    shake = 6

            # Particules : saut (tous les persos) + dash au sol / en l'air (Arinya).
            # On detecte le passage False -> True de jump / dashing.
            for i, f in enumerate((fighter_1, fighter_2)):
                jnow = f.jump
                # poussiere de saut au DECOLLAGE (False->True) ET a l'ATTERRISSAGE
                # (True->False) ; centree sur les pieds, taille = ecart des pieds.
                if jnow != prev_jump[i]:
                    fcx, fw = feet_info(f)
                    spawn_particule("jump", fcx, classes.SOL, f.flip, fw)   # au SOL (pas rect.bottom :
                    #   decale pour Barrion en jump_attack -> poussiere flottait en l'air)
                prev_jump[i] = jnow
                dnow = getattr(f, "dashing", False)
                if dnow and not prev_dash[i]:
                    fcx, fw = feet_info(f)
                    kind = "dash_air" if f.jump else "dash"
                    spawn_particule(kind, fcx, f.rect.bottom, f.flip, fw)
                prev_dash[i] = dnow

        draw_background_animation(index_frame_actu)   # map (bleu nuit) -> non teintee
        dessiner_ombre(screen, fighter_1)              # ombres au sol sous les persos
        dessiner_ombre(screen, fighter_2)
        fighter_1.draw(screen)                         # sprites/effets teintes dans draw()
        fighter_2.draw(screen)
        classes.debug_draw(screen)                     # boites debug (menu Display > Debug)
        maj_particules(avance=not en_hitstop)          # poussiere teintee aussi

        # Barre de degats (trail rouge)
        for f in (fighter_1, fighter_2):
            if f.health < f.last_health:
                f.trail_delay = TRAIL_DELAY
            f.last_health = f.health
            if f.trail_health > f.health:
                if f.trail_delay > 0:
                    f.trail_delay -= 1
                else:
                    f.trail_health = max(f.health, f.trail_health - TRAIL_SPEED)
            else:
                f.trail_health = f.health

        draw_healthbar(fighter_1.max_health, fighter_1.health, fighter_1.trail_health, 20, 20)
        draw_healthbar(fighter_2.max_health, fighter_2.health, fighter_2.trail_health, SCREEN_WIDTH - 420, 20)
        screen.blit(PROPS_SCALED, (0, 0))

        # Effets electriques de Stormr (etincelles/jauge/foudre sur la cible),
        # dessines PAR-DESSUS les deux combattants et les props.
        for f in (fighter_1, fighter_2):
            overlay = getattr(f, "draw_static_overlay", None)
            if overlay:
                overlay(screen)

        secondes = (timer_frames + FPS - 1) // FPS
        round_hud(scores, rounds_to_win, secondes)
        if net is not None:                       # match LAN : pastille de sante reseau
            indicateur_reseau(net)

        # Banniere "SUDDEN DEATH" pendant le drain (tant que personne ne tombe)
        if mort_subite and not round_over:
            sd = font_title.render("SUDDEN DEATH !", True, (220, 80, 60))
            screen.blit(sd, sd.get_rect(center=(SCREEN_WIDTH // 2, 150)))

        # Fin de round : un perso tombe a 0 (combat normal OU mort subite)
        if not round_over and (fighter_1.health <= 0 or fighter_2.health <= 0):
            round_over = True
            jouer_sfx(SFX_KO, 1.0)        # annonce vocale "K.O. !"

        if round_over:
            if gagnant is None:
                if mort_subite:
                    # Mort subite : c'est le plus faible en PV qui perd. Si l'ecart
                    # est minuscule (PV egaux, ils tombent ensemble) -> match nul,
                    # et on met les DEUX a 0 pour que les DEUX anims de mort jouent
                    # (sinon le survivant a ~1 PV restait debout, un seul mourait).
                    # (evite que les arrondis flottants designent toujours le meme.)
                    diff = fighter_1.health - fighter_2.health
                    if abs(diff) < 2:
                        gagnant = -1
                        fighter_1.health = 0
                        fighter_2.health = 0
                    elif diff < 0:
                        gagnant = 1
                    else:
                        gagnant = 0
                elif fighter_1.health <= 0 and fighter_2.health <= 0:
                    gagnant = -1
                elif fighter_1.health <= 0:
                    gagnant = 1
                else:
                    gagnant = 0
            t = font_title.render("K.O. !", True, (220, 80, 60))
            screen.blit(t, t.get_rect(center=(SCREEN_WIDTH // 2, 230)))
            fin_delay += 1
            if fin_delay >= FIN_DELAY_MAX:
                return gagnant

        # Screen shake : decale toute l'image de quelques pixels sur un gros coup
        if shake > 0:
            frame = screen.copy()
            screen.fill((8, 8, 12))
            screen.blit(frame, (random.randint(-5, 5), random.randint(-5, 5)))
            shake -= 1

        pygame.display.flip()


def jeu(personnage_1, personnage_2, net=None):
    """Match en plusieurs rounds (best of 3) avec chrono par round. net != None -> combat
    en RESEAU lockstep (inputs echanges chaque frame avec le pair)."""
    # Decor + props + cadence + musique de la map choisie (tout le match : intro + rounds).
    global animation_frames, frame_nbr, PROPS_SCALED, anim_speed, MAP_ECHELLE
    animation_frames = frames_de_map(MAP_ACTUELLE)
    frame_nbr = len(animation_frames)
    PROPS_SCALED = props_de_map(MAP_ACTUELLE)
    dt = MAPS.get(MAP_ACTUELLE, {}).get("frame_dt")   # secondes/frame voulues (sinon defaut)
    anim_speed = dt * FPS if dt else ANIMATION_SPEED   # -> en frames de jeu (peut etre fractionnaire)
    _mp = MAPS.get(MAP_ACTUELLE, {})
    set_filtre_nuit(_mp.get("night", False), _mp.get("tint"), _mp.get("halo", True))   # tint + halo par map
    set_sol(MAPS.get(MAP_ACTUELLE, {}).get("ground", 850))           # niveau du sol par map
    MAP_ECHELLE = MAPS.get(MAP_ACTUELLE, {}).get("scale", 1.0)       # taille des persos par map
    jouer_musique(MAPS.get(MAP_ACTUELLE, {}).get("music", MUSIQUE_MENU))
    # Statut Discord : "Kenshi vs Stormr" + mode/map, vignette de la map en grande
    # image et portrait du joueur LOCAL en pastille (en LAN : mon_cote dit qui je suis).
    _mode_rp = "LAN" if net else ("Solo" if IA_NIVEAU else "Local")
    _map_rp = "temple" if MAP_ACTUELLE == "temple_day" else MAP_ACTUELLE
    _perso_rp = personnage_2 if (net and getattr(net, "mon_cote", "Left") == "Right") else personnage_1
    discord_rp.maj("%s vs %s" % (personnage_1, personnage_2),
                   "%s - %s" % (_mode_rp, _mp.get("label", "")),
                   grande=_map_rp, grande_txt=_mp.get("label", ""),
                   petite=_perso_rp.lower(), petite_txt=_perso_rp)
    jouer_ambiance_map(MAP_ACTUELLE)   # ambiances de la map (boucle + superposees)
    ROUNDS_TO_WIN = 2          # premier a 2 rounds gagnes remporte le match
    ROUND_TIME = 120           # secondes par round (2 min) ; la mort subite n'arrive
                               #   qu'en dernier recours si les deux sont encore en vie
    scores = [0, 0]            # rounds gagnes par Joueur 1, Joueur 2
    numero_round = 1           # numero AFFICHE : n'avance QUE quand une manche est GAGNEE
    manches = 0                # total de manches jouees (nuls compris) : garde-fou anti-boucle

    # Une EGALITE (double K.O. / mort subite nulle) ne donne pas de point ET ne change PAS de
    # manche : on REJOUE le meme numero (ex. Round 1 nul -> a nouveau "Round 1"). On borne le
    # nombre TOTAL de manches (nuls compris) pour eviter une boucle infinie (2 joueurs AFK).
    while max(scores) < ROUNDS_TO_WIN and manches < 12:
        resultat = jouer_round(personnage_1, personnage_2, scores,
                               numero_round, ROUNDS_TO_WIN, ROUND_TIME, net=net)
        if isinstance(resultat, tuple):
            arreter_ambiance_map()                # sortie du combat (pause/quitter/reseau perdu)
            return resultat
        manches += 1
        if resultat == 0:
            scores[0] += 1; numero_round += 1
        elif resultat == 1:
            scores[1] += 1; numero_round += 1
        # resultat == -1 : EGALITE -> on rejoue la MEME manche (numero_round inchange)

    gagnant = 0 if scores[0] > scores[1] else 1
    arreter_ambiance_map()                        # fin de match
    if net is not None:
        return ("match_fini", gagnant)            # RESEAU : ecran de fin + rematch geres (lockstep) par flux_multijoueur
    final_screen = screen.copy()
    return afficher_victoire(personnage_1, personnage_2, gagnant, final_screen)


# ----------------------------------------------------------------------
#  POPUP "RACCOURCIS" au lancement : proposer un raccourci Bureau et/ou
#  Menu Demarrer (cases a cocher) + "ne plus demander". On ne propose QUE
#  les emplacements ou le raccourci n'existe pas encore (si l'un est cree,
#  le lancement suivant ne propose plus que l'autre).
# ----------------------------------------------------------------------
NOM_RACCOURCI = "The Siege of Grimgate.lnk"


def _ps(cmd):
    """Execute un petit script PowerShell SANS fenetre et renvoie stdout (ou '')."""
    try:
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=15,
                           creationflags=0x08000000)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _dossier_special(nom):
    """Chemin d'un dossier special Windows (ex. 'Desktop', 'Programs')."""
    return _ps("[Environment]::GetFolderPath('%s')" % nom)


def _raccourci_existe(nom_special):
    d = _dossier_special(nom_special)
    return bool(d) and os.path.exists(os.path.join(d, NOM_RACCOURCI))


def _creer_raccourci(nom_special):
    """Cree le raccourci (vers le lanceur, icone du jeu) dans le dossier special.
    Le script vit dans Scripts/ ; le lanceur .bat est a la RACINE (parent), l'icone
    dans Scripts/ (a cote du script)."""
    dossier_script = os.path.dirname(os.path.abspath(__file__))   # ...\Scripts
    racine = os.path.dirname(dossier_script)                       # ...\ (a le .bat)
    bat = os.path.join(racine, "Jouer TSOG.bat").replace("'", "''")
    ico = os.path.join(dossier_script, "Logo_build.ico").replace("'", "''")
    rac = racine.replace("'", "''")
    cmd = ("$d=[Environment]::GetFolderPath('%s');"
           "if($d){$w=New-Object -ComObject WScript.Shell;"
           "$s=$w.CreateShortcut((Join-Path $d '%s'));"
           "$s.TargetPath='%s';$s.WorkingDirectory='%s';"
           "$s.IconLocation='%s,0';$s.WindowStyle=7;"
           "$s.Description='The Siege of Grimgate';$s.Save()}"
           % (nom_special, NOM_RACCOURCI, bat, rac, ico))
    _ps(cmd)


def _case_cocher(surf, rect, coche):
    """Dessine une case a cocher (style metal)."""
    pygame.draw.rect(surf, (22, 20, 26), rect, 0, 5)
    pygame.draw.rect(surf, (208, 170, 70) if coche else (120, 112, 98), rect, 3, 5)
    if coche:
        pygame.draw.rect(surf, (210, 170, 70), rect.inflate(-14, -14), 0, 3)


def popup_raccourcis():
    """Au lancement : propose de creer un raccourci Bureau et/ou Menu Demarrer.
    Ne propose que les emplacements manquants ; case 'ne plus demander' persistante."""
    if sys.platform != "win32" or SETTINGS.get("raccourci_ne_plus_demander"):
        return
    cibles = []
    if not _raccourci_existe("Desktop"):
        cibles.append({"sp": "Desktop", "label": "Desktop", "on": True})
    if not _raccourci_existe("Programs"):
        cibles.append({"sp": "Programs", "label": "Start Menu", "on": True})
    if not cibles:
        # raccourcis deja partout : on memorise pour ne plus refaire les detections
        SETTINGS["raccourci_ne_plus_demander"] = True
        sauver_settings()
        return
    ne_plus = {"on": False}

    n = len(cibles)
    PW, ROW = 760, 60
    PH = 150 + n * ROW + 36 + 40 + 66 + 40
    panel = pygame.Rect(0, 0, PW, PH); panel.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    font_opt = font_medium
    y0 = panel.top + 130
    cases = [(pygame.Rect(panel.x + 80, y0 + i * ROW, 40, 40), c) for i, c in enumerate(cibles)]
    rect_neplus = pygame.Rect(panel.x + 80, y0 + n * ROW + 26, 30, 30)
    btn_creer = Button(panel.centerx - 250, panel.bottom - 86, "Create", 235, 64)
    btn_plus_tard = Button(panel.centerx + 15, panel.bottom - 86, "Later", 235, 64)

    fond = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); fond.fill(NOIR)
    while True:
        clock.tick(FPS)
        mouse = pygame.mouse.get_pos(); clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        if clic:                                   # toggles (case OU son libelle)
            for rect, c in cases:
                if pygame.Rect(rect.x, rect.y, 360, rect.height).collidepoint(mouse):
                    c["on"] = not c["on"]; jouer_sfx(SFX_CLICK, GAIN_CLICK)
            if pygame.Rect(rect_neplus.x, rect_neplus.y, 420, rect_neplus.height).collidepoint(mouse):
                ne_plus["on"] = not ne_plus["on"]; jouer_sfx(SFX_CLICK, GAIN_CLICK)

        screen.blit(fond, (0, 0))
        plaque_metal(screen, panel)
        titre_parchemin(screen, "Add a shortcut?", font_title1, (panel.centerx, panel.top + 56))
        for rect, c in cases:
            _case_cocher(screen, rect, c["on"])
            lbl = font_opt.render(c["label"], True, (232, 224, 206))
            screen.blit(lbl, lbl.get_rect(midleft=(rect.right + 20, rect.centery)))
        _case_cocher(screen, rect_neplus, ne_plus["on"])
        lbl = font_small.render("Don't ask again", True, (176, 170, 156))
        screen.blit(lbl, lbl.get_rect(midleft=(rect_neplus.right + 16, rect_neplus.centery)))

        for b in (btn_creer, btn_plus_tard):
            b.check_hover(mouse); b.draw(screen)
        fini = False
        if btn_creer.is_clicked(mouse, clic):
            for _, c in cases:
                if c["on"]:
                    _creer_raccourci(c["sp"])
            fini = True
        elif btn_plus_tard.is_clicked(mouse, clic):
            fini = True
        if fini:
            if ne_plus["on"]:
                SETTINGS["raccourci_ne_plus_demander"] = True
                sauver_settings()
            return

        dessiner_curseur(screen)
        pygame.display.flip()


# ----------------------------------------------------------------------
#  MULTIJOUEUR LAN (lockstep). Fond neutre pour l'instant (fond dedie a venir).
# ----------------------------------------------------------------------
_f_nom = pygame.font.Font("assets/fonts/OldLondon.ttf", 42)     # nom de session / saisie
_f_corps = pygame.font.Font("assets/fonts/OldLondon.ttf", 32)   # texte courant
_f_hint = pygame.font.Font("assets/fonts/OldLondon.ttf", 24)    # petites infos
_f_sys = pygame.font.SysFont("consolas,segoeui,arial", 28, bold=True)   # code/IP : police CLASSIQUE lisible


def _fond_multi(tick=0):
    """Fond ambiance (forge + brume qui derive, comme les Options) + voile sombre."""
    if FORGE_BG is not None:
        screen.blit(FORGE_BG, (0, 0))
        if FOG_OPT is not None:
            off = int((tick * 0.4) % SCREEN_WIDTH); yb = int(math.sin(tick * 0.02) * 7)
            screen.blit(FOG_OPT, (off - SCREEN_WIDTH, yb)); screen.blit(FOG_OPT, (off, yb))
    else:
        screen.fill((26, 23, 30))
    ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); ov.set_alpha(120); ov.fill(NOIR)
    screen.blit(ov, (0, 0))


def _panneau_multi(titre, sous=None, taille=(820, 470), tick=0):
    """Fond ambiance + panneau metal + titre (+ sous-titre). Renvoie le rect du panneau."""
    _fond_multi(tick)
    p = pygame.Rect(0, 0, taille[0], taille[1]); p.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    plaque_metal(screen, p)
    titre_parchemin(screen, titre, font_title1, (p.centerx, p.top + 54))
    if sous:
        s = _f_corps.render(sous, True, (198, 188, 166))
        screen.blit(s, s.get_rect(center=(p.centerx, p.top + 108)))
    return p


def _champ_saisie(p, label, valeur, tick, y=None):
    """Label + champ de saisie centre (curseur clignotant, texte clippe)."""
    cy = p.centery - 8 if y is None else y
    lab = _f_corps.render(label, True, (198, 188, 166))
    screen.blit(lab, lab.get_rect(center=(p.centerx, cy - 56)))
    champ = pygame.Rect(0, 0, 600, 70); champ.center = (p.centerx, cy + 6)
    pygame.draw.rect(screen, (16, 14, 20), champ, 0, 8)
    pygame.draw.rect(screen, (172, 150, 96), champ, 2, 8)
    aff = valeur + ("|" if (tick // 16) % 2 == 0 else "")
    t = _f_nom.render(aff, True, (238, 230, 210))
    prev = screen.get_clip(); screen.set_clip(champ.inflate(-24, -8))
    screen.blit(t, t.get_rect(midleft=(champ.x + 22, champ.centery)))
    screen.set_clip(prev)
    return champ


def _ecran_message(titre, ligne, ms=1700):
    """Petit ecran d'info (erreur / deconnexion), affiche ~ms millisecondes."""
    t = 0
    while t < ms / (1000 / FPS):
        clock.tick(FPS); t += 1
        for e in pygame.event.get():
            if e.type == pygame.QUIT or e.type == pygame.MOUSEBUTTONDOWN \
               or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                return
        p = _panneau_multi(titre, taille=(760, 360), tick=t)
        m = _f_corps.render(ligne, True, (220, 208, 186))
        screen.blit(m, m.get_rect(center=(p.centerx, p.centery + 6)))
        dessiner_curseur(screen); pygame.display.flip()


def menu_multijoueur():
    """Choix Heberger / Rejoindre (+ profil de TOUCHES du joueur local). Renvoie 'host', 'join'
    ou None (retour menu). Le selecteur de touches : le joueur LOCAL (hote OU client) pilote son
    perso avec le profil Joueur 1 ou Joueur 2 (configurables dans Options > Keybinds)."""
    p = pygame.Rect(0, 0, 820, 500); p.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    b_host = Button(p.centerx - 300, p.centery - 92, "Host a game", 290, 104)
    b_join = Button(p.centerx + 10, p.centery - 92, "Join a game", 290, 104)

    def _btn_touches():                                   # (re)construit le bouton avec son libelle
        prof = "2" if SETTINGS.get("touches_lan") == "Right" else "1"
        return Button(p.centerx - 235, p.centery + 58, "Your keys :  Player %s" % prof, 470, 58)
    b_touches = _btn_touches()
    b_back = Button(p.centerx - 110, p.bottom - 78, "Back", 220, 54)
    tick = 0
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False; tick += 1
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1: clic = True
        _panneau_multi("LAN Multiplayer", "Same Wi-Fi, or a LAN VPN (Hamachi, Radmin...)", tick=tick)
        for b in (b_host, b_join, b_touches, b_back): b.check_hover(mouse); b.draw(screen)
        hint = _f_hint.render("The keys that control your fighter (set them in Options > Keybinds)",
                              True, (168, 160, 144))
        screen.blit(hint, hint.get_rect(center=(p.centerx, p.centery + 134)))
        if b_touches.is_clicked(mouse, clic):             # bascule Left <-> Right + sauvegarde
            SETTINGS["touches_lan"] = "Left" if SETTINGS.get("touches_lan") == "Right" else "Right"
            sauver_settings()
            b_touches = _btn_touches()
        if b_host.is_clicked(mouse, clic): return "host"
        if b_join.is_clicked(mouse, clic): return "join"
        if b_back.is_clicked(mouse, clic): return None
        disperser_si_besoin()   # dissipe la brume de transition en arrivant sur le menu multi
        dessiner_curseur(screen); pygame.display.flip()


def ecran_nom_session(defaut):
    """Saisie du nom de la session (cote hote). -> nom (str) ou None (annule)."""
    p = pygame.Rect(0, 0, 820, 470); p.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    b_ok = Button(p.centerx - 255, p.bottom - 92, "Open to LAN", 245, 60)
    b_back = Button(p.centerx + 15, p.bottom - 92, "Back", 240, 60)
    nom = defaut; tick = 0
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False; entree = False; tick += 1
        for e in pygame.event.get():
            if e.type == pygame.QUIT: return None
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE: return None
                elif e.key == pygame.K_BACKSPACE: nom = nom[:-1]
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER): entree = True
                elif e.unicode and e.unicode.isprintable() and len(nom) < 24: nom += e.unicode
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1: clic = True
        _panneau_multi("Host a game", tick=tick)
        _champ_saisie(p, "Session name", nom, tick)
        b_ok.check_hover(mouse); b_back.check_hover(mouse); b_ok.draw(screen); b_back.draw(screen)
        if b_back.is_clicked(mouse, clic): return None
        if (entree or b_ok.is_clicked(mouse, clic)) and nom.strip():
            return nom.strip()
        dessiner_curseur(screen); pygame.display.flip()


def ecran_hote(nom):
    """Heberge : DIFFUSE la session sur le LAN (nom) et attend une connexion."""
    try:
        hote = reseau.Hote()
    except Exception:
        _ecran_message("Hosting failed", "Could not open the network port.")
        return None
    ip = reseau.ip_locale()
    ann = reseau.Annonceur(nom, ip_locale_=ip)
    p = pygame.Rect(0, 0, 820, 530); p.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    b_back = Button(p.centerx - 110, p.bottom - 84, "Cancel", 220, 58)
    tick = 0
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False; tick += 1
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                ann.fermer(); hote.fermer(); return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1: clic = True
        ann.diffuser()
        session = hote.accepter()
        if session is not None:
            ann.fermer(); hote.fermer(); return session
        _panneau_multi("Hosting", taille=(820, 530), tick=tick)
        nm = _f_nom.render(nom, True, GOLD)
        prev = screen.get_clip(); screen.set_clip(p.inflate(-80, 0))
        screen.blit(nm, nm.get_rect(center=(p.centerx, p.centery - 78)))
        screen.set_clip(prev)
        l2 = _f_corps.render("Visible on your network", True, (206, 196, 174))
        screen.blit(l2, l2.get_rect(center=(p.centerx, p.centery - 22)))
        l3 = _f_corps.render("Waiting for a player" + "." * ((tick // 18) % 4), True, (170, 162, 146))
        screen.blit(l3, l3.get_rect(center=(p.centerx, p.centery + 18)))
        # separateur + bloc code/IP en police CLASSIQUE (lisible) bien au-dessus du bouton
        pygame.draw.line(screen, (96, 88, 74), (p.x + 130, p.centery + 56),
                         (p.right - 130, p.centery + 56), 1)
        lab = _f_hint.render("Elsewhere on the network, join with:", True, (150, 142, 126))
        screen.blit(lab, lab.get_rect(center=(p.centerx, p.centery + 82)))
        l4 = _f_sys.render("Code  " + reseau.code_session(ip) + "       IP  " + ip, True, (226, 212, 172))
        screen.blit(l4, l4.get_rect(center=(p.centerx, p.centery + 118)))
        b_back.check_hover(mouse); b_back.draw(screen)
        if b_back.is_clicked(mouse, clic): ann.fermer(); hote.fermer(); return None
        dessiner_curseur(screen); pygame.display.flip()


def ecran_rejoindre():
    """Liste les sessions du LAN (decouverte UDP) ; clic = rejoindre. + saisie manuelle."""
    try:
        scan = reseau.ScannerLAN()
    except Exception:
        scan = None
    p = pygame.Rect(0, 0, 860, 580); p.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    b_man = Button(p.centerx - 260, p.bottom - 84, "Enter IP / Code", 255, 58)
    b_back = Button(p.centerx + 20, p.bottom - 84, "Back", 240, 58)
    msg = ""; tick = 0
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False; tick += 1
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                if scan: scan.fermer()
                return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1: clic = True
        sessions = scan.scanner() if scan else []
        _panneau_multi("Join a game", "Games on your network", (860, 580), tick)
        lignes = []
        y = p.top + 156
        for (snom, sip, sport) in sessions[:5]:
            r = pygame.Rect(p.x + 56, y, p.width - 112, 60)
            survol = r.collidepoint(mouse)
            pygame.draw.rect(screen, (15, 14, 12), r.move(0, 3), 0, 8)
            pygame.draw.rect(screen, (96, 90, 76) if survol else (58, 54, 46), r, 0, 8)
            pygame.draw.rect(screen, (208, 132, 56) if survol else (120, 112, 98), r, 2, 8)
            pygame.draw.circle(screen, (120, 200, 110), (r.x + 30, r.centery), 8)   # pastille "en ligne"
            prev = screen.get_clip(); screen.set_clip(r.inflate(-90, 0))
            nm = _f_corps.render(snom, True, (238, 228, 208))
            screen.blit(nm, nm.get_rect(midleft=(r.x + 56, r.centery)))
            screen.set_clip(prev)
            if survol:
                jn = _f_hint.render("Join", True, (210, 170, 70))
                screen.blit(jn, jn.get_rect(midright=(r.right - 22, r.centery)))
            lignes.append((r, sip, sport))
            y += 70
        if not sessions:
            s = _f_corps.render("Searching for games" + "." * ((tick // 18) % 4), True, (158, 150, 136))
            screen.blit(s, s.get_rect(center=(p.centerx, p.top + 230)))
        if msg:
            m = _f_hint.render(msg, True, (222, 120, 95))
            screen.blit(m, m.get_rect(center=(p.centerx, p.bottom - 118)))
        b_man.check_hover(mouse); b_back.check_hover(mouse); b_man.draw(screen); b_back.draw(screen)
        if clic:
            for r, sip, sport in lignes:
                if r.collidepoint(mouse):
                    if scan: scan.fermer()
                    p2 = _panneau_multi("Join a game", tick=tick)
                    cm = _f_nom.render("Connecting...", True, GOLD)
                    screen.blit(cm, cm.get_rect(center=(p2.centerx, p2.centery)))
                    dessiner_curseur(screen); pygame.display.flip()
                    sess = reseau.rejoindre(sip, sport)
                    if sess is not None:
                        return sess
                    try: scan = reseau.ScannerLAN()
                    except Exception: scan = None
                    msg = "Could not connect - on the HOST PC, run 'Autoriser le multijoueur.bat' once."
                    break
        if b_man.is_clicked(mouse, clic):
            if scan: scan.fermer()
            return ecran_rejoindre_manuel()
        if b_back.is_clicked(mouse, clic):
            if scan: scan.fermer()
            return None
        dessiner_curseur(screen); pygame.display.flip()


def ecran_rejoindre_manuel():
    """Fallback : saisie d'un code de session OU d'une IP. -> SessionReseau ou None."""
    p = pygame.Rect(0, 0, 820, 470); p.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    b_conn = Button(p.centerx - 255, p.bottom - 92, "Connect", 245, 60)
    b_back = Button(p.centerx + 15, p.bottom - 92, "Back", 240, 60)
    saisie = ""; msg = ""; tick = 0
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False; entree = False; tick += 1
        for e in pygame.event.get():
            if e.type == pygame.QUIT: return None
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE: return None
                elif e.key == pygame.K_BACKSPACE: saisie = saisie[:-1]
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER): entree = True
                elif (e.unicode in "0123456789.-" or e.unicode.isalpha()) and len(saisie) < 21:
                    saisie += e.unicode.upper()
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1: clic = True
        _panneau_multi("Join a game", tick=tick)
        _champ_saisie(p, "Session code  or  IP address", saisie, tick)
        if msg:
            m = _f_hint.render(msg, True, (222, 120, 95))
            screen.blit(m, m.get_rect(center=(p.centerx, p.centery + 78)))
        b_conn.check_hover(mouse); b_back.check_hover(mouse); b_conn.draw(screen); b_back.draw(screen)
        if b_back.is_clicked(mouse, clic): return None
        if (entree or b_conn.is_clicked(mouse, clic)) and saisie.strip():
            cible = saisie.strip()
            ip = cible if "." in cible else reseau.ip_depuis_code(cible)
            if ip is None:
                msg = "Invalid code."
            else:
                p2 = _panneau_multi("Join a game", tick=tick)
                cm = _f_nom.render("Connecting to " + ip, True, GOLD)
                screen.blit(cm, cm.get_rect(center=(p2.centerx, p2.centery)))
                dessiner_curseur(screen); pygame.display.flip()
                session = reseau.rejoindre(ip)
                if session is not None:
                    return session
                msg = "Connection failed (check the code/IP & firewall)."
        dessiner_curseur(screen); pygame.display.flip()


def selection_hote(perso1, perso2):
    """Hote : choisit les 2 persos + la map (ecrans existants). -> (p1, p2, map_id) ou None."""
    venant_map = False
    while True:
        etat, perso1, perso2 = choose(perso1, perso2, presel=venant_map)
        if etat != SELECTION_MAP:
            return None                                   # back/quit -> annule
        etat2, perso1, perso2 = choose_map(perso1, perso2)
        if etat2 == JEU:
            return (perso1, perso2, MAP_ACTUELLE)         # choose_map a regle MAP_ACTUELLE
        if etat2 == SELECTION:
            venant_map = True                             # back -> on garde les persos
            continue
        return None


def ecran_attente_selection(session):
    """Client : attend la selection (persos+map) de l'hote. -> (p1, p2, map_id) ou None."""
    p = pygame.Rect(0, 0, 820, 470); p.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
    b_back = Button(p.centerx - 110, p.bottom - 86, "Cancel", 220, 58)
    tick = 0
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False; tick += 1
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1: clic = True
        ligne = session.lire_ligne_dispo()
        if ligne is not None:
            parts = ligne.split("|")
            return tuple(parts) if len(parts) == 3 else None
        if not session.alive:
            return None
        _panneau_multi("Connected !", tick=tick)
        l1 = _f_corps.render("The host is picking the fighters", True, (206, 196, 174))
        screen.blit(l1, l1.get_rect(center=(p.centerx, p.centery - 16)))
        l2 = _f_corps.render("Please wait" + "." * ((tick // 18) % 4), True, (170, 162, 146))
        screen.blit(l2, l2.get_rect(center=(p.centerx, p.centery + 34)))
        b_back.check_hover(mouse); b_back.draw(screen)
        if b_back.is_clicked(mouse, clic): return None
        dessiner_curseur(screen); pygame.display.flip()


def ecran_fin_reseau(session, perso1, perso2, gagnant):
    """Fin de match RESEAU : annonce le vainqueur + VOTE de REMATCH en LOCKSTEP. Le vote passe par
    le CANAL BINAIRE d'inputs (move1=vote emis, move2=vote=OUI) -> aucun melange texte/binaire, et
    les 2 cotes resolvent a la MEME frame (lockstep) donc restent synchro pour le match suivant.
    Renvoie True si LES DEUX veulent rejouer, False sinon (menu / deconnexion)."""
    je_gagne = ((gagnant == 0) == (session.mon_cote == "Left"))
    cx = SCREEN_WIDTH // 2
    b_rematch = Button(cx - 300, 626, "Rematch", 290, 92)
    b_quit = Button(cx + 10, 626, "Quit to Menu", 290, 92)
    try:                                              # fond fige = map floutee (comme la victoire locale)
        _fond = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        _fond.blit(animation_frames[0], (0, 0)); _fond.blit(PROPS_SCALED, (0, 0))
        fond = apply_blur(_fond)
    except Exception:
        fond = apply_blur(screen.copy())
    titre = "You Win !" if je_gagne else "Defeat"
    coul = GOLD if je_gagne else (206, 96, 96)
    mon_vote = None                                   # None (indecis) / True (rematch) / False (menu)
    peer_vote = None
    drain = session.delay + 5                         # frames a ignorer (vidange des inputs de fin de match)
    fr = 0
    while True:
        clock.tick(FPS); fr += 1
        clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                mon_vote = False
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        mouse = pygame.mouse.get_pos()
        if mon_vote is None:
            if b_rematch.is_clicked(mouse, clic):
                mon_vote = True
            elif b_quit.is_clicked(mouse, clic):
                mon_vote = False
        # --- ECHANGE LOCKSTEP du vote ---
        vote_inp = classes.Inputs(move1=(mon_vote is not None), move2=(mon_vote is True))
        l, r = session.echanger(vote_inp)
        if not session.alive:
            return False                              # deconnexion pendant le vote
        peer = r if session.mon_cote == "Left" else l
        if fr > drain and peer.move1:
            peer_vote = bool(peer.move2)
        if fr > drain and mon_vote is not None and peer_vote is not None:
            return bool(mon_vote and peer_vote)       # rematch seulement si LES DEUX disent OUI
        # --- RENDU ---
        screen.blit(fond, (0, 0))
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); ov.set_alpha(150); ov.fill(NOIR)
        screen.blit(ov, (0, 0))
        titre_parchemin(screen, titre, font_title, (cx, 150), coul)
        if peer_vote is None:
            stat, scol = "Opponent is deciding...", (180, 172, 154)
        elif peer_vote:
            stat, scol = "Opponent wants a rematch !", (140, 210, 130)
        else:
            stat, scol = "Opponent left to the menu", (214, 150, 90)
        s = _f_corps.render(stat, True, scol)
        screen.blit(s, s.get_rect(center=(cx, 470)))
        if mon_vote is None:
            b_rematch.check_hover(mouse); b_quit.check_hover(mouse)
            b_rematch.draw(screen); b_quit.draw(screen)
        else:
            w = _f_corps.render("Rematch - waiting for opponent..." if mon_vote else "Leaving...",
                                True, (212, 202, 180))
            screen.blit(w, w.get_rect(center=(cx, 668)))
        indicateur_reseau(session)
        dessiner_curseur(screen)
        pygame.display.flip()


def flux_multijoueur(perso1, perso2):
    """Flux LAN complet : menu -> host/join -> selection -> combat reseau -> menu."""
    global MAP_ACTUELLE
    while True:
        choix = menu_multijoueur()
        if choix is None:
            return MENU_ACCUEIL                      # Back du menu multi -> accueil
        session = None
        if choix == "host":
            nom_defaut = (os.environ.get("USERNAME") or "Player") + "'s game"
            nom = ecran_nom_session(nom_defaut)
            if nom is None:
                continue                             # Cancel -> retour menu multi
            session = ecran_hote(nom)
            if session is None:
                continue                             # Cancel -> retour menu multi
            sel = selection_hote(perso1, perso2)
            if sel is None:
                session.fermer(); continue
            p1, p2, mapid = sel
            session.envoyer_ligne("%s|%s|%s" % (p1, p2, mapid))
        else:  # join
            session = ecran_rejoindre()
            if session is None:
                continue                             # Cancel -> retour menu multi
            sel = ecran_attente_selection(session)
            if sel is None:
                session.fermer(); continue
            p1, p2, mapid = sel
        MAP_ACTUELLE = mapid if mapid in MAPS else "chapel"

        # MATCHS RESEAU : on rejoue tant que LES DEUX veulent un rematch (memes persos + map).
        montrer_deco = False
        while session.alive:
            resultat = jeu(p1, p2, net=session)
            if isinstance(resultat, tuple) and resultat[0] == "quitter":
                session.fermer(); return ("quitter", perso1, perso2)   # fenetre fermee
            if isinstance(resultat, tuple) and resultat[0] == "reseau_desync":
                _ecran_message("Desync", "The two games diverged - match ended.")
                break                                                  # divergence deterministe (bug)
            if isinstance(resultat, tuple) and resultat[0] == "reseau_perdu":
                montrer_deco = True; break                             # le pair a lache
            if isinstance(resultat, tuple) and resultat[0] == "reseau_quitte":
                break                                                  # j'ai quitte (ESC) -> menu, sans message
            # ("match_fini", gagnant) -> ecran de fin + vote de rematch en lockstep
            gagnant = resultat[1] if isinstance(resultat, tuple) and len(resultat) > 1 else 0
            if not ecran_fin_reseau(session, p1, p2, gagnant):
                montrer_deco = not session.alive                       # False = pas de rematch (ou deco pendant le vote)
                break
            # rematch accepte des 2 cotes -> on reboucle (jeu() recree scores/rounds)
        if montrer_deco:
            _ecran_message("Disconnected", "The match ended (connection closed).")
        session.fermer()
        return MENU_ACCUEIL                          # apres le match -> accueil


# --- COMBOS a entrainer, par perso (seuls ceux qui ont des enchainements precis) ---
# "seq" = suite des BOUTONS a presser (M1/M2). Le guide visuel affiche la suite + le timing.
def _seq_combo_config(cls):
    """Derive la sequence de boutons (M1/M2...) du combo config["combo"] d'un perso
    config-driven (Fighter2) -- SOURCE UNIQUE : si la chaine change (rework/equilibrage),
    le Combo Trainer suit automatiquement. None si le perso n'a pas de combo config."""
    combo = (getattr(cls, "CONFIG", None) or {}).get("combo")
    if not combo:
        return None
    btn_depart = {atk: btn for btn, atk in combo.get("base", {}).items()}
    act = combo.get("depart")
    b0 = btn_depart.get(act)
    if not act or not b0:
        return None
    LB = {"move1": "M1", "move2": "M2"}
    seq = [LB.get(b0, b0)]
    vus = {act}
    while act in combo.get("chaines", {}):
        btn, act = combo["chaines"][act]
        seq.append(LB.get(btn, btn))
        if act in vus:                      # garde-fou anti-boucle (config erronee)
            break
        vus.add(act)
    return seq


COMBOS_TRAIN = {
    # Oswald/Barrion : sequence DERIVEE du config["combo"] du perso (source unique).
    "Oswald":  [{"nom": "Radiant Chain",    "seq": _seq_combo_config(Oswald) or ["M1", "M2", "M2"],
                 "note": "Frame-perfect - press on the impact"}],
    "Barrion": [{"nom": "Knight's Combo",   "seq": _seq_combo_config(Barrion) or ["M1", "M2", "M1"],
                 "note": "Frame-perfect - press on the impact"}],
    # Stormr/Arinya : combos codes dans leur classe (pas config-driven) -> sequence locale.
    "Stormr":  [{"nom": "Double Strike",    "seq": ["M1", "M2"],        "note": "Basic chain"},
                {"nom": "Thunder Finisher", "seq": ["M1", "M2", "M1"],  "note": "Charge the target full, then finish",
                 "success": "lightning"}],
    "Arinya":  [{"nom": "Spear Chain",      "seq": ["M1", "M1"],        "note": "Press M1 again right after the first hit"}],
}
_f_combo_title = pygame.font.SysFont("georgia,serif", 26, bold=True)
_f_combo_badge = pygame.font.SysFont("consolas,arial", 30, bold=True)
_f_combo_note = pygame.font.SysFont("georgia,serif", 22, italic=True)   # note du Moves Guide


def _badge_touches(txt):
    """Traduit les jetons M1/M2/UP/<< / >> d'un badge du guide vers les VRAIES
    touches du joueur (profil 'Left' = celui du training). Lu au RENDU ->
    toujours a jour, meme apres un remap dans Options > Keybinds."""
    rep = {"M1": "move1", "M2": "move2", "UP": "up", "<<": "left", ">>": "right"}
    mots = []
    for mot in txt.split():
        action = rep.get(mot)
        mots.append(nom_touche(KEYBINDS["Left"].get(action)) if action else mot)
    return "  ".join(mots)


def _combo_timing(joueur):
    """Fenetre d'enchainement REELLE du coup en cours, lue DIRECTEMENT dans la logique du
    perso (methode combo_timing de sa classe = SOURCE UNIQUE, les memes valeurs que le code
    d'enchainement) -> le trainer s'adapte AUTOMATIQUEMENT a tout reequilibrage des fenetres.
    (total, debut, fin EXCLUSIVE, position) ou None."""
    fn = getattr(joueur, "combo_timing", None)
    return fn() if callable(fn) else None


def _profondeur_combo(joueur):
    """Profondeur de chaine du perso (nb de coups enchaines). Fighter2/Stormr -> combo_step ;
    Arinya (pas de combo_step) -> attack_type pendant une attaque (1er coup=1, 2e=2)."""
    cs = getattr(joueur, "combo_step", None)
    if cs is not None:
        return cs
    if getattr(joueur, "attacking", False):
        return getattr(joueur, "attack_type", 0)
    return 0


def _attaque_ordinale(joueur):
    """Rang (1-base) de l'attaque EN COURS dans la chaine, ou 0 si pas en attaque. Sert a
    masquer la barre de timing sur la DERNIERE attaque (plus rien a enchainer).
    Fighter2/Arinya -> attack_type ; Stormr -> attack_action (ATTACK1/2/3)."""
    if not getattr(joueur, "attacking", False):
        return 0
    at = getattr(joueur, "attack_type", 0)          # Fighter2 (1/2/3), Arinya (1/2)
    if at:
        return at
    aa = getattr(joueur, "attack_action", None)     # Stormr : ATTACK1/2/3
    for k in (1, 2, 3):
        if aa is not None and aa == getattr(joueur, "ATTACK%d" % k, None):
            return k
    return 0


def _combo_reussi(joueur, combo, degats):
    """True quand le DERNIER coup du combo choisi vient de TOUCHER le mannequin (pas juste
    l'initiation) -> le mannequin ne tombe que quand l'attaque connecte vraiment. 'success' :
    'lightning' = la foudre de Stormr inflige ses degats ; defaut = dernier coup (profondeur ==
    longueur du combo) qui inflige des degats."""
    if degats <= 0:
        return False
    if combo.get("success") == "lightning":
        return getattr(joueur, "lightning", None) is not None
    return _profondeur_combo(joueur) >= len(combo["seq"])


def dessiner_guide_combo(surface, joueur, combo, tick, succes=False):
    """Aide visuelle du combo en cours (bas de l'ecran) : suite de boutons M1/M2 (fait=vert,
    a presser=or pulsant, a venir=gris) + barre de TIMING (curseur a caler dans la zone verte)."""
    seq = [_badge_touches(str(b)) for b in combo["seq"]]   # badges -> touches REELLES du joueur
    est_move = "detect" in combo   # entree du Moves Guide (validation par detect, pas par profondeur)
    if est_move:
        # Progression du MOVE : fonction "prog" de classes.MOVES_GUIDE (0..len(seq)),
        # lue chaque frame -> les badges bleuissent au fil du move (comme les combos).
        pr = combo.get("prog")
        try:
            cs = max(0, min(len(seq), int(pr(joueur)))) if pr else 0
        except Exception:
            cs = 0
    else:
        cs = max(0, min(_profondeur_combo(joueur), len(seq)))
    if succes:
        cs = len(seq)              # flash de reussite : tous les badges valides (et VERTS)
    BH, BGAP = 66, 22
    # Separateur entre badges : fleche = ENCHAINEMENT (defaut) ; texte "sep" (ex "or")
    # = ALTERNATIVE (spin/dash : gauche OU droite) -> tous les badges s'allument pareil.
    sep = combo.get("sep")
    sep_img = _f_combo_badge.render(sep, True, (185, 175, 152)) if sep else None
    if sep_img:
        BGAP = max(BGAP, sep_img.get_width() + 20)
    largeurs = [max(66, _f_combo_badge.size(str(b))[0] + 26) for b in seq]   # badges a largeur variable
    total_w = sum(largeurs) + (len(seq) - 1) * BGAP
    note = combo.get("note")
    PANEL_W = max(total_w + 96, 440,
                  (_f_combo_note.size(note)[0] + 84) if note else 0)   # la note tient TOUJOURS dedans (marge rivets)
    PANEL_H = 176 if est_move else 216      # move : pas de barre de timing -> panneau plus court
    px = SCREEN_WIDTH // 2 - PANEL_W // 2; py = 92   # EN HAUT, sous les barres de vie + l'indication
    plaque_metal(surface, pygame.Rect(px, py, PANEL_W, PANEL_H))   # panneau plaque de fer
    tt = _f_combo_title.render(combo["nom"], True, (38, 30, 20))
    surface.blit(tt, tt.get_rect(center=(SCREEN_WIDTH // 2, py + 26)))
    # Badges des boutons
    bx = SCREEN_WIDTH // 2 - total_w // 2; by = py + 50
    for i, b in enumerate(seq):
        r = pygame.Rect(bx, by, largeurs[i], BH)
        bx += largeurs[i] + BGAP
        if succes:
            fill, edge = (28, 96, 52), (120, 235, 150)          # reussite -> tout VERT (avec le Success !)
        elif i < cs:
            fill, edge = (24, 54, 92), (90, 150, 230)           # deja enchaine -> BLEU (le vert = "presser")
        elif i == cs or sep:                                    # a presser (ou ALTERNATIVE : tous pareils)
            p = 0.5 + 0.5 * math.sin(tick * 0.2)                # -> pulse or
            fill = (int(72 + 46 * p), int(52 + 38 * p), 14); edge = (255, int(198 + 42 * p), 72)
        else:
            fill, edge = (34, 32, 40), (108, 102, 90)           # a venir
        pygame.draw.rect(surface, fill, r, 0, 12)
        pygame.draw.rect(surface, edge, r, 3, 12)
        lab = _f_combo_badge.render(b, True, (240, 232, 214))
        surface.blit(lab, lab.get_rect(center=r.center))
        if i < len(seq) - 1:
            ax = r.right + BGAP // 2
            if sep_img:                                         # alternative : "or" entre les badges
                surface.blit(sep_img, sep_img.get_rect(center=(ax, r.centery)))
            else:                                               # enchainement : fleche "puis"
                pygame.draw.polygon(surface, (150, 142, 120),
                                    [(ax - 5, r.centery - 7), (ax + 5, r.centery), (ax - 5, r.centery + 7)])
    # Barre de timing : le curseur balaie de gauche a droite ; presser quand il atteint la
    # ZONE VERTE (la fenetre d'enchainement du coup en cours). Le curseur vire au vert vif dedans.
    # Pas de barre sur la DERNIERE attaque du combo (rien a enchainer apres).
    tim = None if est_move else (_combo_timing(joueur) if _attaque_ordinale(joueur) < len(seq) else None)
    bary = by + BH + 18
    if tim:
        total, ws, we, now = tim
        BARW = max(total_w, 320); barx = SCREEN_WIDTH // 2 - BARW // 2; BARH = 16
        # piste ROUGE = appui INVALIDE (trop tot / trop tard) ; zone VERTE = la fenetre REELLE,
        # PROPORTIONNELLE a l'intervalle lu dans le code du perso (fin EXCLUSIVE). Apres la
        # fenetre, tout est rouge : appuyer ne declenche plus rien.
        pygame.draw.rect(surface, (118, 38, 34), (barx, bary, BARW, BARH), 0, 5)     # piste (rouge)
        pygame.draw.rect(surface, (12, 10, 8), (barx, bary, BARW, BARH), 2, 5)
        gx = barx + int(BARW * ws / total)
        gw = max(6, int(BARW * (we - ws) / total))                                   # proportionnel
        gx = min(gx, barx + BARW - gw)
        dans = ws <= now < we
        pygame.draw.rect(surface, (95, 235, 125) if dans else (46, 120, 74), (gx, bary, gw, BARH), 0, 4)
        pygame.draw.rect(surface, (200, 255, 210), (gx, bary, gw, BARH), 2, 4)       # liseré clair
        cxp = barx + int(BARW * min(now, total) / total)
        ccol = (120, 255, 150) if dans else (245, 240, 225)                          # curseur vert vif dans la fenetre
        pygame.draw.polygon(surface, ccol, [(cxp, bary - 3), (cxp - 7, bary - 13), (cxp + 7, bary - 13)])
        pygame.draw.rect(surface, ccol, (cxp - 2, bary, 4, BARH))
        lab = _f_dev.render("PRESS on green", True, (150, 60, 50) if not dans else (30, 120, 55))
        surface.blit(lab, lab.get_rect(midtop=(SCREEN_WIDTH // 2, bary + BARH + 3)))
    # Note pedagogique (une ligne, bas du panneau) : le "pourquoi/quand" du move ou du combo
    if note:
        nt = _f_combo_note.render(note, True, (56, 44, 30))
        surface.blit(nt, nt.get_rect(center=(SCREEN_WIDTH // 2, py + PANEL_H - 24)))
    else:
        nt = _f_dev.render(combo.get("note", ""), True, (58, 48, 34))
        surface.blit(nt, nt.get_rect(center=(SCREEN_WIDTH // 2, bary + 6)))


def _hp_perso(nom):
    """HP max d'un perso (pour regler la barre indicative du mannequin) -- SOURCE UNIQUE :
    CONFIG["health"] (persos config-driven) ou la constante de classe HEALTH (Konrad/Arinya/
    Stormr, la meme que leur __init__). Tout reequilibrage des PV se reflete automatiquement."""
    cls = PERSONNAGES.get(nom)
    cfg = getattr(cls, "CONFIG", None)
    if cfg and "health" in cfg:
        return int(cfg["health"])
    return int(getattr(cls, "HEALTH", 320))


def sous_menu_dummy(fond, hp_actuel, shield_actuel="off"):
    """Reglages du mannequin :
      - HP : clic sur un perso -> le mannequin prend ses HP de reference.
      - Shield : Off / Always block (garde tenue -> chip) / Block when hit (garde FRAICHE a
        l'impact -> RENVOIE le spin de Barrion). Le dummy est passif : sa garde est pilotee par
        la boucle d'entrainement (test du blocage / du renvoi).
    Renvoie ('hp', valeur), ('shield', mode) ou None (retour sans changer)."""
    ICON_PX, GAP = 84, 30
    noms = NOMS_PERSOS
    total = len(noms) * ICON_PX + (len(noms) - 1) * GAP
    x0, y = (SCREEN_WIDTH - total) // 2, 404
    cases = []
    for i, nom in enumerate(noms):
        r = pygame.Rect(x0 + i * (ICON_PX + GAP), y, ICON_PX, ICON_PX)
        ic = pygame.transform.scale(
            pygame.image.load(PERSONNAGES[nom].ICON).convert_alpha(), (ICON_PX, ICON_PX))
        cases.append((r, nom, ic, _hp_perso(nom)))
    sh_opts = [("Off", "off"), ("Always block", "always"), ("Block when hit", "on_hit")]
    SBW, SBH, SGAP = 250, 62, 24                          # boutons Shield (horizontaux)
    stot = len(sh_opts) * SBW + (len(sh_opts) - 1) * SGAP
    sx0, sy = (SCREEN_WIDTH - stot) // 2, 632
    sh_btns = [(Button(sx0 + i * (SBW + SGAP), sy, lab, SBW, SBH), mode)
               for i, (lab, mode) in enumerate(sh_opts)]
    b_back = Button(SCREEN_WIDTH // 2 - 110, 748, "Back", 220, 56)
    voile = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); voile.set_alpha(190); voile.fill(NOIR)
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        screen.blit(fond, (0, 0)); screen.blit(voile, (0, 0))
        titre_parchemin(screen, "Custom Dummy", font_title1, (SCREEN_WIDTH // 2, 292))
        lab = font_medium.render("HP", True, (232, 212, 150))
        screen.blit(lab, lab.get_rect(center=(SCREEN_WIDTH // 2, 366)))
        for r, nom, ic, hp in cases:
            survol = r.collidepoint(mouse)
            sel = int(hp) == int(hp_actuel)
            pygame.draw.rect(screen, (44, 42, 36), r.inflate(12, 12), 0, 8)
            screen.blit(ic, r.topleft)
            bord = GOLD if sel else ((228, 198, 120) if survol else (120, 112, 98))
            pygame.draw.rect(screen, bord, r.inflate(12, 12), 4 if sel else 3, 8)
            n = _f_dev.render(nom, True, (230, 220, 198))
            screen.blit(n, n.get_rect(midtop=(r.centerx, r.bottom + 12)))
            h = _f_dev.render("%d HP" % hp, True, (214, 184, 120))
            screen.blit(h, h.get_rect(midtop=(r.centerx, r.bottom + 34)))
            if survol and clic:
                return ("hp", hp)
        slab = font_medium.render("Shield", True, (232, 212, 150))
        screen.blit(slab, slab.get_rect(center=(SCREEN_WIDTH // 2, 596)))
        for b, mode in sh_btns:
            b.check_hover(mouse); b.draw(screen)
            if mode == shield_actuel:                     # lisere dore = mode actif
                pygame.draw.rect(screen, GOLD, b.rect.inflate(12, 12), 4, 10)
            if b.is_clicked(mouse, clic):
                return ("shield", mode)
        b_back.check_hover(mouse); b_back.draw(screen)
        if b_back.is_clicked(mouse, clic):
            return None
        dessiner_curseur(screen); pygame.display.flip()


def sous_menu_perso(fond):
    """Grille des icones des persos jouables (sous-menu 'Change character' de la pause).
    Renvoie le nom du perso choisi, ou None (retour)."""
    ICON_PX, GAP = 96, 26
    noms = NOMS_PERSOS
    total = len(noms) * ICON_PX + (len(noms) - 1) * GAP
    x0, y = (SCREEN_WIDTH - total) // 2, 430
    cases = []
    for i, nom in enumerate(noms):
        r = pygame.Rect(x0 + i * (ICON_PX + GAP), y, ICON_PX, ICON_PX)
        ic = pygame.transform.scale(       # scale (nearest) : icones 26x26 pixel-art -> net (pas floute)
            pygame.image.load(PERSONNAGES[nom].ICON).convert_alpha(), (ICON_PX, ICON_PX))
        cases.append((r, nom, ic))
    b_back = Button(SCREEN_WIDTH // 2 - 110, 600, "Back", 220, 56)
    voile = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); voile.set_alpha(190); voile.fill(NOIR)
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        screen.blit(fond, (0, 0)); screen.blit(voile, (0, 0))
        titre_parchemin(screen, "Change character", font_title1, (SCREEN_WIDTH // 2, 300))
        for r, nom, ic in cases:
            survol = r.collidepoint(mouse)
            pygame.draw.rect(screen, (44, 42, 36), r.inflate(12, 12), 0, 8)
            screen.blit(ic, r.topleft)
            pygame.draw.rect(screen, (228, 198, 120) if survol else (120, 112, 98), r.inflate(12, 12), 3, 8)
            lab = _f_dev.render(nom, True, (230, 220, 198))
            screen.blit(lab, lab.get_rect(midtop=(r.centerx, r.bottom + 16)))
            if survol and clic:
                jouer_sfx(SFX_CHOOSE_PERSO.get(nom), 0.9)     # son de validation du perso
                return nom
        b_back.check_hover(mouse); b_back.draw(screen)
        if b_back.is_clicked(mouse, clic):
            return None
        dessiner_curseur(screen); pygame.display.flip()


def pause_entrainement(perso, hp_actuel, shield_actuel="off"):
    """Pause epuree : Resume (primaire) + reglages en GRILLE 2 colonnes (Change Character /
    Custom Dummy / Combo Trainer si dispo / Options) + Main Menu. Le Combo Trainer ouvre un
    petit menu DEROULANT (Free training + combos), ancre sous son bouton. Renvoie 'resume',
    'menu', 'quit', un NOM de perso, ('combo', combo|None) ou ('hp', valeur)."""
    stop_sfx_combat()   # coupe les sons de combat qui trainent a l'ouverture de la pause
    flou = apply_blur(screen.copy())
    combos = COMBOS_TRAIN.get(perso, [])
    moves = classes.MOVES_GUIDE.get(perso, [])       # coups signatures (source : classes.py)
    cx = SCREEN_WIDTH // 2
    voile = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); voile.set_alpha(120); voile.fill(NOIR)
    b_resume = Button(cx - 230, 224, "Resume", 460, 92)              # action primaire
    sec = [("Change Character", "change"), ("Custom Dummy", "dummy")]
    if combos or moves:
        sec.append(("Moves Guide", "combo"))
    sec.append(("Options", "options"))
    GW, GH, GX, GY = 300, 66, 30, 18                                 # grille 2 colonnes
    x0 = cx - GW - GX // 2; y0 = 362
    grid = []
    for i, (lab, act) in enumerate(sec):
        seul = (i == len(sec) - 1) and (i % 2 == 0)                  # dernier ET seul sur sa ligne
        bx = (cx - GW // 2) if seul else x0 + (0 if i % 2 == 0 else GW + GX)   # -> centre
        by = y0 + (i // 2) * (GH + GY)
        grid.append((Button(bx, by, lab, GW, GH), act))
    ny = (len(sec) + 1) // 2
    b_menu = Button(cx - 160, y0 + ny * (GH + GY) + 26, "Main Menu", 320, 62)
    drop_items = ([("Free training", None)] + [(m["nom"], m) for m in moves]
                  + [(c["nom"], c) for c in combos])
    DROP_H = 50
    deroule = False
    while True:
        clock.tick(FPS); mouse = pygame.mouse.get_pos(); clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return "quit"
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                if deroule: deroule = False               # ferme le deroulant d'abord
                else: return "resume"
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        screen.blit(flou, (0, 0)); screen.blit(voile, (0, 0))
        titre_parchemin(screen, "Pause", font_title, (cx, 132))
        b_resume.check_hover(mouse); b_resume.draw(screen)
        combo_rect = None
        for b, act in grid:
            b.check_hover(mouse); b.draw(screen)
            if act == "combo":
                combo_rect = b.rect
        b_menu.check_hover(mouse); b_menu.draw(screen)
        # Petit menu deroulant du Combo Trainer (assombrit le reste, ancre sous son bouton)
        drop_rects = []
        if deroule and combo_rect:
            dim = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA); dim.fill((0, 0, 0, 150))
            screen.blit(dim, (0, 0))
            dessiner_bouton_metal(screen, combo_rect, "Moves Guide", True)   # au-dessus du voile
            dy = combo_rect.bottom + 8
            for label, cdata in drop_items:
                rr = pygame.Rect(combo_rect.x, dy, combo_rect.width, DROP_H)
                drop_rects.append((rr, cdata))
                dessiner_bouton_metal(screen, rr, label, rr.collidepoint(mouse), police=_f_dev)
                dy += DROP_H + 5
        # Clics
        if clic:
            if deroule:
                sur = next((cd for rr, cd in drop_rects if rr.collidepoint(mouse)), "__none__")
                if sur != "__none__":
                    return ("combo", sur)
                if not (combo_rect and combo_rect.collidepoint(mouse)):
                    deroule = False                        # clic ailleurs -> ferme
            else:
                if b_resume.is_clicked(mouse, clic): return "resume"
                if b_menu.is_clicked(mouse, clic):   return "menu"
                for b, act in grid:
                    if not b.is_clicked(mouse, clic):
                        continue
                    if act == "change":
                        choix = sous_menu_perso(flou)
                        if choix:
                            return choix
                    elif act == "dummy":
                        res = sous_menu_dummy(flou, hp_actuel, shield_actuel)
                        if res is not None:               # ('hp', valeur) ou ('shield', mode)
                            return res
                    elif act == "combo":
                        deroule = True                     # ouvre le petit deroulant
                    elif act == "options":
                        res = options(PAUSE, "Kenshi", "Lysandra", flou)
                        if res[0] == "quitter":
                            return "quit"
                    break
        dessiner_curseur(screen); pygame.display.flip()


def choose_perso_training(perso_actuel="Kenshi"):
    """Selection du combattant AVANT d'entrer dans le Temple d'entrainement (1 seul perso).
    Reprend le style de l'ecran de selection (fond dedie + brume, cadre metal, apercu anime,
    roster de tuiles). Renvoie le nom du perso choisi, None (retour) ou 'quitter'."""
    jouer_musique(MUSIQUE_MENU)
    sel = perso_actuel if perso_actuel in PERSONNAGES else NOMS_PERSOS[0]
    survole_prev = None
    FRAME_W, FRAME_H, FRAME_CY, FRAME_MARGE = 300, 452, 356, 16
    BASELINE = 560
    font_lore = pygame.font.Font("assets/fonts/OldLondon.ttf", 34)
    TILE, ICON_PX, gap = 100, 78, 30
    icones = {nom: pygame.transform.scale(
        pygame.image.load(PERSONNAGES[nom].ICON).convert_alpha(), (ICON_PX, ICON_PX))
        for nom in NOMS_PERSOS}
    total = TILE * len(NOMS_PERSOS) + gap * (len(NOMS_PERSOS) - 1)
    x = SCREEN_WIDTH // 2 - total // 2
    boutons = {}
    for nom in NOMS_PERSOS:
        boutons[nom] = Button(x, 690, nom, TILE, TILE); boutons[nom].muet = True
        x += TILE + gap
    button_back = Button(50, 806, "Back", 190, 62)                        # bas gauche
    button_go = Button(SCREEN_WIDTH - 360, 800, "Enter Temple", 300, 78)  # bas droite (SOUS le roster)
    tick = 0
    while True:
        clock.tick(FPS); tick += 1
        mouse = pygame.mouse.get_pos(); clic = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return "quitter"
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                return None
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                clic = True
        for b in boutons.values():
            b.check_hover(mouse)
        survole = next((n for n, b in boutons.items() if b.is_hovered), None)
        if survole is not None and survole != survole_prev:
            jouer_sfx(SFX_HOVER_PERSO, 0.8)
        survole_prev = survole
        affiche = survole or sel                          # apercu : survol prioritaire, sinon selection

        # Fond DEDIE au training (sinon celui du choose normal) + brume qui derive
        _bg = CHOOSE_TRAIN_BG or CHOOSE_BG
        if _bg is not None:
            screen.blit(_bg, (0, 0))
            off = int((tick * 0.45) % SCREEN_WIDTH); yb = int(math.sin(tick * 0.02) * 7)
            screen.blit(FOG_CHOOSE, (off - SCREEN_WIDTH, yb)); screen.blit(FOG_CHOOSE, (off, yb))
        else:
            draw_background_animation(0)
        ov = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)); ov.set_alpha(110); ov.fill(NOIR)
        screen.blit(ov, (0, 0))
        titre_parchemin(screen, "Training Temple", font_title1, (SCREEN_WIDTH // 2, 58))

        # Cadre metal central + combattant anime (clippe au cadre)
        cx = SCREEN_WIDTH // 2
        outer = pygame.Rect(0, 0, FRAME_W, FRAME_H); outer.center = (cx, FRAME_CY)
        plaque_metal(screen, outer)
        inner = outer.inflate(-2 * FRAME_MARGE, -2 * FRAME_MARGE)
        pygame.draw.rect(screen, (22, 20, 28), inner, 0, 6)
        frames = apercu_frames(affiche)
        img, fdx = frames[_apercu_idx(frames, tick)]
        prev_clip = screen.get_clip(); screen.set_clip(inner)
        sh = pygame.Surface((220, 44), pygame.SRCALPHA); pygame.draw.ellipse(sh, (0, 0, 0, 120), sh.get_rect())
        screen.blit(sh, sh.get_rect(center=(cx, BASELINE + 4)))
        screen.blit(img, img.get_rect(midbottom=(int(cx - fdx), BASELINE)))
        screen.set_clip(prev_clip)
        pygame.draw.rect(screen, (8, 7, 11), inner, 3, 6)
        pygame.draw.rect(screen, GOLD, inner, 2, 6)
        # Nom + titre lore
        nm = font_medium.render(affiche, True, (236, 224, 200))
        screen.blit(nm, nm.get_rect(center=(cx, 624)))
        lo = font_lore.render(NOMS_TITRES.get(affiche, ""), True, (214, 182, 92))
        screen.blit(lo, lo.get_rect(center=(cx, 668)))

        # Roster (tuiles acier + liseré or sur le perso choisi)
        for nom, b in boutons.items():
            r = b.rect
            if sel == nom:
                pygame.draw.rect(screen, GOLD, r.inflate(12, 12), 4, 6)
            base = (92, 86, 73) if b.is_hovered else (54, 50, 43)
            pygame.draw.rect(screen, (15, 14, 12), r.inflate(6, 6), 0, 6)
            pygame.draw.rect(screen, base, r, 0, 5)
            pygame.draw.rect(screen, (200, 124, 50) if b.is_hovered else (120, 112, 98), r, 2, 5)
            ic = icones[nom]; screen.blit(ic, ic.get_rect(center=r.center))
            if b.is_clicked(mouse, clic):
                sel = nom
                jouer_sfx(SFX_CHOOSE_PERSO.get(nom), 0.9)     # son de validation du perso

        button_back.check_hover(mouse); button_back.draw(screen)
        button_go.check_hover(mouse); button_go.draw(screen)
        if button_back.is_clicked(mouse, clic):
            return None
        if button_go.is_clicked(mouse, clic):
            return sel
        disperser_si_besoin()
        dessiner_curseur(screen)
        pygame.display.flip()


def intro_entrainement(joueur, dummy):
    """Arrivee du joueur dans le temple : il marche depuis la gauche jusqu'a sa place, puis
    son TITRE apparait (comme une partie normale, mais solo et sans decompte). Renvoie True
    si on doit quitter (fermeture fenetre)."""
    fin_x = joueur.rect.x
    start_x = -joueur.rect.width - 40
    joueur.title_hidden = True                          # le titre statique est masque pendant l'anim
    dest = joueur.title_dest()
    idle_i, run_i = _action_idx(joueur, "idle"), _action_idx(joueur, "run")
    WALK, NL, NDUR = 90, 12, 30
    fin = WALK + 26
    idx = bg_timer = t = 0
    while t < fin:
        clock.tick(FPS)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                joueur.title_hidden = False
                return True
            if e.type == pygame.KEYDOWN and e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_ESCAPE):
                t = fin                                 # une touche saute l'intro
        bg_timer += 1
        if bg_timer >= anim_speed:
            bg_timer -= anim_speed; idx = (idx + 1) % frame_nbr
        draw_background_animation(idx)
        if t < WALK:                                    # marche vers sa place
            p = t / WALK
            joueur.rect.x = int(start_x + (fin_x - start_x) * p)
            joueur.action = run_i
            joueur.frame_index = (t // 4) % len(joueur.animation_list[run_i])
        else:                                           # arrive -> idle
            joueur.rect.x = fin_x
            joueur.action = idle_i
            joueur.frame_index = ((t - WALK) // 6) % len(joueur.animation_list[idle_i])
        joueur.image = joueur.animation_list[joueur.action][joueur.frame_index]
        detecter_sons_combat(joueur, dummy)             # surtout les PAS de marche
        dummy.update(screen, joueur)
        dessiner_ombre(screen, joueur); dessiner_ombre(screen, dummy)
        joueur.draw(screen); dummy.draw(screen)
        classes.debug_draw(screen)
        draw_healthbar(joueur.max_health, joueur.health, joueur.health, 20, 20)
        draw_healthbar(320, 320, 320, SCREEN_WIDTH - 420, 20)   # mannequin : barre pleine
        screen.blit(PROPS_SCALED, (0, 0))
        ov = getattr(joueur, "draw_static_overlay", None)
        if ov:
            ov(screen)
        if t >= NL:                                     # apparition du titre (grand -> a sa place)
            if t < NL + NDUR:
                anim_titre_intro(joueur.title, dest, t - NL, NDUR)
            else:
                screen.blit(joueur.title, dest)
        disperser_si_besoin()
        pygame.display.flip()
        t += 1
    joueur.title_hidden = False
    return False


def jouer_entrainement(perso):
    """Mode ENTRAINEMENT : le joueur (touches Joueur 1) frappe un MANNEQUIN invincible sur le
    temple (JOUR). Enchainer 3 coups rapproches -> le mannequin joue sa mort puis reapparait.
    ESC = pause (avec changement de perso). Renvoie l'etat suivant (MENU_ACCUEIL / 'quitter')."""
    global animation_frames, frame_nbr, PROPS_SCALED, anim_speed, MAP_ECHELLE
    MAP = "temple_day"
    animation_frames = frames_de_map(MAP); frame_nbr = len(animation_frames)
    PROPS_SCALED = props_de_map(MAP)
    _dt = MAPS[MAP].get("frame_dt"); anim_speed = _dt * FPS if _dt else ANIMATION_SPEED
    set_filtre_nuit(False)                              # temple JOUR : pas de filtre nuit
    set_sol(MAPS[MAP].get("ground", 850))
    MAP_ECHELLE = MAPS[MAP].get("scale", 1.0)
    ambiance_menu(False)                               # coupe le VENT du menu (temple = ambiance de map)
    jouer_musique(MAPS[MAP].get("music", MUSIQUE_MENU))
    jouer_ambiance_map(MAP)
    discord_rp.maj("Training as %s" % perso, MAPS[MAP].get("label", ""),
                   grande="temple", grande_txt=MAPS[MAP].get("label", ""),
                   petite=perso.lower(), petite_txt=perso)
    particules.clear()

    def _creer(nom):
        f = PERSONNAGES[nom]("Left", False)
        mettre_a_echelle(f, MAP_ECHELLE)
        f.rect.bottom = classes.SOL
        _init_etats_combat(f)                            # sinon detecter_sons_combat plante
        f._nom = nom                                     # -> charge les sons du perso (swing/hit/voix)
        return f
    joueur = _creer(perso); joueur.rect.centerx = 470
    dummy = TrainingDummy("Right", True); mettre_a_echelle(dummy, MAP_ECHELLE)
    dummy.rect.bottom = classes.SOL; dummy.rect.centerx = 1120
    _init_etats_combat(dummy)

    if intro_entrainement(joueur, dummy):                # arrivee dans le temple (marche + titre)
        arreter_ambiance_map(); return "quitter"

    reset_horloge(); reset_horloge_active()
    idx = 0; anim_t = 0.0; tf = 0
    succes_timer = 0                                      # flash "Success !" du Moves Guide
    prev_mourant = False                                  # front de respawn -> vie pleine
    prev_jump = False
    prev_dash = False                                     # poussiere de dash (Arinya), cf. versus
    chute_jouee = True                                   # son de chute de la moitie haute (1x/mort)
    perso_courant = perso; combo_actif = None            # perso en cours + combo a entrainer (guide)
    # Barre de vie INDICATIVE du mannequin (BARRE_MAX = HP de reference, reglable dans la pause).
    BARRE_MAX = 320.0
    RESET_DELAY = 40                                      # battement (frames) apres la fin des coups -> reset
    hp_aff = trail_aff = BARRE_MAX; repos_timer = 0; bar_delay = 0   # barre de vie du mannequin
    fhud = pygame.font.SysFont("consolas,arial", 26, bold=True)
    fdeg = pygame.font.SysFont("consolas,arial", 38, bold=True)   # compteur de degats cumules
    deg_cumul = 0.0; deg_timer = 0                # coups rapproches -> UN nombre qui grossit
    shield_mode = "off"      # bouclier du mannequin : off / always / on_hit (menu Custom Dummy)

    while True:
        clock.tick(FPS)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                arreter_ambiance_map(); return "quitter"
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                r = pause_entrainement(perso_courant, BARRE_MAX, shield_mode)
                if r == "menu": arreter_ambiance_map(); return MENU_ACCUEIL
                if r == "quit": arreter_ambiance_map(); return "quitter"
                if isinstance(r, tuple) and r[0] == "combo":   # combo/move a entrainer (None = libre)
                    combo_actif = r[1]; succes_timer = 0
                    hp_aff = trail_aff = BARRE_MAX; repos_timer = 0
                elif isinstance(r, tuple) and r[0] == "shield":   # bouclier du mannequin
                    shield_mode = r[1]
                elif isinstance(r, tuple) and r[0] == "hp":    # HP custom du mannequin
                    BARRE_MAX = float(r[1])
                    hp_aff = trail_aff = BARRE_MAX; repos_timer = 0
                elif r in PERSONNAGES:                    # changement de perso -> meme position
                    perso_courant = r
                    cx = joueur.rect.centerx
                    joueur = _creer(r); joueur.rect.centerx = cx
                    succes_timer = 0
                    combo_actif = None                    # guide remis a zero au changement de perso
                    hp_aff = trail_aff = BARRE_MAX; repos_timer = 0
        # PAS de hitstop en entrainement : tout avance a chaque frame -> l'indicateur de combo
        # ne se fige jamais et aucun appui (ex : finisher de Stormr) n'est perdu.
        avancer_horloge(); avancer_horloge_active(); tf += 1
        anim_t += 1
        if anim_t >= anim_speed:
            anim_t -= anim_speed; idx = (idx + 1) % frame_nbr
        joueur.inputs = lire_inputs("Left")
        joueur.mvmt(screen, dummy); dummy.mvmt(screen, joueur)
        # BOUCLIER DU MANNEQUIN (menu Custom Dummy) : le dummy est passif (mvmt = no-op) donc on
        # pilote sa garde ici pour tester le blocage / le RENVOI du spin.
        if shield_mode != "off":
            # NB: on ne force PLUS le dummy a faire face au joueur -> il garde son orientation
            # (vers le joueur au depart) et on peut donc le SPIN DE DOS pour tester ce cas.
            dummy.block_health = 0                                     # bouclier tjr net (dummy = outil)
            if shield_mode == "always":
                dummy.block = True                                     # garde tenue -> chip
            else:                                                      # on_hit : garde quand un coup arrive
                dummy.block = joueur.attacking or getattr(joueur, "spinning", False)
        else:
            dummy.block = False
        # Age du blocage pour LES DEUX (avant : seul le joueur -> le dummy ne pouvait pas parer
        # le spin de Barrion car son block_age restait a 999). getattr = auto-init.
        for _f in (joueur, dummy):
            _prev = getattr(_f, "_block_prev", False)
            _f.block_age = 0 if (_f.block and not _prev) else \
                (getattr(_f, "block_age", 999) + 1 if _f.block else 999)
            _f._block_prev = _f.block
        if shield_mode == "on_hit" and dummy.block:
            dummy.block_age = 0                       # garde FRAICHE a chaque coup -> RENVOIE le spin
        # Le mannequin EXPOSE la barre du training comme ses VRAIS PV : les kits qui
        # lisent la vie de la cible (Execution de Kenshi sous 25%...) marchent aussi ici.
        # (Sans ca il restait a 1 000 000 PV internes -> Execution jamais declenchable.)
        dummy.max_health = BARRE_MAX
        dummy.health = max(1.0, hp_aff)
        hp_before = dummy.health
        joueur.update(screen, dummy)                      # peut toucher le mannequin
        degats = hp_before - dummy.health                 # avant que le dummy remette ses PV
        dummy.update(screen, joueur)                      # invincible : PV remis au max
        detecter_sons_combat(joueur, dummy)
        detecter_sons_combat(dummy, joueur)               # bouclier du dummy : sons + particules
        # Reussite du combo = son DERNIER coup TOUCHE (pas juste l'initiation).
        # Reussite : combo classique (profondeur + degats) OU move signature du Moves
        # Guide (fonction detect de classes.MOVES_GUIDE, front montant / degats selon le move).
        if combo_actif is None or dummy.mourant:
            reussi = False
        elif "detect" in combo_actif:
            reussi = bool(combo_actif["detect"](joueur, degats))
        else:
            reussi = _combo_reussi(joueur, combo_actif, degats)
        if degats > 0 and not dummy.mourant:                  # son de coup encaisse
            jouer_sfx(SONS_DUMMY["hit"], GAIN_COMBAT, cat="perso")
        # COMPTEUR DE DEGATS (sous la barre du mannequin) : les coups rapproches se
        # CUMULENT en un seul nombre ; il s'efface apres un court silence.
        if degats > 0 and not dummy.mourant:
            if deg_timer <= 0:
                deg_cumul = 0.0                   # nouvelle serie
            deg_cumul += degats
            deg_timer = 55                        # ~1,8 s, relancee a chaque coup
        elif deg_timer > 0:
            deg_timer -= 1
        # GUIDE (combo/move) : la reussite ne CASSE PLUS le mannequin (ca creait des
        # etats bizarres avec les moves multi-hit) -> flash "Success !" + petit son.
        if reussi and succes_timer <= 0:
            succes_timer = 55
            jouer_sfx_click()
        # La moitie haute brisee touche le sol (frame FALL_FRAME de l'anim de mort).
        if dummy.mourant and not chute_jouee and dummy.frame_index >= dummy.FALL_FRAME:
            jouer_sfx(SONS_DUMMY["fall"], GAIN_COMBAT, cat="perso")
            chute_jouee = True
        # --- Barre de vie du mannequin ---
        if prev_mourant and not dummy.mourant:            # il vient de se relever -> vie PLEINE
            hp_aff = trail_aff = BARRE_MAX; repos_timer = 0
        prev_mourant = dummy.mourant
        if combo_actif is None:
            # LIBRE : vraie barre de vie. A ZERO -> le mannequin se brise, puis
            # reapparait avec sa vie pleine (reset au respawn ci-dessus).
            if degats > 0 and not dummy.mourant:
                if trail_aff < hp_aff:
                    trail_aff = hp_aff
                hp_aff = max(0.0, hp_aff - degats)
                bar_delay = TRAIL_DELAY
                if hp_aff <= 0:
                    dummy.tuer()
                    jouer_sfx(SONS_DUMMY["break"], GAIN_COMBAT, cat="perso")
                    chute_jouee = False
            if trail_aff > hp_aff:                        # trail rouge qui rattrape
                if bar_delay > 0: bar_delay -= 1
                else: trail_aff = max(hp_aff, trail_aff - TRAIL_SPEED)
            else:
                trail_aff = hp_aff
        else:                                             # COMBO : vrais degats, RESET differe
            if degats > 0:
                if hp_aff >= BARRE_MAX:                    # nouveau cycle -> on repart de plein
                    trail_aff = BARRE_MAX
                hp_aff = max(BARRE_MAX * 0.12, hp_aff - degats)   # plancher : ne perd jamais TOUT
                bar_delay = TRAIL_DELAY
                repos_timer = RESET_DELAY                  # on tape -> pas de reset
            elif getattr(joueur, "attacking", False):
                repos_timer = RESET_DELAY                  # animation de combat en cours -> on garde l'affichage
            elif hp_aff < BARRE_MAX:                       # plus d'attaque : petit battement PUIS reset
                repos_timer -= 1                           #   -> le temps de constater les degats
                if repos_timer <= 0:
                    hp_aff = BARRE_MAX
            if trail_aff > hp_aff:                         # trail rouge qui rattrape (comme les persos)
                if bar_delay > 0: bar_delay -= 1
                else: trail_aff = max(hp_aff, trail_aff - TRAIL_SPEED)
            else:
                trail_aff = hp_aff
        jnow = joueur.jump                                # poussiere de saut (decollage/atterrissage)
        if jnow != prev_jump:
            fcx, fw = feet_info(joueur)
            spawn_particule("jump", fcx, classes.SOL, joueur.flip, fw)   # au SOL (cf. versus)
        prev_jump = jnow
        dnow = getattr(joueur, "dashing", False)          # poussiere de DASH (manquait en training :
        if dnow and not prev_dash:                        #   le bloc n'existait qu'en versus)
            fcx, fw = feet_info(joueur)
            spawn_particule("dash_air" if joueur.jump else "dash", fcx,
                            joueur.rect.bottom, joueur.flip, fw)
        prev_dash = dnow
        draw_background_animation(idx)
        dessiner_ombre(screen, joueur); dessiner_ombre(screen, dummy)
        joueur.draw(screen); dummy.draw(screen)
        classes.debug_draw(screen)                        # boites debug (menu Display > Debug)
        maj_particules(avance=True)
        screen.blit(PROPS_SCALED, (0, 0))     # calque AVANT : le perso passe DERRIERE les props
        overlay = getattr(joueur, "draw_static_overlay", None)   # effets electriques Stormr (jauge/etincelles/foudre)
        if overlay:
            overlay(screen)
        # Barres de vie : joueur (gauche) + mannequin (droite = INDICATEUR de degats)
        draw_healthbar(joueur.max_health, joueur.health, joueur.trail_health, 20, 20)
        _DX = SCREEN_WIDTH - 420
        draw_healthbar(BARRE_MAX, hp_aff, trail_aff, _DX, 20)             # vraie barre (les 2 modes)
        if deg_timer > 0 and deg_cumul > 0:       # degats cumules de la serie en cours
            _dt = fdeg.render("-%d" % round(deg_cumul), True, (255, 116, 84))
            _dt.set_alpha(min(255, deg_timer * 14))                       # fondu de sortie
            screen.blit(_dt, (_DX, 52))
        if shield_mode != "off":                          # rappel du mode bouclier du mannequin
            _sl = "Shield: %s" % ("Always block" if shield_mode == "always" else "Block when hit")
            _si = fhud.render(_sl, True, (150, 205, 255))
            screen.blit(_si, _si.get_rect(topright=(SCREEN_WIDTH - 24, 54)))
        hint = ("Follow the guide   -   ESC : pause" if combo_actif
                else "Deplete its bar to break the dummy   -   ESC : pause")
        info = fhud.render(hint, True, (238, 228, 205))
        screen.blit(info, info.get_rect(midtop=(SCREEN_WIDTH // 2, 58)))   # SOUS les barres de vie
        if combo_actif:                                   # aide visuelle du guide (sous l'indication)
            dessiner_guide_combo(screen, joueur, combo_actif, tf, succes=succes_timer > 0)
            if succes_timer > 0:                          # move/combo reussi -> flash sous le panneau
                succes_timer -= 1
                _p = 0.5 + 0.5 * math.sin(tf * 0.35)
                st = police_acier(52).render("Success !", True,
                                             (110 + int(70 * _p), 235, 130))
                so = police_acier(52).render("Success !", True, (12, 30, 14))
                r_st = st.get_rect(midtop=(SCREEN_WIDTH // 2, 322))
                screen.blit(so, (r_st.x + 2, r_st.y + 3))
                screen.blit(st, r_st)
        disperser_si_besoin()                            # dissipe la brume de transition a l'arrivee
        pygame.display.flip()                            # pas de curseur pendant le combat


def main():
    global BRUME_DISPERSE
    # Animation de lancement : logo du studio Ephyria (une seule fois au demarrage)
    if splash_studio() == "quitter":
        pygame.quit()
        return

    popup_raccourcis()   # propose un raccourci Bureau / Menu Demarrer (1x au lancement)

    etat_actuel = MENU_ACCUEIL
    run = True
    premier_lancement = True
    retour_vers = MENU_ACCUEIL
    
    personnage_joueur_1 = "Kenshi"
    personnage_joueur_2 = "Lysandra"
    
    while run:
        ancien_etat = etat_actuel
        if etat_actuel == MENU_ACCUEIL:
            resultat = menu_accueil(fade_in=premier_lancement, perso1=personnage_joueur_1, perso2=personnage_joueur_2)
            if len(resultat) == 4:
                etat_actuel, retour_vers, personnage_joueur_1, personnage_joueur_2 = resultat
            else:
                etat_actuel, personnage_joueur_1, personnage_joueur_2 = resultat
            premier_lancement = False
        elif etat_actuel == SELECTION:
            # presel = on revient du choix de map -> on garde les persos deja selectionnes
            resultat = choose(personnage_joueur_1, personnage_joueur_2,
                              presel=(ancien_etat == SELECTION_MAP))
            etat_actuel, personnage_joueur_1, personnage_joueur_2 = resultat
        elif etat_actuel == SELECTION_MAP:
            resultat = choose_map(personnage_joueur_1, personnage_joueur_2)
            etat_actuel, personnage_joueur_1, personnage_joueur_2 = resultat
        elif etat_actuel == BATTLE_MENU:
            resultat = menu_battle(personnage_joueur_1, personnage_joueur_2)
            etat_actuel, personnage_joueur_1, personnage_joueur_2 = resultat
        elif etat_actuel == TRAINING:
            perso = choose_perso_training(personnage_joueur_1)   # choix du perso d'abord
            if perso == "quitter":
                etat_actuel = "quitter"
            elif perso is None:                                  # Back -> retour au sous-menu
                etat_actuel = BATTLE_MENU
            else:
                personnage_joueur_1 = perso
                transition_couvrir(); BRUME_DISPERSE = True       # brume : choose -> temple
                etat_actuel = jouer_entrainement(perso)
        elif etat_actuel == MULTIJOUEUR:
            etat_actuel = flux_multijoueur(personnage_joueur_1, personnage_joueur_2)
        elif etat_actuel == JEU:
            resultat = jeu(personnage_joueur_1, personnage_joueur_2)
            if len(resultat) == 4:
                etat_actuel, retour_vers, personnage_joueur_1, personnage_joueur_2 = resultat
            else:
                etat_actuel, personnage_joueur_1, personnage_joueur_2 = resultat
        elif etat_actuel == "options":
            resultat = options(retour_vers, personnage_joueur_1, personnage_joueur_2)
            etat_actuel, personnage_joueur_1, personnage_joueur_2 = resultat
        elif etat_actuel == "quitter":
            run = False

        # Transition : la brume couvre l'ancien ecran ; le nouveau la dispersera.
        # SAUF entre le menu principal et son sous-menu 'battle' -> fluide (meme fond, pas de chargement).
        _sans_trans = {MENU_ACCUEIL, BATTLE_MENU}
        if run and etat_actuel != ancien_etat and not (ancien_etat in _sans_trans and etat_actuel in _sans_trans):
            transition_couvrir()
            BRUME_DISPERSE = True

    pygame.quit()


if __name__ == "__main__":
    main()
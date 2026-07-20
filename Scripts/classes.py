import pygame
import math
import random
try:
    import numpy as _np
except Exception:
    _np = None

# ----------------------------------------------------------------------
#  HORLOGE DE SIMULATION (deterministe -> pre-requis du multijoueur lockstep)
#  Le temps "ressenti" par les combattants ne vient PLUS de l'horloge murale
#  (pygame.time.get_ticks, non reproductible) mais d'un COMPTEUR DE FRAMES :
#  temps_ms() = frame * 1000 // 30. A 30 FPS fixe ca avance comme avant
#  (~33 ms/frame) -> aucun changement de feeling, mais 100% reproductible.
#  Le combat appelle avancer_horloge() une fois par frame de simulation.
# ----------------------------------------------------------------------
_HORLOGE = 0


def temps_ms():
    """Temps de simulation en ms, derive du compteur de frames (deterministe)."""
    return _HORLOGE * 1000 // 30


def avancer_horloge():
    """A appeler UNE fois par frame de simulation (quand le combat avance)."""
    global _HORLOGE
    _HORLOGE += 1


def reset_horloge():
    """Remet l'horloge de simulation a zero (debut de round)."""
    global _HORLOGE
    _HORLOGE = 0


# --- Horloge ACTIVE : comme l'horloge de simu MAIS figee pendant le hitstop.
# Sert au TIMING DES COMBOS : pendant le gel d'impact, les inputs ne sont pas lus
# (mvmt est saute) et l'animation de l'attaque est figee. Si la fenetre de combo
# comptait le temps reel (horloge normale), elle s'ecoulerait DANS le vide pendant
# le gel -> combo injouable des qu'on touche. En la mesurant sur l'horloge active,
# la fenetre "attend" le joueur et reste alignee sur l'animation. Deterministe
# (la duree du hitstop est identique des 2 cotes en lockstep).
_HORLOGE_ACTIVE = 0


def temps_actif_ms():
    """Temps ACTIF en ms (hors hitstop), pour les fenetres de combo."""
    return _HORLOGE_ACTIVE * 1000 // 30


def avancer_horloge_active():
    """A appeler 1x/frame SEULEMENT quand le combat avance (PAS pendant le hitstop)."""
    global _HORLOGE_ACTIVE
    _HORLOGE_ACTIVE += 1


def reset_horloge_active():
    """Remet l'horloge active a zero (debut de round)."""
    global _HORLOGE_ACTIVE
    _HORLOGE_ACTIVE = 0

# ======================================================================
#  TOUCHES (remappables via le menu Keybinds). "Left" = Joueur 1, "Right" =
#  Joueur 2. Schema UNIFIE pour tous les persos : move1 = action principale
#  (attaque), move2 = action secondaire (2e attaque / changer d'arme / lancer).
#  Defauts : move1=Y, move2=I (J1) ; move1=; , move2=! (J2).
#  Le dico est modifie en place par le menu des options (et charge depuis
#  settings.json), donc tous les persos voient les memes touches.
# ======================================================================
KEYBINDS = {
    "Left":  {"left": pygame.K_q,    "right": pygame.K_d,     "up": pygame.K_z,  "block": pygame.K_s,
              "move1": pygame.K_y,    "move2": pygame.K_i},
    "Right": {"left": pygame.K_LEFT, "right": pygame.K_RIGHT, "up": pygame.K_UP, "block": pygame.K_DOWN,
              "move1": pygame.K_SEMICOLON, "move2": pygame.K_EXCLAIM},
}

# Copie figee des touches par defaut (pour le bouton "Reset" du menu Keybinds).
KEYBINDS_DEFAULT = {cote: dict(KEYBINDS[cote]) for cote in KEYBINDS}


def touche_pressee(key, code):
    """True si la touche 'code' est enfoncee. code == None -> action NON liee
    (touche unbind), renvoie toujours False sans planter l'indexation."""
    return code is not None and bool(key[code])


class Inputs:
    """Snapshot des 6 actions d'un joueur sur UNE frame. Les combattants ne lisent
    plus le clavier directement : ils lisent self.inputs (rempli par le combat depuis
    le clavier local OU, a terme, le reseau). Serialisable trivialement (6 bits)."""
    __slots__ = ("left", "right", "up", "block", "move1", "move2")

    def __init__(self, left=False, right=False, up=False, block=False, move1=False, move2=False):
        self.left = left; self.right = right; self.up = up
        self.block = block; self.move1 = move1; self.move2 = move2


def lire_inputs(side):
    """Snapshot des inputs d'un cote, lus sur le clavier LOCAL (via KEYBINDS)."""
    key = pygame.key.get_pressed()
    kb = KEYBINDS[side]
    return Inputs(touche_pressee(key, kb["left"]), touche_pressee(key, kb["right"]),
                  touche_pressee(key, kb["up"]), touche_pressee(key, kb["block"]),
                  touche_pressee(key, kb["move1"]), touche_pressee(key, kb["move2"]))


def portee_perso(f):
    """Portee d'attaque AVANT approximative d'un combattant (px AVANT echelle), lue depuis sa
    structure d'attaques. On prend la portee de l'OUVERTURE (attack1, le coup que l'IA lance en
    premier) et NON le max : sinon un perso comme Lysandra (attack1 porte 220, attack2 470) etait
    engage a ~420 et son 1er coup (attack1) frappait dans le vide. Sert a l'IA pour engager a SA
    vraie portee ET respecter celle de l'adversaire (spacing). Fallback si inconnu."""
    try:
        cfg = getattr(f, "config", None)
        if cfg and "attacks" in cfg:                    # Fighter / Fighter2 : hitboxes en tuples
            atk = cfg["attacks"].get(1) or next(iter(cfg["attacks"].values()))
            r = max((dx + w for (dx, dy, w, h) in atk.get("hitboxes_right", ())), default=0)
            if r:
                return r
        if hasattr(f, "WEAPONS"):                       # Konrad : allonge de l'arme EQUIPEE
            arme = f.WEAPONS.get(getattr(f, "current_weapon", 1))
            if arme:
                return arme["allonge"]
        if hasattr(f, "ATTACKS"):                       # Arinya (allonge) / Stormr (avant) : attack1
            a1 = getattr(f, "ATTACK1", None)
            atk = f.ATTACKS.get(a1) if a1 is not None else None
            if atk is None:
                atk = next(iter(f.ATTACKS.values()))
            r = atk.get("allonge", atk.get("avant", 0))
            if r:
                return r
    except Exception:
        pass
    return 340


def _ia_zoneur(f):
    """L'adversaire a-t-il un JEU A DISTANCE (projectile) ? -> l'IA COLLE la distance au lieu
    de rester a portee de zonage (adaptation au perso du joueur)."""
    return hasattr(f, "SPEAR_LEN") or hasattr(f, "LIGHTNING_DMG")


class IA:
    """Cerveau d'un combattant CONTROLE PAR L'ORDI (mode solo). Produit un Inputs par frame
    depuis l'etat (moi, adversaire) -> se branche EXACTEMENT comme un clavier
    (fighter.inputs = ia.decide(moi, adv)). GENERIQUE : n'utilise que les 6 boutons universels
    (left/right/up/block/move1/move2), donc marche pour les 7 persos sans connaitre leur kit.
    Pour tourner le perso vers la cible, on presse la DIRECTION vers elle sur la meme frame que
    l'attaque (mvmt traite la direction AVANT l'attaque -> flip correct).
    ADAPTATIF AU PERSO DU JOUEUR : lit la PORTEE reelle des deux persos (portee_perso) -> engage
    a SA vraie portee, respecte celle de l'adversaire (spacing/bait pour punir un coup dans le
    vide), et COLLE un zoneur (Arinya/Stormr) au lieu de subir son jeu a distance.
    L'IA PUNIT aussi la recovery adverse (fin d'attaque = fenetre) et enchaine de VRAIS combos
    (suite de boutons lue dans config['combo']). RNG prive (le solo n'est pas lockstep).
    Parade FRAME-PERFECT de l'IA = amelioration future ; ici la defense reste REACTIVE."""

    CONTACT = 150     # trop pres : on respace au lieu de traverser l'adversaire
    MENACE_MAX = 700  # plafond de la distance de menace (px) : evite de bloquer beaucoup trop tot

    #   reaction : frames avant de repondre a une menace (temps de reaction simule ; haut = lent)
    #   garde    : proba de bloquer une menace (apres le delai de reaction)
    #   agressif : proba d'attaquer quand on est en portee et pret
    #   combo    : proba d'enchainer (vrai combo, bouton lu dans la sequence du perso)
    #   punish   : proba de punir un adversaire en RECOVERY (il vient d'attaquer dans le vide)
    #   espace   : tendance au SPACING/bait (rester juste hors de portee adverse) ; 0 = fonce
    #   erreur   : proba de RATER une garde (ouverture offerte au joueur)
    #   hesite   : proba de rester plante quelques frames (respiration ; ouvertures)
    #   adapt    : intensite de l'ADAPTATION au gameplay du joueur (0 = ne lit pas ses habitudes)
    #   tactique : proba d'utiliser un MOVE SPECIAL du kit (tp, spin, dash, lance, saut...) quand
    #              la situation s'y prete (0 = ne joue que move1/move2/garde generiques)
    NIVEAUX = {
        "facile":    dict(reaction=10, garde=0.28, agressif=0.45, combo=0.15, punish=0.25, espace=0.00, erreur=0.14, hesite=0.35, adapt=0.0, tactique=0.00),
        "normal":    dict(reaction=5,  garde=0.62, agressif=0.82, combo=0.55, punish=0.70, espace=0.35, erreur=0.05, hesite=0.10, adapt=0.6, tactique=0.06),
        "difficile": dict(reaction=1,  garde=0.94, agressif=0.98, combo=0.95, punish=1.00, espace=0.60, erreur=0.00, hesite=0.00, adapt=1.0, tactique=0.12),
    }

    def __init__(self, niveau="normal", graine=None):
        self.niveau = niveau if niveau in self.NIVEAUX else "normal"
        self.p = self.NIVEAUX[self.niveau]
        self._pe = dict(self.p)    # params EFFECTIFS (base + adaptation au joueur)
        self.rng = random.Random(graine)
        self._menace_t = None      # 1re frame ou l'adversaire menace (latence de reaction)
        self._t = 0
        self._pause = 0            # frames d'hesitation restantes
        self._adv_att_prev = False # etat 'attaque' de l'adversaire la frame precedente (fronts)
        self._combo = False        # enchainement en cours
        self._cbo = []             # boutons d'ENCHAINEMENT (apres l'ouverture)
        self._cbo_i = 0
        self._cbo_pulse = False    # pulse 1 frame/2 (le combo attend un FRONT, pas un maintien)
        self._fi_prev = 0          # frame_index precedent (detecte une nouvelle sous-attaque)
        self._combo_grace = 0      # frames de grace entre 2 sous-attaques (evite que la punition
        # LECTURE DU GAMEPLAY DU JOUEUR (habitudes) -> on contre. Fenetre glissante (decay).
        self._obs_f = 0            # frames observees
        self._obs_block = 0        # frames ou le joueur bloque
        self._obs_atk = 0         # nb d'attaques LANCEES par le joueur (fronts)
        self._casse_garde = False  # le joueur turtle -> on est en mode "casse-garde" (agression forcee)
        # MOVES SPECIAUX du kit : file d'inputs multi-frames (double-tap dash/spin, charge lance...)
        self._file = []            # sequence d'inputs a jouer (chaque element = dict de champs Inputs)
        self._special_cd = 0       # frames avant le prochain move special (anti-spam)

    def _observer(self, adv, debut_att):
        """Accumule les habitudes du joueur (garde / agression), fenetre glissante avec decay."""
        self._obs_f += 1
        if getattr(adv, "block", False):
            self._obs_block += 1
        if debut_att:
            self._obs_atk += 1
        if self._obs_f >= 150:     # decay : on halve tout -> l'IA suit le jeu RECENT du joueur
            self._obs_f //= 2
            self._obs_block //= 2
            self._obs_atk //= 2

    def _adapter(self):
        """Recalcule les params EFFECTIFS en fonction des habitudes lues (intensite = 'adapt')."""
        base = self.p
        a = base.get("adapt", 0.0)
        pe = dict(base)
        self._casse_garde = False
        if a > 0 and self._obs_f >= 24:
            taux_garde = self._obs_block / self._obs_f            # part de frames ou le joueur bloque
            rush = self._obs_atk * 30.0 / self._obs_f            # attaques/seconde du joueur (~30 FPS)
            # ANTI-TURTLE : le joueur bloque beaucoup -> on FORCE l'agression + on COLLE (chip la
            # garde jusqu'a la casser), et on tient a le punir sur la garde brisee.
            if taux_garde > 0.30:
                pe["agressif"] = min(1.0, base["agressif"] + a * 0.45)
                pe["combo"] = min(1.0, base["combo"] + a * 0.20)
                pe["espace"] = base["espace"] * (1.0 - a)        # moins de bait : on reste au contact
                self._casse_garde = True
            # ANTI-RUSH : le joueur attaque beaucoup -> on defend + on bait plus (whiff-punish).
            if rush > 1.8:
                pe["garde"] = min(0.98, base["garde"] + a * 0.15)
                pe["espace"] = min(0.95, pe["espace"] + a * 0.25)
        self._pe = pe

    def lecture(self):
        """Etat de lecture du joueur (pour debug/tests) : ses taux + les params adaptes."""
        f = max(1, self._obs_f)
        return {"garde_joueur": self._obs_block / f, "rush_joueur": self._obs_atk * 30.0 / f,
                "agressif_eff": self._pe["agressif"], "espace_eff": self._pe["espace"],
                "casse_garde": self._casse_garde}

    def _seq_combo(self, moi):
        """Suite des boutons d'ENCHAINEMENT (apres l'ouverture move1) PROPRE au perso. Les persos
        ont des boutons DIFFERENTS pour chainer -> une sequence generique cassait des combos (ex:
        Stormr chaine M2 puis M1, son 3e coup = la FOUDRE si la cible est chargee ; Arinya rechaine
        en M1 -- son M2 est le LANCER, pas un chainon)."""
        cfg = getattr(moi, "config", None)
        if cfg and "combo" in cfg:                    # Oswald (M2,M2) / Barrion (M2,M1) : lu du config
            combo = cfg["combo"]; act = combo.get("depart")
            chaines = combo.get("chaines", {}); vus = set(); seq = []
            while act in chaines and act not in vus:
                vus.add(act); btn, act = chaines[act]; seq.append(btn)
            return seq
        if hasattr(moi, "COMBO1_MIN"):                # Stormr : M1 -> M2 -> M1 (3e = foudre si charge)
            return ["move2", "move1"]
        if hasattr(moi, "COMBO_MIN") and hasattr(moi, "combo_queued"):  # Arinya : M1 -> M1 (melee)
            return ["move1"]
        return []                                     # Kenshi / Lysandra / Konrad : pas d'enchainement

    def _peut_combo(self, moi):
        return len(self._seq_combo(moi)) > 0

    def _armer_combo(self, moi):
        """Prepare l'enchainement (appele quand on a DECIDE de comboer)."""
        self._combo = True
        self._cbo = self._seq_combo(moi)
        self._cbo_i = 0
        self._cbo_pulse = False
        self._fi_prev = getattr(moi, "frame_index", 0)

    def _lancer_attaque(self, moi, vers, bien_face, veut_combo):
        """Construit l'Inputs d'une attaque en garantissant le FACING. IMPORTANT : certains persos
        (Arinya, Stormr) posent attacking=True AVANT le bloc de deplacement qui gere le flip -> la
        direction pressee sur la frame d'attaque ne les tourne PAS. Il faut donc DEJA regarder la
        cible : si ce n'est pas le cas, on se tourne d'abord (l'attaque part la frame suivante),
        sinon on attaque dans le vide. move2 n'est un POKE que si c'est une ATTAQUE (Konrad move2 =
        arme suivante, Arinya move2 = lancer -> on poke en move1)."""
        i = Inputs()
        if not bien_face:
            setattr(i, vers, True)            # se tourner vers la cible d'abord
            return i
        if veut_combo and self._peut_combo(moi):
            i.move1 = True                    # les combos partent d'attack1 (move1)
            self._armer_combo(moi)
        else:
            move2_ok = not (hasattr(moi, "WEAPONS") or hasattr(moi, "SPEAR_LEN"))
            setattr(i, "move2" if (move2_ok and self.rng.random() < 0.4) else "move1", True)
            self._combo = False
        setattr(i, vers, True)
        return i

    def _enfiler(self, frames):
        """Programme une SEQUENCE d'inputs multi-frames (double-tap, charge...) : frames = liste de
        dict de champs Inputs. Renvoie la 1re a jouer maintenant ; le reste est joue aux frames
        suivantes (cf debut de decide)."""
        self._file = list(frames)
        return Inputs(**self._file.pop(0))

    def _tactique_perso(self, moi, cfg, dd, vers):
        """Utilise le KIT SPECIFIQUE du perso (moves a double-tap / charge / haut) selon la distance
        dd (px AVANT echelle). Renvoie un Inputs (move lance, _special_cd arme) ou None (rien)."""
        # OSWALD : TELEPORT offensif (invulnerable) -> combler la distance / mixup dans le dos.
        if cfg and "teleport" in cfg and getattr(moi, "tp_cd", 0) == 0 \
                and getattr(moi, "attack_cd", 0) == 0 and dd > 240:
            self._special_cd = 30
            return Inputs(up=True, **{vers: True})
        # BARRION : SPIN (double-tap) a moyenne distance (mobilite phare), sinon SAUT-attaque de loin.
        if cfg and "spin" in cfg:
            if getattr(moi, "spin_cd", 0) == 0 and getattr(moi, "attack_cd", 0) == 0 and dd > 180:
                self._special_cd = 45
                return self._enfiler([{}, {vers: True}, {}, {vers: True}])   # double-tap -> spin
            if "jump_attack" in cfg and getattr(moi, "attack_cd", 0) == 0 and dd > 300:
                self._special_cd = 35
                return Inputs(up=True, **{vers: True})                        # saut-attaque (salto)
        # ARINYA : LANCE chargee pour zoner de tres loin, sinon DASH (double-tap) pour combler.
        if hasattr(moi, "SPEAR_LEN"):
            if getattr(moi, "has_spear", True) and getattr(moi, "attack_cd", 0) == 0 and dd > 430:
                self._special_cd = 45
                return self._enfiler([{"move2": True, vers: True}] * 5 + [{}])  # charge puis lache
            if getattr(moi, "dash_cd", 0) == 0 and dd > 240:
                self._special_cd = 25
                return self._enfiler([{}, {vers: True}, {}, {vers: True}])    # double-tap -> dash
        # KONRAD : varier l'ARSENAL (move2 = changer d'arme) hors de portee immediate.
        if hasattr(moi, "WEAPONS") and getattr(moi, "switch_cd", 0) == 0 and dd > 200:
            self._special_cd = 60
            return Inputs(move2=True)                                         # arme suivante
        # LYSANDRA : COUP SISMIQUE charge -- JAMAIS a bout portant (figee = sac de frappe
        # face aux rapides) : a mi-distance seulement, avec parcimonie, charge courte en
        # general et PLEINE (perce-garde) 3 fois sur 10 pour punir les turtles.
        if cfg and cfg.get("seisme") and getattr(moi, "attack_cd", 0) == 0 \
                and not getattr(moi, "attacking", False) and 260 < dd < 430:
            self._special_cd = 110
            n = 52 if self.rng.random() < 0.3 else 26
            return self._enfiler([{"move2": True}] * n + [{}])
        return None

    def decide(self, moi, adv):
        """Renvoie l'Inputs de l'IA pour cette frame."""
        self._t += 1
        i = Inputs()
        adv_att = getattr(adv, "attacking", False)
        debut_att_adv = adv_att and not self._adv_att_prev  # l'adversaire LANCE une attaque (front)
        fin_att_adv = self._adv_att_prev and not adv_att    # ... ou vient d'en FINIR une (recovery)
        self._adv_att_prev = adv_att

        # LECTURE DU JOUEUR : on observe ses habitudes puis on adapte nos params effectifs (_pe).
        self._observer(adv, debut_att_adv)
        self._adapter()
        p = self._pe

        # Stun / mort : les inputs sont ignores par mvmt de toute facon (on vide la file).
        if not getattr(moi, "alive", True) or getattr(moi, "hit", False):
            self._menace_t = None
            self._combo = False
            self._file = []
            return i
        if self._special_cd > 0:
            self._special_cd -= 1

        e = getattr(moi, "echelle", 1.0)
        contact = self.CONTACT * e
        dx = adv.rect.centerx - moi.rect.centerx
        d = abs(dx)
        vers = "right" if dx > 0 else "left"      # direction VERS l'adversaire (pour se tourner)
        loin = "left" if dx > 0 else "right"
        ma_portee = portee_perso(moi) * e
        sa_portee = portee_perso(adv) * e
        zoneur = _ia_zoneur(adv)
        pret = getattr(moi, "attack_cd", 0) == 0
        attaque_d = ma_portee * 0.90
        bien_face = getattr(moi, "flip", (dx < 0)) == (dx < 0)   # regarde-t-on deja la cible ?
        cfg = getattr(moi, "config", None)
        # Haut = ESQUIVE pour ce perso ? (Oswald=teleport / Barrion=saut-attaque -> non)
        up_dodge = not (cfg and ("teleport" in cfg or "jump_attack" in cfg))

        # === 0) SEQUENCE SPECIALE EN COURS (double-tap dash/spin, charge lance) : on la deroule. ===
        if self._file:
            return Inputs(**self._file.pop(0))
        # === 0a) LYSANDRA : jamais FIGEE en charge sismique par ACCIDENT. Seule la tactique
        # dediee (file ci-dessus) maintient move2 ; si un gel demarre parce que l'offense
        # normale tenait le bouton, on relache IMMEDIATEMENT (charge quasi nulle).
        if getattr(moi, "_seisme_gel", False):
            return i
        # === 0b) SLAM Barrion : un saut-attaque est en vol -> on peut ECRASER (move2 en l'air). ===
        if getattr(moi, "jumping_attack", False) and not getattr(moi, "hit_down", False):
            return Inputs(move2=True) if self.rng.random() < 0.30 else i

        # === 1) COMBO EN COURS : on enchaine (le systeme attend un FRONT montant -> on PULSE le
        # bouton 1 frame sur 2 pour couvrir la fenetre frame-perfect ; bon bouton lu dans la
        # sequence du perso : Oswald M2,M2 / Barrion M2,M1). ===
        if getattr(moi, "attacking", False):
            self._combo_grace = 3                 # tant qu'on attaque, on recharge la grace
            if self._combo and self._cbo_i < len(self._cbo):
                fi = getattr(moi, "frame_index", 0)
                if fi < self._fi_prev - 1:        # une nouvelle sous-attaque est partie -> bouton suivant
                    self._cbo_i += 1
                    self._cbo_pulse = False
                self._fi_prev = fi
                if self._cbo_i < len(self._cbo):
                    self._cbo_pulse = not self._cbo_pulse
                    if self._cbo_pulse:
                        setattr(i, self._cbo[self._cbo_i], True)
                    setattr(i, vers, True)
            return i
        # Trou de 1-2 frames ENTRE deux sous-attaques enchainees (attacking retombe brievement) :
        # on continue de PULSER le bouton d'enchainement pour ne pas casser le combo -- sinon la
        # punition-de-stun plus bas re-ouvrait attack1 et l'IA n'atteignait jamais le finisher.
        if self._combo and self._cbo_i < len(self._cbo) and self._combo_grace > 0:
            self._combo_grace -= 1
            self._cbo_pulse = not self._cbo_pulse
            if self._cbo_pulse:
                setattr(i, self._cbo[self._cbo_i], True)
            setattr(i, vers, True)
            return i
        self._combo = False

        # === 2) PUNITION DE STUN / GARDE BRISEE : l'adversaire est SONNE -> on DEBALLE le combo max
        # (fenetre libre : il ne peut ni bloquer ni riposter). C'est LA grosse recompense du chip qui
        # casse la garde. Hors de portee -> on FONCE (pas de bait) pour ne pas rater la fenetre. ===
        if getattr(adv, "hit", False):
            if pret and d <= ma_portee * 1.05:
                return self._lancer_attaque(moi, vers, bien_face, True)   # combo max sur le stun
            setattr(i, vers, True)                # hors de portee : se precipiter sur l'adversaire sonne
            return i

        # === 3) DEFENSE REACTIVE : menace dans la portee de l'ADVERSAIRE. AUTO-PROTECTION : si MA
        # garde est presque cassee, je NE bloque plus (je me ferais briser) -> je recule au lieu. ===
        menace = (adv_att or getattr(adv, "spear", None) is not None
                  or getattr(adv, "lightning", None) is not None
                  or getattr(adv, "spinning", False)
                  or getattr(adv, "jumping_attack", False))
        menace_d = min(sa_portee * 1.3 + 60 * e, self.MENACE_MAX * e)
        if menace and d < menace_d:
            if self._menace_t is None:
                self._menace_t = self._t
            if (self._t - self._menace_t) >= self.p["reaction"] \
                    and self.rng.random() < p["garde"] and self.rng.random() >= self.p["erreur"]:
                garde_usee = getattr(moi, "block_health", 0) >= BOUCLIER_MAX * 0.5
                # ESQUIVE (Haut) : pour les persos qui l'ont, parfois esquiver (invuln, 0 degat) au
                # lieu de bloquer -- surtout si la garde est deja entamee (on la preserve).
                if up_dodge and getattr(moi, "dodge_cd", 0) == 0 \
                        and self.rng.random() < (0.6 if garde_usee else 0.3):
                    i.up = True
                    return i
                if getattr(moi, "block_health", 0) < BOUCLIER_MAX * 0.75:
                    i.block = True                # garde SAINE -> on bloque (attaque amortie)
                else:
                    setattr(i, loin, True)        # garde presque cassee -> on se degage (pas de bris)
                return i
        else:
            self._menace_t = None

        # === 4) PUNITION DE RECOVERY : l'adversaire a attaque dans le vide -> je frappe (fort) ===
        en_recovery = fin_att_adv or (not adv_att and getattr(adv, "attack_cd", 0) > 0)
        if en_recovery and pret and d <= ma_portee and self.rng.random() < p["punish"]:
            return self._lancer_attaque(moi, vers, bien_face, self.rng.random() < p["combo"])

        # === 5) HESITATION : par moments, on ne fait rien (respiration -> ouvertures) ===
        if self._pause > 0:
            self._pause -= 1
            return i
        if self.rng.random() < self.p["hesite"]:
            self._pause = self.rng.randint(2, 8)
            return i

        # === 5b) MOVE SPECIAL DU KIT (tp / spin / dash / lance chargee / saut) si la distance s'y
        # prete -> l'IA utilise vraiment le kit du perso (double-tap et charges via une file). ===
        if self._special_cd == 0 and self.rng.random() < p.get("tactique", 0.0):
            act = self._tactique_perso(moi, getattr(moi, "config", None), d / e if e else d, vers)
            if act is not None:
                return act

        # === 6) OFFENSE : dans MA portee et pret -> je frappe (combo ou poke varie). Seuil d'attaque
        # = seuil d'approche -> AUCUNE zone morte (sinon un perso a courte portee comme Oswald
        # s'arretait juste trop loin pour toucher). NB : move2 n'est un POKE que si c'est une attaque
        # (pour Konrad move2=changement d'arme, pour Arinya move2=lancer -> on poke en move1). ===
        if pret and d <= attaque_d and self.rng.random() < p["agressif"]:
            return self._lancer_attaque(moi, vers, bien_face, self.rng.random() < p["combo"])

        # === 7) DEPLACEMENT (adaptatif) : approcher jusqu'a MA portee ; pendant ma recovery je
        # respace hors de portee ADVERSE (bait -> punir un coup dans le vide), SAUF face a un zoneur
        # ou en mode CASSE-GARDE (le joueur turtle) ou je COLLE pour chip/casser la garde. ===
        if d > attaque_d:
            setattr(i, vers, True)                # approcher jusqu'a etre en portee
            if (not up_dodge) and zoneur and d > ma_portee * 1.5 and self.rng.random() < 0.05:
                i.up = True                       # vs zoneur : Haut = rapprochement (Barrion/Oswald seulement)
        elif d < contact:
            setattr(i, loin, True)                # trop colle : respacer
        elif not pret and not zoneur and not self._casse_garde and not adv_att \
                and p["espace"] > 0 and d < sa_portee * 0.9:
            setattr(i, loin, True)                # en recovery : reculer hors de portee (bait)
        return i


# ======================================================================
#  DEBUG D'AFFICHAGE (menu Options > Display > Debug, cases a cocher).
#  PERMISSIF : tout code qui possede une hitbox / une damage box / un cd
#  passe par ici -> automatiquement couvert par les options de debug.
#   - "hitbox"     : boite de collision de TOUT ce qui est hitable ; chaque
#                    draw de perso/dummy appelle debug_box(self.rect, "hitbox").
#   - "damage box" : toute boite qui INFLIGE des degats (attaques, spin,
#                    saut/slam, lance...) ; le code qui construit la boite
#                    appelle debug_box(r, "damage box") (no-op si decoche).
#   - "cd"         : les barres de recharge (dash Arinya, spin Barrion, ...)
#                    ne s'affichent que si DEBUG_AFFICHAGE["cd"] -- exception :
#                    la barre d'armes de Konrad, toujours affichee (gameplay).
#  Les boucles de combat appellent debug_draw(screen) apres le dessin.
# ======================================================================
DEBUG_AFFICHAGE = {"hitbox": False, "damage box": False, "cd": False}
_DEBUG_BOXES = []
_DEBUG_COULEURS = {"hitbox": (60, 255, 60), "damage box": (255, 60, 60)}


def debug_box(rect, cat):
    """Enregistre une boite a dessiner par-dessus la frame (no-op si la case est decochee)."""
    if DEBUG_AFFICHAGE.get(cat):
        if len(_DEBUG_BOXES) > 400:      # garde-fou (ex : accumulation hors boucle de combat)
            _DEBUG_BOXES.clear()
        _DEBUG_BOXES.append((pygame.Rect(rect), cat))


def debug_draw(surface):
    """Dessine puis vide les boites de debug de la frame (appele par les boucles de combat)."""
    for r, cat in _DEBUG_BOXES:
        pygame.draw.rect(surface, _DEBUG_COULEURS.get(cat, (255, 255, 0)), r, 2)
    _DEBUG_BOXES.clear()


def esquive(target):
    """True si la cible est INTOUCHABLE : le coup RATE simplement, SANS effet bouclier
    (contrairement au spin de Barrion qui renvoie un blocked_hit). SOURCE UNIQUE : toutes
    les sources de degats la consultent -> tout futur etat d'esquive s'ajoute ICI.
    Actuellement : le teleport d'Oswald ET l'ESQUIVE (Haut) i-frames des autres persos."""
    return getattr(target, "teleporting", False) or getattr(target, "dodge_iframes", 0) > 0


def esquive_params(f):
    """Profil d'esquive PROPRE au perso (son AGILITE) : (dur, speed, hop, iframes, cd). Lu depuis
    `ESQUIVE_PROFIL` (Konrad/Arinya/Stormr) ou config['esquive'] (Kenshi/Lysandra) ; a defaut, les
    constantes 'normales'. -> on SENT la difference : agile = vif/rapide/cd court, lourd = lent."""
    pr = getattr(f, "ESQUIVE_PROFIL", None)
    if pr is None:
        cfg = getattr(f, "config", None)
        pr = cfg.get("esquive") if cfg else None
    pr = pr or {}
    return (pr.get("dur", ESQUIVE_DUR), pr.get("speed", ESQUIVE_SPEED), pr.get("hop", ESQUIVE_HOP),
            pr.get("iframes", ESQUIVE_IFRAMES), pr.get("cd", ESQUIVE_CD))


LAME_FENETRE_MS = 800    # fenetre "lame affutee" apres une esquive de Kenshi (~24 frames actives)


def demarrer_esquive(f, U, e, target=None):
    """ESQUIVE (Haut) = un SAUT INVULNERABLE, toujours DANS LE SENS INVERSE de l'adversaire (on
    s'ELOIGNE de lui), QUEL QUE SOIT le regard : ennemi devant -> saut en arriere ; ennemi dans
    le dos (on s'est retourne) -> saut en avant (= toujours loin de lui). Vitesse/hauteur/cd selon
    l'AGILITE du perso (esquive_params). A appeler en tete de mvmt. Gere cooldown + init paresseuse.
    KIT KENSHI (cles optionnelles de config['esquive'], inertes pour les autres persos) :
      - "traverse": px -> de PRES, l'esquive TRAVERSE l'ennemi (depose dans son dos) ; toute
        esquive RETOMBEE PROPREMENT ouvre une fenetre 'lame affutee' (_lame_niv 1, ou 2 si
        l'ennemi a ete traverse) exploitee par Kenshi.mult_degats (Premier sang / Passe-lame).
      - "cancel_cd": frames -> DODGE-CANCEL : Haut pendant une attaque coupe l'attaque en
        esquive (compteur nb_cancels pour le Moves Guide), puis cooldown dedie cancel_cd.
    Le FLOW de Kenshi (stacks de coups) raccourcit aussi le cooldown d'esquive ici."""
    f.dodging = getattr(f, "dodging", False)      # garantit l'attribut (init paresseuse)
    if getattr(f, "hit", False):
        f.dodging = False                         # touche pendant l'esquive (atterrissage vulnerable) -> annulee
        f._pl_depart = None                       # esquive cassee -> pas de fenetre de lame
    if getattr(f, "dodge_cd", 0) > 0:
        f.dodge_cd -= 1
    if getattr(f, "cancel_cd", 0) > 0:
        f.cancel_cd -= 1
    if getattr(f, "dodge_iframes", 0) > 0:
        f.dodge_iframes -= 1
    pr_cfg = (getattr(f, "config", None) or {}).get("esquive") or {}
    # Traversee (dash rasant) terminee -> on coupe la poussiere de dash empruntee a Arinya.
    if not f.dodging and getattr(f, "_dodge_trav", False):
        f.dashing = False
        f._dodge_trav = False
    # Suivi de la TRAVERSEE pendant le vol : le cote par rapport a la cible a-t-il change ?
    if f.dodging and target is not None and getattr(f, "_pl_depart", None) is not None:
        cote = 1 if f.rect.centerx >= target.rect.centerx else -1
        if cote != f._pl_depart:
            f._pl_croise = True
    # Fin d'esquive RETOMBEE PROPREMENT (pas cassee par un coup) -> fenetre de lame (Kenshi).
    if getattr(f, "_pl_dodge_prev", False) and not f.dodging \
            and getattr(f, "_pl_depart", None) is not None:
        if pr_cfg.get("traverse"):
            f._lame_niv = 2 if getattr(f, "_pl_croise", False) else 1
            f._lame_fin = temps_actif_ms() + LAME_FENETRE_MS
            if getattr(f, "_pl_croise", False) and target is not None:
                # Il atterrit FACE a l'ennemi traverse -> pret a frapper dans le dos.
                f.flip = f.rect.centerx > target.rect.centerx
        f._pl_depart = None
    f._pl_dodge_prev = f.dodging
    up_press = U and not getattr(f, "_dodge_up_prev", False)
    f._dodge_up_prev = U
    if not up_press or getattr(f, "dodging", False) or getattr(f, "dodge_cd", 0) != 0:
        return
    en_attaque = getattr(f, "attacking", False)
    if (en_attaque and pr_cfg.get("cancel_cd") and getattr(f, "cancel_cd", 0) == 0
            and not getattr(f, "hit", False)):
        # DODGE-CANCEL (Kenshi) : l'attaque en cours est COUPEE par l'esquive.
        f.attacking = False
        f.attack_type = 0
        f.damage_dealt = False
        f.attack_cd = max(getattr(f, "attack_cd", 0), 10)   # pas de re-attaque instantanee
        f.cancel_cd = pr_cfg["cancel_cd"]
        f.nb_cancels = getattr(f, "nb_cancels", 0) + 1
        en_attaque = False
    if (en_attaque or getattr(f, "hit", False) or getattr(f, "dashing", False)
            or getattr(f, "charging", False) or getattr(f, "throwing", False)):
        return
    # SENS = a l'OPPOSE de l'adversaire ; EXCEPTION "traverse" (Kenshi) : de PRES, l'esquive
    # passe AU TRAVERS de l'ennemi et le depose dans son dos (de loin : fuite normale).
    trav_dash = False
    if target is not None:
        loin = 1 if f.rect.centerx >= target.rect.centerx else -1
        trav = pr_cfg.get("traverse")
        if trav and abs(f.rect.centerx - target.rect.centerx) < trav * e:
            f.dodge_dir = -loin                   # vers/au travers de l'ennemi
            trav_dash = True
        else:
            f.dodge_dir = loin
        f._pl_depart = loin                       # cote de depart (pour detecter le croisement)
        f._pl_croise = False
    else:
        f.dodge_dir = 1 if f.flip else -1         # fallback (pas de cible) : a l'oppose du regard
        f._pl_depart = None
    dur, speed, hop, iframes, cd = esquive_params(f)   # profil d'AGILITE du perso (fige au declenchement)
    f.dodging = True
    f.dodge_timer = dur
    f.dodge_iframes = iframes
    f.dodge_cd = max(16, cd - 6 * getattr(f, "flow", 0))   # FLOW : l'esquive recharge plus vite
    f._dodge_dur = dur; f._dodge_speed = speed; f._dodge_hop = hop
    f._dodge_trav = trav_dash
    if trav_dash:
        f._dodge_hop = 0                          # traversee = DASH RASANT (pas de saut)
        f._dodge_speed = pr_cfg.get("trav_speed", f._dodge_speed)   # ... et plus VIF
        f._dodge_dur = pr_cfg.get("trav_dur", f._dodge_dur)
        f.dodge_timer = f._dodge_dur


def esquive_dx(f, e):
    """Avance l'esquive d'une frame : renvoie dx (px) ET met a jour l'arc VERTICAL _dodge_lift
    (saut en cloche). Vitesse/duree/hauteur = le profil FIGE au declenchement (agilite du perso)."""
    dur = getattr(f, "_dodge_dur", ESQUIVE_DUR)
    hop = getattr(f, "_dodge_hop", ESQUIVE_HOP)
    speed = getattr(f, "_dodge_speed", ESQUIVE_SPEED)
    f.dodge_timer = getattr(f, "dodge_timer", 0) - 1
    t = dur - f.dodge_timer                        # 1..dur
    f._dodge_lift = hop * e * math.sin(math.pi * max(0, min(t, dur)) / dur)
    if f.dodge_timer <= 0:
        f.dodging = False
        f._dodge_lift = 0.0
    return getattr(f, "dodge_dir", -1) * speed * e


# TOUS les cooldowns affichables par le debug "cd" : (label, attribut, couleur, max typique
# pour la longueur de barre). PERMISSIF : un futur cooldown = une ligne ici, et tous les
# persos qui portent l'attribut l'affichent automatiquement.
_DEBUG_CDS = (("atk",  "attack_cd", (235, 120, 70),  40),
              ("blk",  "block_cd",  (100, 150, 235), 120),
              ("spin", "spin_cd",   (222, 130, 55),  40),
              ("tp",   "tp_cd",     (255, 215, 120), 26),
              ("dash", "dash_cd",   (90, 170, 220),  45),
              ("dodge", "dodge_cd", (150, 220, 150), 45),
              ("cncl", "cancel_cd", (200, 160, 255), 90),
              ("wpn",  "switch_cd", (185, 185, 185), 36))
_FONT_CD = None


def debug_cds(surface, f):
    """DEBUG "cd" (menu Display > Debug) : pile de mini-barres etiquetees au-dessus du perso,
    UNE par cooldown ACTIF (attaque, blocage casse, spin, teleport, dash, armes...). Appele
    par le draw de chaque combattant (no-op si la case est decochee)."""
    global _FONT_CD
    if not DEBUG_AFFICHAGE["cd"]:
        return
    if _FONT_CD is None:
        _FONT_CD = pygame.font.SysFont("consolas,arial", 16, bold=True)
    y = f.rect.top - 52
    w = 90
    x = f.rect.centerx - w // 2
    for nom, attr, coul, vmax in _DEBUG_CDS:
        v = getattr(f, attr, 0)
        if not v or v <= 0:
            continue
        pygame.draw.rect(surface, (25, 22, 30), (x, y, w, 8))
        pygame.draw.rect(surface, coul, (x, y, max(2, int(w * min(1.0, v / float(vmax)))), 8))
        lab = _FONT_CD.render("%s %d" % (nom, int(v)), True, coul)
        surface.blit(lab, (x + w + 6, y - 5))
        y -= 15


# ======================================================================
#  MECANIQUES DE COMBAT COMMUNES (anti stun-lock, parade, armure, recul,
#  vie du bouclier). SOURCES UNIQUES : tous les persos passent par ici.
# ======================================================================
PARADE_FENETRE = 0      # gf : FRAME PERFECT PUR -- garde levee PILE a la frame de l'impact
                        #   (comme le renvoi de la lance d'Arinya, SPEAR_PARADE = 0)
GRACE_MS = 500          # apres un stun : re-stun impossible pendant ce delai (degats passent)
# BOUCLIER (nerf 2026-07 : "le bloc tank trop et a trop de vie") -- SOURCES UNIQUES.
BLOC_CHIP = 0.25        # part des degats qui PASSE malgre un blocage (etait 0.10 : le bloc
                        #   absorbait 90% -> turtle imprenable ; a 0.25 il faut vraiment PARER)
BOUCLIER_MAX = 55       # usure ou la garde CASSE (etait 75 : bouclier trop endurant)
REGEN_BOUCLIER = 0.10   # usure resorbee/frame hors blocage (etait 0.15 : + lent -> pas de turtle en boucle)
# ESQUIVE (Haut) : SAUT INVULNERABLE (i-frames) a l'oppose de l'ennemi -- remplace le saut vertical
# inutile (Kenshi/Lysandra/Konrad/Arinya/Stormr ; Oswald=teleport, Barrion=saut-attaque gardent Haut).
# Ces constantes = le profil "NORMAL" (Stormr). Chaque perso a son AGILITE via esquive_params() :
# Kenshi/Arinya agiles (vif, rapide, cd court) ; Konrad lourd, Lysandra tres lourde (lent, cd long).
ESQUIVE_DUR = 12        # frames du bond -- plus grand = saut plus LENT (plus d'airtime, moins agile)
ESQUIVE_SPEED = 24      # vitesse HORIZONTALE (px/frame, x echelle) -> distance = dur*speed
ESQUIVE_HOP = 140       # HAUTEUR du saut (px, x echelle)
ESQUIVE_IFRAMES = 4     # invulnerabilite COURTE, uniquement au DEBUT (<< DUR 12) -> il faut TIMER
                        #   l'esquive pile sur le coup ; sinon on est vulnerable le reste du saut
                        #   (l'adversaire qui time bien PEUT nous toucher) -> pas une evasion gratuite.
ESQUIVE_CD = 45         # recharge (1,5 s a 30 FPS)


def grace_active(f):
    """GRACE post-stun : apres un take_hit, on ne peut pas etre RE-stun pendant GRACE_MS
    (les degats passent, mais on garde le controle : bloquer/riposter/fuir possible) ->
    casse les boucles de stun-lock au spam. Le sprite clignote pendant la grace."""
    return temps_actif_ms() < getattr(f, "_grace", 0)


def armure_lourde(f):
    """SUPER ARMOR : pendant une attaque LOURDE, un coup encaisse ne l'interrompt PAS
    (degats subis, mais ni stun ni recul : l'attaque CONTINUE). Declaration par perso :
    config["armure"] = types d'attaque (persos config-driven), ARMURE_ARMES (Konrad,
    armes lourdes), finisher ATTACK3 (Stormr)."""
    if not getattr(f, "attacking", False):
        return False
    cfg = getattr(f, "config", None)
    if cfg is not None:
        return getattr(f, "attack_type", 0) in cfg.get("armure", ())
    if getattr(f, "current_weapon", None) is not None:
        return f.current_weapon in getattr(f, "ARMURE_ARMES", ())
    a3 = getattr(f, "ATTACK3", None)
    return a3 is not None and getattr(f, "attack_action", None) == a3


def parade_parfaite(cible):
    """True si la garde est levee PILE a la frame de l'impact (block_age <= PARADE_FENETRE,
    = 0 -> frame perfect pur) = PARADE PARFAITE (voir riposte_parade)."""
    return getattr(cible, "block_age", 999) <= PARADE_FENETRE


def riposte_parade(attaquant, cible):
    """Resolution d'une PARADE PARFAITE : la cible ne subit RIEN (ni chip ni usure) ;
    l'attaquant est REPOUSSE et sa recuperation rallongee (blocked_hit) -> vraie fenetre
    de contre-attaque. _fx_parade signale le flash/son a la boucle de jeu."""
    attaquant.damage_dealt = True
    attaquant.blocked_hit = True
    dirn = 1 if attaquant.rect.centerx >= cible.rect.centerx else -1
    attaquant.rect.x += dirn * 90
    attaquant.rect.clamp_ip(pygame.Rect(0, -600, 1600, 2600))
    cible._fx_parade = temps_ms()


def poussee(cible, depuis_x, degats):
    """RECUL D'IMPACT : un coup non bloque repousse la cible (force selon les degats) ->
    le spammeur doit se rapprocher entre 2 coups, les combats respirent et deviennent
    spatiaux. Pas de recul si ARMURE lourde (l'attaque continue) ni si la cible est
    declaree non poussable (mannequin d'entrainement, fixe par design)."""
    if armure_lourde(cible) or not getattr(cible, "POUSSABLE", True):
        return
    anc = getattr(cible, "ancrage_actif", None)
    if anc is not None and anc():
        cible._ancrage_nb = getattr(cible, "_ancrage_nb", 0) + 1   # compte (Moves Guide)
        return                                     # ANCRAGE (Lysandra) : pas un pouce de recul
    force = min(140, 25 + 2.2 * degats)
    dirn = 1 if cible.rect.centerx >= depuis_x else -1
    cible.rect.x += int(dirn * force)
    cible.rect.clamp_ip(pygame.Rect(0, -600, 1600, 2600))


def bouclier_tick(f):
    """Vie du BOUCLIER, appelee chaque frame par les mvmt : (1) l'usure se RESORBE hors
    blocage (le bouclier 'respire' -> outil durable, plus un consommable fragile) ;
    (2) usure pleine (>= BOUCLIER_MAX) = garde BRISEE : recharge longue ET le defenseur est SONNE
    (take_hit) -> celui qui a casse la garde gagne une vraie fenetre d'attaque."""
    if f.block_health > 0 and not getattr(f, "_block_prev", False):
        f.block_health = max(0.0, f.block_health - REGEN_BOUCLIER)
    if f.block_health >= BOUCLIER_MAX:
        f.block_health = 0
        f.block_cd = 120
        f.hit = True                       # garde brisee -> sonne


# ======================================================================
#  FILTRE "BLEU NUIT" DU COMBAT
#  La map est en bleu nuit (forteresse de Grimgate) ; on assombrit/bleute
#  UNIQUEMENT les sprites des persos et leurs effets (bouclier, lance,
#  poussiere) pour les fondre dedans. Les UI (noms, indicateurs, barres de
#  CD) ne passent PAS par ce filtre. Exception : les ECLAIRS de Stormr
#  (slashs cyan + foudre) restent a pleine luminosite (ils eclairent).
# ======================================================================
FILTRE_NUIT = True
TEINTE_NUIT_DEFAUT = (118, 140, 198)   # teinte par defaut (chapelle : bleu nuit, R<G<B)
TEINTE_NUIT = TEINTE_NUIT_DEFAUT       # teinte ACTIVE (reglee par map via set_filtre_nuit)
HALO_STORMR = True                     # halo lumineux de Stormr (separable du tint, par map)
SEUIL_HALO = 9000               # nb de pixels d'eclair pour un halo a pleine intensite
ALPHA_BOLT = 120                # mi-intensite des bolts blancs (0=tint complet ... 255=plein) :
#   les bolts ressortent sans etre crus, et le leak cheveux/cape (meme blanc) reste discret.


def assombrir_nuit(img):
    """Copie assombrie/bleutee de l'image (filtre nuit). NON cachee -> reservee aux
    surfaces ephemeres (poussiere redimensionnee chaque frame). Pour les frames
    FIXES (sprites/boucliers/lance), utiliser sprite_nuit (cachee)."""
    if not FILTRE_NUIT:
        return img
    t = img.copy()
    t.fill(TEINTE_NUIT + (255,), special_flags=pygame.BLEND_RGBA_MULT)
    return t


_TINT_CACHE = {}   # frame FIXE -> sa version teintee (filtre nuit) : copy+fill 1 seule fois


def sprite_nuit(base):
    """Version teintee d'une frame FIXE, MISE EN CACHE. Le copy+fill (lourd) n'est
    fait qu'une fois par frame distincte, plus a chaque image -> supprime le lag."""
    if not FILTRE_NUIT:
        return base
    t = _TINT_CACHE.get(base)
    if t is None:
        t = base.copy()
        t.fill(TEINTE_NUIT + (255,), special_flags=pygame.BLEND_RGBA_MULT)
        _TINT_CACHE[base] = t
    return t


def _masque_eclair(img):
    """Renvoie (cyan, blanc) : masques numpy des pixels d'eclair. cyan = croissant /
    lame (pleine lumiere) ; blanc = arcs blancs vifs (bolts MAIS aussi, meme couleur,
    quelques highlights de cheveux/cape - inseparables). None si numpy absent."""
    if _np is None:
        return None
    try:
        px = pygame.surfarray.pixels3d(img)
        R = px[:, :, 0].astype(_np.int16)
        G = px[:, :, 1].astype(_np.int16)
        B = px[:, :, 2].astype(_np.int16)
        cyan = (B >= 165) & (B >= R + 25) & (G >= 100)
        blanc = (R >= 228) & (G >= 236) & (B >= 243) & ~cyan
        del px
        return cyan, blanc
    except Exception:
        return None


_ECLAIR_CACHE = {}   # frame d'origine -> (surface 'eclairs seuls', nb pixels) : calcule 1x


def eclair_overlay(base):
    """(surface ne gardant que les eclairs, count). Calcule 1x par frame puis cache.
    Le CYAN (slash/lame) est garde a PLEINE lumiere ; le BLANC (bolts + leak inevitable
    cheveux/cape) a MI-INTENSITE (alpha ALPHA_BOLT) -> bolts visibles, leak discret.
    Sans filtre nuit (maps non nocturnes) : pas de surbrillance ni de halo (les eclairs
    sont deja dans le sprite a leur luminosite normale)."""
    if not FILTRE_NUIT:
        return None, 0
    cache = _ECLAIR_CACHE.get(base)
    if cache is None:
        m = _masque_eclair(base)
        if m is None:
            cache = (None, 0)
        else:
            cyan, blanc = m
            try:
                ecl = base.copy()
                a = pygame.surfarray.pixels_alpha(ecl)
                a[blanc] = ALPHA_BOLT      # bolts (et cheveux/cape) attenues
                a[cyan] = 255              # slash / lame pleine lumiere
                a[~(cyan | blanc)] = 0     # le reste du corps : transparent (sprite teinte dessous)
                del a
                cache = (ecl, int(cyan.sum()) + int(blanc.sum()))
            except Exception:
                cache = (None, 0)
        _ECLAIR_CACHE[base] = cache
    return cache


def set_filtre_nuit(actif, teinte=None, halo=True):
    """Active/desactive le filtre nuit (tint) + le halo de Stormr. A regler PAR MAP.
    'teinte' = couleur de multiplication propre a la map (None -> bleu chapelle par defaut).
    'halo' = le halo lumineux de Stormr (separe du tint : une map peut avoir le tint SANS le
    halo ; ses slashs restent surbrillants et ses particules ne sont pas teintees dans tous
    les cas). Le cache de teinte est vide au debut du round -> la teinte s'applique au combat."""
    global FILTRE_NUIT, TEINTE_NUIT, HALO_STORMR
    FILTRE_NUIT = bool(actif)
    TEINTE_NUIT = tuple(teinte) if teinte else TEINTE_NUIT_DEFAUT
    HALO_STORMR = bool(halo)


# ======================================================================
#  ECHELLE DES PERSOS + NIVEAU DU SOL (par MAP)
#  Les persos peuvent etre plus gros/petits selon la map. L'echelle k touche
#  TOUT le spatial : sprites, rect, offsets, hitbox d'attaque, vitesses, et
#  les sprites/constantes annexes (lance d'Arinya, eclair de Stormr...). Les
#  effets qui derivent du rect (sparks/foudre de Stormr, poussiere) suivent
#  automatiquement. f.echelle est lu dans draw/mvmt/check_collision.
# ======================================================================
SOL = 850   # niveau du sol en combat (reglé par map via set_sol)

# constantes de LONGUEUR/VITESSE a mettre a l'echelle par instance (Arinya, Stormr)
_CONST_ECHELLE = ("SPEAR_HALF", "SPEAR_LEN", "SPEAR_THICK", "SPEAR_PLANT_DEPTH",
                  "SPEAR_BOX_W", "SPEAR_BOX_H", "SPEAR_G", "DASH_SPEED", "LIGHTNING_W")


def set_sol(y):
    """Regle le niveau du sol du combat (par map)."""
    global SOL
    SOL = int(y)


def mettre_a_echelle(f, k):
    """Met un combattant a l'echelle k (sprites, rect ancre aux pieds, sprites annexes,
    constantes de longueur/vitesse). L'offset, les vitesses de deplacement et les hitbox
    d'attaque sont mis a l'echelle via f.echelle (lu dans draw/mvmt/check_collision).
    Le sol vient de SOL. Appeler une fois apres la creation du combattant."""
    f.echelle = k
    if hasattr(f, "GROUND"):
        f.GROUND = SOL                       # Arinya : sol par instance
    if k == 1.0:
        return

    def sc(img):
        return pygame.transform.scale(
            img, (max(1, round(img.get_width() * k)), max(1, round(img.get_height() * k))))

    f.animation_list = [[sc(fr) for fr in anim] for anim in f.animation_list]
    f.image = f.animation_list[f.action][f.frame_index]
    cx, by = f.rect.centerx, f.rect.bottom   # ancrage : pieds au sol
    f.rect.width = round(f.rect.width * k)
    f.rect.height = round(f.rect.height * k)
    f.rect.centerx = cx
    f.rect.bottom = by
    for attr in ("shield_sprite0", "shield_sprite1", "shield_sprite2", "spear_png"):
        s = getattr(f, attr, None)
        if isinstance(s, pygame.Surface):
            setattr(f, attr, sc(s))
    if hasattr(f, "spear_fly"):
        f.spear_fly = [sc(s) for s in f.spear_fly]
    for nom in _CONST_ECHELLE:               # longueurs/vitesses (Arinya, Stormr)
        if hasattr(f, nom):
            setattr(f, nom, getattr(f, nom) * k)


def reset_caches_combat():
    """A appeler au debut d'un round : borne la memoire des caches (teinte + eclairs)
    aux 2 combattants en cours. Le recalcul (lazy) est masque par la brume de transition."""
    _TINT_CACHE.clear()
    _ECLAIR_CACHE.clear()


# Halo lumineux ajoute autour d'un perso pendant ses attaques (flash d'eclair
# cyan-blanc de Stormr, glow DORE d'Oswald...). Pre-genere PAR COULEUR a
# plusieurs intensites (8 paliers) pour ne PAS refaire copy+fill a chaque
# frame -> 1 blit additif/frame cote appelant.
HALO_RAYON = 560
_HALO_VARIANTES = {}                    # {couleur: [8 paliers d'intensite]}


def halo_variante(force, couleur=(150, 215, 255)):
    """Renvoie la variante de halo (pre-generee) la plus proche de l'intensite
    'force' (0..1), dans la couleur demandee (defaut : cyan-blanc de Stormr)."""
    if couleur not in _HALO_VARIANTES:
        base = pygame.Surface((2 * HALO_RAYON, 2 * HALO_RAYON), pygame.SRCALPHA)
        for r in range(HALO_RAYON, 0, -1):
            f = (1 - r / HALO_RAYON) ** 1.7
            col = (int(couleur[0] * f), int(couleur[1] * f), int(couleur[2] * f))
            pygame.draw.circle(base, col, (HALO_RAYON, HALO_RAYON), r)
        variantes = []
        for i in range(1, 9):                          # 8 paliers d'intensite
            v = base.copy()
            k = int(255 * i / 8)
            v.fill((k, k, k, 255), special_flags=pygame.BLEND_RGBA_MULT)
            variantes.append(v)
        _HALO_VARIANTES[couleur] = variantes
    idx = max(0, min(7, int(round(force * 8)) - 1))
    return _HALO_VARIANTES[couleur][idx]

# ======================================================================
#  ARCHITECTURE DES PERSONNAGES
# ----------------------------------------------------------------------
#  Tous les persos "classiques" (qui marchent, sautent, bloquent et ont
#  2 attaques au corps a corps) partagent la meme logique : elle vit dans
#  la classe de base "Fighter".
#
#  Chaque perso ne declare QUE ce qui le rend different, dans un
#  dictionnaire CONFIG (sprite, stats, touches, hitbox d'attaque...).
#
#  -> Ajouter un perso classique = creer une sous-classe avec son CONFIG.
#  -> Un perso TRES different (ex: lancer de lance a recuperer) peut
#     heriter de Fighter et surcharger les methodes concernees, ou
#     ajouter ses propres attributs/objets.
# ======================================================================


class Button:
    """Bouton de menu 'plaque de fer abimee' (palette chaude de l'epee du logo).
    Au survol : la plaque s'eclaircit, bordure + texte virent a la braise."""

    _polices = {}   # cache des polices Old London par taille
    son_hover = None   # callbacks SFX (branches par TSOG Game.py) : hover / clic
    son_click = None

    @staticmethod
    def _police(taille):
        taille = max(10, int(taille))
        if taille not in Button._polices:
            Button._polices[taille] = pygame.font.Font("assets/fonts/OldLondon.ttf", taille)
        return Button._polices[taille]

    def __init__(self, x, y, text, width=400, height=80):
        self.rect = pygame.Rect(x, y, width, height)
        self.text = text
        # Police calee sur la HAUTEUR (plus le bouton est grand, plus le texte est
        # imposant), puis reduite si le texte deborde en largeur.
        taille = int(height * 0.72)
        f = Button._police(taille)
        while taille > 16 and f.size(text)[0] > width - 46:
            taille -= 2
            f = Button._police(taille)
        self.font_menu = f
        self.is_hovered = False
        self.muet = False   # True -> ce bouton ne joue pas les SFX hover/clic

    def draw(self, surface):
        r = self.rect
        x, y, w, h = r.x, r.y, r.width, r.height
        hover = self.is_hovered
        base = (140, 129, 110) if hover else (110, 102, 87)            # fer chaud
        clair = (min(255, base[0] + 34), min(255, base[1] + 32), min(255, base[2] + 28))
        fonce = (int(base[0] * 0.6), int(base[1] * 0.6), int(base[2] * 0.6))
        pygame.draw.rect(surface, (15, 14, 12), r.inflate(8, 8), 0, 7)  # ombre / cadre
        pygame.draw.rect(surface, base, r, 0, 6)                        # plaque
        pygame.draw.rect(surface, clair, (x + 4, y + 3, w - 8, 6), 0, 4)        # reflet haut
        pygame.draw.rect(surface, fonce, (x + 4, y + h - 9, w - 8, 6), 0, 4)    # ombre bas
        bord = (200, 124, 50) if hover else (171, 156, 134)            # braise au survol
        pygame.draw.rect(surface, (26, 23, 18), r, 3, 6)
        pygame.draw.rect(surface, bord, r, 2, 6)
        for rx, ry in ((x + 14, y + 14), (x + w - 14, y + 14),
                       (x + 14, y + h - 14), (x + w - 14, y + h - 14)):  # rivets
            pygame.draw.circle(surface, (70, 63, 52), (rx, ry), 4)
            pygame.draw.circle(surface, (176, 162, 138), (rx, ry), 4, 1)
        coul = (236, 218, 180) if hover else (214, 202, 176)           # texte grave
        ombre = self.font_menu.render(self.text, True, (26, 22, 16))
        haut = self.font_menu.render(self.text, True, coul)
        # Centrage OPTIQUE : boite d'ENCRE des lettres, pas la surface (Old London
        # a beaucoup de vide au-dessus des glyphes -> sinon le texte tombe trop bas).
        bb = haut.get_bounding_rect()
        px = r.centerx - bb.w // 2 - bb.x
        py = r.centery - bb.h // 2 - bb.y
        surface.blit(ombre, (px + 2, py + 3))
        surface.blit(haut, (px, py))

    def check_hover(self, mouse_pos):
        avant = self.is_hovered
        self.is_hovered = self.rect.collidepoint(mouse_pos)
        if self.is_hovered and not avant and Button.son_hover and not self.muet:  # front montant
            Button.son_hover()

    def is_clicked(self, mouse_pos, mouse_clicked):
        clique = self.rect.collidepoint(mouse_pos) and mouse_clicked
        if clique and Button.son_click and not self.muet:
            Button.son_click()
        return clique

#----------------------------------------------------------------------------------------------------------------------

class Fighter:
    """Classe de base commune a tous les combattants classiques.

    Le comportement (deplacement, gravite, saut, blocage, animation,
    collisions d'attaque) est identique pour tous. Les seules choses qui
    changent d'un perso a l'autre sont lues depuis self.CONFIG, defini par
    chaque sous-classe.

    Format d'une hitbox d'attaque : (dx, dy, largeur, hauteur)
      - dx : decalage horizontal par rapport au centre du perso (centerx)
      - dy : decalage vertical par rapport au haut du perso (rect.y)
      - hauteur = None  ->  prend toute la hauteur du perso (rect.height)
    Les listes "hitboxes_right"/"hitboxes_left" permettent des hitbox
    differentes selon le sens (utile quand le sprite n'est pas symetrique).
    """

    CONFIG = None  # a definir dans chaque sous-classe

    def __init__(self, side, flip_state):
        """Initialise le combattant a partir de son CONFIG."""
        c = self.CONFIG
        self.config = c
        self.actions = c["actions"]  # nom d'action -> index dans la sprite sheet

        sprite_sheet = pygame.image.load(c["sprite_sheet"]).convert_alpha()
        animation_steps = c["animation_steps"]

        # Position de départ selon le côté
        self.side = side
        if self.side == "Left":
            x, y = c["pos_left"]
        elif self.side == "Right":
            x, y = c["pos_right"]
        self.size = c["size"]
        self.image_scale = c["image_scale"]
        self.animation_list = self.load_image(sprite_sheet, animation_steps)
        self.flip = flip_state
        # L'offset depend du sens dans lequel le perso regarde
        self.offset = list(c["offset_face_left"]) if flip_state else list(c["offset_face_right"])
        self.action = 0
        self.frame_index = 0
        self.image = self.animation_list[self.action][self.frame_index]
        self.rect = pygame.Rect((x, y, c["rect_size"][0], c["rect_size"][1]))
        self.update_time = temps_ms()

        # Variables de mouvement
        self.vel_y = 0
        self.jump = False
        self.running = False

        # Variables de combat
        self.attacking = False
        self.attack_type = 0
        self.attack_cd = 0
        self.hit = False
        self.damage_dealt = False
        self.health = c["health"]
        self.max_health = c["health"]
        self.alive = True

        # Variables de blocage
        self.block = False
        self.blocked_hit = False
        self.block_health = 0
        self.block_cd = 0

        # Chargement des sprites de bouclier (communs a tous les persos)
        shield0 = pygame.image.load('assets/fighting assets/block_shield0.png').convert_alpha()
        self.shield_sprite0 = pygame.transform.scale(shield0, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield1 = pygame.image.load('assets/fighting assets/block_shield1.png').convert_alpha()
        self.shield_sprite1 = pygame.transform.scale(shield1, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield2 = pygame.image.load('assets/fighting assets/block_shield2.png').convert_alpha()
        self.shield_sprite2 = pygame.transform.scale(shield2, (self.rect.width * 1.5, self.rect.height * 1.5))
        title = pygame.image.load(c["title"]).convert_alpha()
        self.title = pygame.transform.scale(title, (c["title_size"][0], c["title_size"][1]))

    def load_image(self, sprite_sheet, animation_steps):
        """Découpe la sprite sheet en frames individuelles"""
        animation_list = []
        for y, animation in enumerate(animation_steps):
            frame_list = []
            for x in range(animation):
                frame = sprite_sheet.subsurface(x*self.size, y*self.size, self.size, self.size)
                frame_list.append(pygame.transform.scale(frame, (self.size*self.image_scale, self.size*self.image_scale)))
            animation_list.append(frame_list)
        return animation_list

    def mvmt(self, surface, target):
        """Gère les entrées clavier, la gravité et les déplacements"""
        c = self.config
        e = getattr(self, "echelle", 1.0)      # echelle de la map (zoom coherent)
        speed_base = c["speed_base"] * e
        gravit = c["gravit"] * e
        jump = c["jump"] * e
        dx = 0
        dy = 0
        self.running = False
        self.attack_type = 0
        self.block = False

        # Gérer la destruction du bouclier
        bouclier_tick(self)      # regen de l'usure hors blocage + garde BRISEE = sonne

        # Décrémenter les cooldowns
        if self.attack_cd > 0:
            self.attack_cd -= 1

        # Vitesse réduite en l'air
        if self.jump == True:
            speed = speed_base/1.5
        else:
            speed = speed_base

        # Malus pendant le cooldown du bouclier
        if self.block_cd > 0:
            self.block_cd -= 1
            speed = speed_base/2
            jump = jump/1.5

        # Inputs lus depuis le SNAPSHOT (rempli par le combat : clavier local ou reseau),
        # plus depuis le clavier directement -> base du multijoueur. move1/2 = attaques 1/2.
        inp = getattr(self, "inputs", None) or Inputs()
        BLOCK = inp.block
        L = inp.left
        R = inp.right
        U = inp.up
        A1 = inp.move1
        A2 = inp.move2

        # Plus de contrôle (ex: partie terminee) : on ignore les entrees clavier,
        # mais la gravite et les animations continuent normalement.
        if not getattr(self, "controls_enabled", True):
            BLOCK = L = R = U = A1 = A2 = False

        # ESQUIVE (Haut) : bond arriere INVULNERABLE (remplace le saut vertical inutile).
        demarrer_esquive(self, U, e, target)
        if self.dodging:
            dx = esquive_dx(self, e)
            self.vel_y = 0
            dy = (getattr(self, "GROUND", SOL) - self._dodge_lift) - self.rect.bottom   # petit saut en cloche
            self.jump = self._dodge_lift > 0.5     # anim de saut pendant le bond
            if getattr(self, "_dodge_trav", False):
                self.running = True                # traversee (Kenshi) : DASH rasant -> anim de course
                self.dashing = True                #   + poussiere de dash (blocs dash de TSOG Game)
        # Traiter les inputs seulement si disponible
        elif self.attacking == False and self.alive == True and self.hit == False and self.block == False:

            # Blocage
            if BLOCK and self.jump == False and self.block_cd == 0:
                self.block = True
                speed = 0
                jump = 0

            # Déplacements
            if L:
                dx = -speed
                self.flip = True
                self.offset = list(c["offset_face_left"])
                self.running = True
            if R:
                dx = speed
                self.flip = False
                self.offset = list(c["offset_face_right"])
                self.running = True

            # Appliquer la gravité
            self.vel_y += gravit
            dy += self.vel_y

            # Attaques
            if (A1 or A2) and self.block == False and self.attack_cd == 0:
                dy = 0
                self.attack(surface, target)
                if A1:
                    self.attack_type = 1
                elif A2:
                    self.attack_type = 2

        # Limites de l'écran
        if self.rect.left + dx < 0:
            dx = -self.rect.left
        if self.rect.right + dx > 1600:
            dx = 1600 - self.rect.right
        if self.rect.top + dy < 0:
            dy = -self.rect.top
        if self.rect.bottom + dy > SOL:
            dy = SOL - self.rect.bottom
            self.jump = False

        # Appliquer les mouvements
        self.rect.x += dx
        self.rect.y += dy

    def update(self, surface, target):
        """Met à jour l'animation selon l'état du personnage"""
        if self.hit and (grace_active(self) or armure_lourde(self)):
            self.hit = False               # anti stun-lock / super armor : degats subis, pas de stun
            self._hit_amorti = True        # ... mais les SONS d'impact jouent quand meme (feedback)
        A = self.actions
        # Choisir l'action à afficher (priorité du haut vers le bas)
        if self.health <= 0:
            self.health = 0
            self.alive = False
            self.update_action(A["death"])
        elif self.hit == True:
            self.update_action(A["take_hit"])
        elif self.block == True:
            self.update_action(A["idle"])
        elif self.attacking == True:
            if self.attack_type == 1:
                self.update_action(A["attack1"])
            elif self.attack_type == 2:
                self.update_action(A["attack2"])
        elif self.jump == True:
            self.update_action(A["jump"])
        elif self.running == True:
            self.update_action(A["run"])
        else:
            self.update_action(A["idle"])

        # Avancer l'animation. Cadence PAR ACTION si dispo (self._cd_idx : index d'action
        # -> ms/frame), sinon CONFIG["anim_cd"], sinon 99. Les persos a bcp de frames ont
        # besoin d'un idle LENT mais d'attaques RAPIDES -> cadence differente par action.
        _cd = getattr(self, "_cd_idx", None)
        if _cd is not None and self.action in _cd:
            animation_cd = _cd[self.action]
        else:
            animation_cd = self.config.get("anim_cd", 99)
        animation_cd = self.cadence_anim(animation_cd)   # kits (Flow de Kenshi)
        self.image = self.animation_list[self.action][self.frame_index]
        if temps_ms() - self.update_time > animation_cd:
            self.frame_index += 1
            self.update_time = temps_ms()

        # Vérifier les collisions d'attaque
        if self.attacking:
            self.check_collision(surface, target)

        # Gérer la fin de l'animation
        if self.frame_index >= len(self.animation_list[self.action]):
            if self.alive == False:
                self.frame_index = len(self.animation_list[self.action]) - 1
            else:
                self.frame_index = 0
                # Fin d'attaque 1
                if self.action == A["attack1"]:
                    if self.blocked_hit == True:
                        self.attacking = False
                        self.attack_cd = int(self.config["attack1_cd"]*1.25)
                        self.blocked_hit = False
                    else:
                        self.attacking = False
                        self.attack_cd = self.config["attack1_cd"]
                # Fin d'attaque 2
                elif self.action == A["attack2"]:
                    if self.blocked_hit == True:
                        self.attacking = False
                        self.attack_cd = int(self.config["attack2_cd"]*1.25)
                        self.blocked_hit = False
                    else:
                        self.attacking = False
                        self.attack_cd = self.config["attack2_cd"]
                # Fin de stun
                if self.action == A["take_hit"]:
                    self.hit = False
                    self._grace = temps_actif_ms() + GRACE_MS   # anti stun-lock
                    self.attacking = False
                    self.attack_cd = 15

    def update_action(self, new_action):
        """Change l'action et réinitialise l'animation"""
        if new_action != self.action:
            self.action = new_action
            self.frame_index = 0
            self.update_time = temps_ms()

    def attack(self, surface, target):
        """Lance une attaque"""
        if self.attack_cd == 0:
            self.attacking = True
            self.damage_dealt = False

    # --- Hooks de KIT (surcharges par les persos, ex. Kenshi) ---------------
    def mult_degats(self, target):
        """Multiplicateur applique aux degats de CE perso au moment ou un coup
        connecte (lame affutee, execution...). 1.0 par defaut."""
        return 1.0

    def coup_touche(self, target, bloque):
        """Appele quand un coup de CE perso vient de connecter (bloque ou non).
        Sert aux kits (stacks de Flow, consommation de fenetre...)."""

    def cadence_anim(self, cd):
        """Cadence d'animation effective (ms/frame) - les kits peuvent accelerer
        certaines actions (Flow de Kenshi sur les attaques)."""
        return cd

    def perce_garde(self, target):
        """True si le coup EN COURS ignore TOUTE defense de garde : blocage ET parade
        parfaite (seisme charge de Lysandra -- telegraphie 1,4 s a l'avance, une parade
        frame-perfect serait triviale a placer et tuerait le move). Seule l'ESQUIVE
        reste une reponse."""
        return False

    def check_collision(self, surface, target):
        """Vérifie si l'attaque touche l'adversaire et inflige les dégâts.

        Les hitbox, la frame de déclenchement et les dégâts sont propres à
        chaque attaque et lus depuis le CONFIG du perso.
        """
        A = self.actions
        for atk_type, action_name in ((1, "attack1"), (2, "attack2")):
            atk = self.config["attacks"][atk_type]
            # L'attaque ne touche qu'a une frame precise de son animation
            if self.action == A[action_name] and self.frame_index == atk["frame"]:
                specs = atk["hitboxes_right"] if self.flip == False else atk["hitboxes_left"]

                # Construire les rectangles de hitbox a partir des decalages (a l'echelle)
                e = getattr(self, "echelle", 1.0)
                rects = []
                for (dx, dy, w, h) in specs:
                    height = self.rect.height if h is None else int(h * e)
                    rects.append(pygame.Rect(self.rect.centerx + dx * e, self.rect.y + dy * e,
                                             int(w * e), height))
                for r in rects:
                    debug_box(r, "damage box")
                touche = any(r.colliderect(target.rect) for r in rects)
                if touche and self.damage_dealt == False:
                    damage = atk["damage"] * self.mult_degats(target)   # kits (lame, execution...)
                    block_dmg = atk["block_dmg"]
                    # Le bouclier ne protege que du cote ou le perso regarde.
                    # flip == True -> regarde a gauche, flip == False -> a droite.
                    # Un coup ne peut etre bloque que s'il vient de la face.
                    attaquant_a_gauche = self.rect.centerx < target.rect.centerx
                    bloque_de_face = (attaquant_a_gauche == target.flip)
                    if esquive(target):
                        self.damage_dealt = True        # teleport : le coup RATE (esquive totale)
                    elif getattr(target, "spinning", False):
                        # Cible en TOUPIE = INVULNERABLE ; l'attaquant prend l'effet "coup dans
                        # un bouclier" (blocked_hit -> cd d'attaque rallonge), aucun degat/stun.
                        self.damage_dealt = True
                        self.blocked_hit = True
                    elif (target.block and bloque_de_face and parade_parfaite(target)
                          and not self.perce_garde(target)):
                        riposte_parade(self, target)    # PARADE PARFAITE : rien pour la cible
                    elif target.block == True and bloque_de_face and not self.perce_garde(target):
                        # Coup bloque : on encaisse des degats reduits et le
                        # bouclier se degrade, mais PAS de stun (target.hit) :
                        # l'anim de hit ne se joue pas et le bouclier reste affiche.
                        target.health -= damage*BLOC_CHIP
                        target.block_health += block_dmg*0.9
                        self.damage_dealt = True
                        self.blocked_hit = True
                        self.coup_touche(target, True)
                    else:
                        target.health -= damage
                        target.hit = True
                        self.damage_dealt = True
                        poussee(target, self.rect.centerx, damage)   # recul d'impact
                        self.coup_touche(target, False)

    def title_dest(self):
        """Position (coin haut-gauche) du nom a sa place normale."""
        if self.side == "Left":
            return (30, 50)
        return (self.config["title_x_right"], 50)

    def draw(self, surface):
        """Affiche le personnage, son bouclier et son nom"""
        debug_box(self.rect, "hitbox")
        debug_cds(surface, self)
        e = getattr(self, "echelle", 1.0)
        img = pygame.transform.flip(sprite_nuit(self.image), self.flip, False)
        if grace_active(self):
            img.set_alpha(130 if (temps_ms() // 66) % 2 else 220)   # grace : le sprite clignote
        surface.blit(img, (self.rect.x - self.offset[0]*e, self.rect.y - self.offset[1]*e))

        # Afficher le nom (sauf s'il est masque pendant l'intro)
        if not getattr(self, "title_hidden", False):
            surface.blit(self.title, self.title_dest())

        # Indicateur de joueur au-dessus de la tete : triangle pointant vers le
        # bas (la tete). Rouge pour le cote gauche, bleu pour le cote droit.
        # Permet de differencier les deux joueurs, surtout en miroir (meme perso).
        indic_color = (220, 40, 40) if self.side == "Left" else (40, 90, 220)
        cx = self.rect.centerx
        top = self.rect.top
        demi_largeur = 18   # demi-largeur de la base du triangle
        hauteur = 26        # hauteur du triangle
        ecart = 14          # espace entre la pointe et la tete
        pointe = (cx, top - ecart)
        base_g = (cx - demi_largeur, top - ecart - hauteur)
        base_d = (cx + demi_largeur, top - ecart - hauteur)
        pygame.draw.polygon(surface, indic_color, [base_g, base_d, pointe])
        pygame.draw.polygon(surface, (255, 255, 255), [base_g, base_d, pointe], 3)

        # Choisir le sprite de bouclier selon son état d'usure. montre = on bloque OU on maintient
        # le bouclier qq frames apres un coup pris DE DOS en gardant (garde_hold) -> pas de retract.
        montre = self.block or temps_ms() < getattr(self, "_garde_hold", 0)
        shield = None
        if montre and self.block_health >= BOUCLIER_MAX*0.66:
            shield = self.shield_sprite2
        elif montre and self.block_health < BOUCLIER_MAX*0.66 and self.block_health >= BOUCLIER_MAX*0.33:
            shield = self.shield_sprite1
        elif montre and self.block_health < BOUCLIER_MAX*0.33:
            shield = self.shield_sprite0

        # Afficher le bouclier centré sur le perso
        if shield is not None:
            shield_img1 = pygame.transform.flip(sprite_nuit(shield), self.flip, False)
            shield_width = shield_img1.get_width()
            shield_height = shield_img1.get_height()
            shield_x = self.rect.centerx - shield_width // 2
            shield_y = self.rect.centery - shield_height // 2
            surface.blit(shield_img1, (shield_x, shield_y))

#----------------------------------------------------------------------------------------------------------------------

class Lysandra(Fighter):
    """Chevalier (Hero Knight) - le JUGGERNAUT : lente, massive, inarretable.
    Kit (aucune nouvelle animation, tout est code) :
      - INEBRANLABLE : super-armure sur TOUS ses coups (config['armure']=(1,2)) -> un
        coup encaisse pendant son attaque fait mal mais ne l'interrompt JAMAIS.
      - MARCHE INARRETABLE : avancer VERS l'ennemi charge son 'poids' (jauge sous sa
        barre de vie) -> prochain coup jusqu'a +35% ; s'arreter/reculer la vide.
      - COUP SISMIQUE : M2 MAINTENU fige la montee d'Attack2 (tremblement) ; relacher
        l'abat, chargee jusqu'a x2.2 -- et A PLEINE CHARGE le coup PERCE LA GARDE.
      - ANCRAGE : en garde ou en charge sismique, AUCUN recul (poussee ignoree).
      - COLERE LENTE : plus ELLE a perdu de vie, plus elle frappe fort (jusqu'a +35%
        sous 20% PV). Multiplicateur total plafonne a MULT_CAP."""

    ICON = "assets/characters/Hero Knight/Icon.png"   # icone 26px pour le menu

    POIDS_MAX_F = 28               # ~0,9 s de marche vers l'ennemi = poids plein (charge VITE)
    POIDS_BONUS = 0.50             # +50% de degats a poids plein (consomme par le coup)
    COLERE_BONUS = 0.35            # +35% au plancher de vie...
    COLERE_HAUT = 0.60             # ... la colere demarre sous 60% PV
    COLERE_BAS = 0.20              # ... et plafonne a 20% PV
    MULT_CAP = 2.75                # garde-fou : cumul poids*colere*seisme plafonne

    CONFIG = {
        "sprite_sheet": "assets/characters/Hero Knight/Sprites/HeroKnight.png",
        "animation_steps": [7, 11, 3, 11, 3, 8, 4, 7],
        # ordre des lignes de la sprite sheet : nom -> index
        "actions": {"attack2": 0, "death": 1, "fall": 2, "idle": 3,
                    "jump": 4, "run": 5, "take_hit": 6, "attack1": 7},
        "size": 180,
        "image_scale": 6,
        "pos_left": (300, 530),
        "pos_right": (1100, 530),
        "rect_size": (200, 320),
        "offset_face_right": [470, 365],
        "offset_face_left": [410, 365],
        "health": 500,
        "speed_base": 20,
        # Lysandra TRES LOURDE : esquive LENTE, invuln TRES serree (3f) -> dur a placer, tres punissable ;
        # elle s'appuiera plutot sur le BOUCLIER. Gros cd.
        "esquive": {"dur": 19, "speed": 14, "hop": 105, "iframes": 3, "cd": 68},
        "gravit": 1.8,
        "jump": 30,
        "attack1_cd": 25,
        "attack2_cd": 15,
        # touches d'attaque par cote (le J1 a 'i' et 'y' inverses vs Kenshi)
        "attack_keys": {"Left": (pygame.K_i, pygame.K_y),
                        "Right": (pygame.K_EXCLAIM, pygame.K_SEMICOLON)},
        "title": "assets/characters/Hero Knight/title.png",
        "title_size": (347.4, 45),
        "title_x_right": 1212.6,
        "attacks": {
            # Equilibrage 2026-07 : atk1 25->22. Lysandra a les PV les + hauts du jeu (500) et
            # aucun outil -> son burst d'ouverture n'a pas a etre le + gros en plus (modele dps~15).
            1: {"frame": 3, "damage": 22, "block_dmg": 22,
                "hitboxes_right": [(0, 0, 220, None), (-138, -80, 358, 80)],
                "hitboxes_left":  [(-220, 0, 220, None), (-220, -80, 358, 80)]},
            2: {"frame": 3, "damage": 17, "block_dmg": 17,
                "hitboxes_right": [(0, 0, 470, None), (-138, -170, 608, 170)],
                "hitboxes_left":  [(-470, 0, 470, None), (-470, -170, 608, 170)]},
        },
        "armure": (1, 2),          # INEBRANLABLE : super-armure sur TOUTES ses attaques
        # COUP SISMIQUE (M2 maintenu) : gel a la frame 2 (juste avant l'impact frame 3),
        # charge jusqu'a charge_ms (x mult_max), perce la garde des perce_seuil de charge,
        # relache force apres auto_ms (anti-statue).
        "seisme": {"gel_frame": 2, "charge_ms": 1400, "auto_ms": 1600,
                   "mult_max": 2.2, "perce_seuil": 0.85},
    }

    def title_dest(self):
        """Nom un cran PLUS BAS (comme Konrad/Kenshi) : la jauge de POIDS vit sous la barre."""
        if self.side == "Left":
            return (30, 74)
        return (self.config["title_x_right"], 74)

    def ancrage_actif(self):
        """ANCRAGE : en garde ou en charge sismique, rien ne la fait reculer."""
        return bool(self.block or getattr(self, "_seisme_gel", False))

    def _en_attack2(self):
        """True pendant TOUTE l'attaque 2. PIEGE : mvmt remet attack_type a 0 des la
        2e frame -> le repere fiable est l'ACTION (attack_type ne sert qu'a la 1re)."""
        return self.attacking and (self.attack_type == 2
                                   or self.action == self.actions["attack2"])

    def mvmt(self, surface, target):
        x0 = self.rect.centerx
        super().mvmt(surface, target)
        # MARCHE INARRETABLE : chaque frame passee a AVANCER vers l'ennemi charge le
        # poids ; reculer/s'arreter le vide (3x plus vite). Il TIENT pendant ses
        # attaques (sinon il s'eventerait avant que le coup parte).
        vers = 1 if target.rect.centerx >= x0 else -1
        avance = (self.rect.centerx - x0) * vers
        if avance > 0 and not self.jump and not self.attacking:
            self.poids = min(self.POIDS_MAX_F, getattr(self, "poids", 0) + 1)
        elif avance <= 0 and not self.attacking and not self.jump:
            self.poids = max(0, getattr(self, "poids", 0) - 3)

    def update(self, surface, target):
        cfgs = self.config["seisme"]
        held = bool(getattr(self, "inputs", None)) and self.inputs.move2
        # INEBRANLABLE : marque l'absorption (Moves Guide) avant que la base efface le hit.
        self._armure_abs = bool(self.hit and self.attacking)
        # --- CHARGE SISMIQUE : Attack2 TENUE -> montee figee, relachee = l'abattee part ---
        if self._en_attack2():
            if not getattr(self, "_seisme_arme", False):
                self._seisme_arme = True          # nouvelle attaque 2 : etat remis a neuf
                self._seisme_gel = False
                self._seisme_lache = False
                self._seisme_t0 = 0
                self._seisme_mult = 1.0
                self._seisme_perce = False
            if (self.frame_index >= cfgs["gel_frame"] and held
                    and not getattr(self, "_seisme_lache", False)):
                if not self._seisme_gel:
                    self._seisme_gel = True
                    self._seisme_t0 = temps_actif_ms()
                if temps_actif_ms() - self._seisme_t0 >= cfgs["auto_ms"]:
                    held = False                  # relache FORCEE (anti-statue)
                else:
                    self.frame_index = cfgs["gel_frame"]
                    self.update_time = temps_ms()  # fige l'avancee de l'animation
            if getattr(self, "_seisme_gel", False) and not held:
                ratio = min(1.0, (temps_actif_ms() - self._seisme_t0) / cfgs["charge_ms"])
                self._seisme_mult = 1.0 + (cfgs["mult_max"] - 1.0) * ratio
                self._seisme_perce = ratio >= cfgs["perce_seuil"]
                self._seisme_gel = False
                self._seisme_lache = True         # plus de re-gel sur CETTE attaque
        else:
            self._seisme_arme = False
        super().update(surface, target)

    def mult_degats(self, target):
        m = getattr(self, "_seisme_mult", 1.0) if self._en_attack2() else 1.0
        m *= 1.0 + self.POIDS_BONUS * (getattr(self, "poids", 0) / self.POIDS_MAX_F)
        ratio = self.health / self.max_health
        colere = self.COLERE_BONUS * max(0.0, min(1.0, (self.COLERE_HAUT - ratio)
                                                  / (self.COLERE_HAUT - self.COLERE_BAS)))
        self._colere_active = colere > 0.01
        m *= 1.0 + colere
        return min(self.MULT_CAP, m)

    def perce_garde(self, target):
        # SEISME a pleine charge : l'onde passe a travers TOUT (garde ET parade parfaite,
        # sinon le move telegraphie serait trivial a contrer). Reponse : l'esquive.
        return self._en_attack2() and getattr(self, "_seisme_perce", False)

    def coup_touche(self, target, bloque):
        self._marche_conso = getattr(self, "poids", 0) / self.POIDS_MAX_F   # pour le guide
        self._seisme_conso = getattr(self, "_seisme_mult", 1.0) if self._en_attack2() else 1.0
        self.poids = 0                            # le poids est CONSOMME par le coup
        self._seisme_mult = 1.0
        self._seisme_perce = False

    def draw(self, surface):
        # Tremblement pendant la charge sismique (le sol gronde sous elle)
        if getattr(self, "_seisme_gel", False):
            dxj = ((temps_ms() // 33) % 3) - 1
            self.rect.x += dxj
            super().draw(surface)
            self.rect.x -= dxj
        else:
            super().draw(surface)
        # Jauge de POIDS : rail translucide + remplissage blanc, sous SA barre de vie.
        total, hh = 184, 9
        bx = 20 if self.side == "Left" else 1180 + 396 - total
        jauge = pygame.Surface((total, hh), pygame.SRCALPHA)
        pygame.draw.rect(jauge, (170, 170, 170, 70), (0, 0, total, hh), 0, 3)
        w = int(total * getattr(self, "poids", 0) / self.POIDS_MAX_F)
        if w > 0:
            pygame.draw.rect(jauge, (246, 244, 238, 255), (0, 0, w, hh), 0, 3)
        pygame.draw.rect(jauge, (30, 27, 22, 160), (0, 0, total, hh), 1, 3)
        surface.blit(jauge, (bx, 52))

#----------------------------------------------------------------------------------------------------------------------

class Kenshi(Fighter):
    """Heros martial (Martial Hero) - GLASS CANNON insaisissable. Tres peu de vie,
    mais un kit de duelliste-executeur (aucune nouvelle animation, tout est code) :
      - PASSE-LAME : de pres, son esquive TRAVERSE l'ennemi (config esquive 'traverse') ;
        toute esquive retombee ouvre une fenetre 'lame affutee' -> prochain coup +25%
        (Premier sang), +50% si l'ennemi a ete traverse (frappe dans le dos).
      - DODGE-CANCEL : Haut pendant une attaque coupe l'attaque en esquive (cd dedie).
      - FLOW : chaque coup PROPRE qui touche = 1 stack (max 3) -> attaques plus rapides
        + esquive qui recharge plus vite ; PERDRE de la vie (meme du chip) remet a zero.
      - EXECUTION : +30% de degats contre un ennemi sous 25% de sa vie max.
    Mecanique d'esquive/cancel implementee dans demarrer_esquive (source unique)."""

    ICON = "assets/characters/Martial Hero/Icon.png"   # icone 26px pour le menu

    LAME_M = {1: 1.25, 2: 1.5}     # Premier sang / Passe-lame (frappe dans le dos)
    EXEC_SEUIL = 0.25              # Execution : cible sous 25% de sa vie max...
    EXEC_M = 1.3                   # ... -> degats x1.3
    FLOW_MAX = 3
    FLOW_ANIM = 0.12               # -12% de ms/frame d'attaque par stack (-36% a 3 : ca se SENT)
    FLOW_GRACE_MS = 4500           # sans attaquer pendant 4,5 s...
    FLOW_DECAY_MS = 1000           # ... le Flow s'evente : -1 stack par seconde

    CONFIG = {
        "sprite_sheet": "assets/characters/Martial Hero/Sprites/MartialHero.png",
        "animation_steps": [8, 2, 8, 4, 6, 6, 6, 2],
        "actions": {"idle": 0, "jump": 1, "run": 2, "take_hit": 3,
                    "attack1": 4, "attack2": 5, "death": 6, "fall": 7},
        "size": 200,
        "image_scale": 6,
        "pos_left": (300, 538),
        "pos_right": (1143, 538),
        "rect_size": (157, 312),
        # offset identique des deux cotes (le perso ne le change pas en courant)
        "offset_face_right": [520, 420],
        "offset_face_left": [520, 420],
        "health": 280,             # GLASS CANNON : la plus petite vie du roster (assumee)
        "speed_base": 25,
        # Kenshi TRES AGILE : esquive VIVE (saut rapide/haut, cd court) ; invuln BREVE au debut (4f
        # sur dur 9) -> majorite du saut vulnerable. Son agilite est dans la VITESSE/cd, pas l'invuln.
        # "traverse" : de pres (<300px monde) l'esquive TRAVERSE l'ennemi (passe-lame) ;
        # "cancel_cd" : dodge-cancel d'attaque, recharge dediee (frames).
        # "trav_speed"/"trav_dur" : la traversee est un dash plus VIF que l'esquive de fuite.
        "esquive": {"dur": 9, "speed": 34, "hop": 150, "iframes": 4, "cd": 36,
                    "traverse": 300, "cancel_cd": 90, "trav_speed": 48, "trav_dur": 7},
        "gravit": 2,
        "jump": 32,
        "attack1_cd": 15,
        "attack2_cd": 28,
        "attack_keys": {"Left": (pygame.K_y, pygame.K_i),
                        "Right": (pygame.K_EXCLAIM, pygame.K_SEMICOLON)},
        "title": "assets/characters/Martial Hero/title.png",
        "title_size": (223.44, 45),
        "title_x_right": 1336.56,
        "attacks": {
            # Attaque 1 : hitbox asymetrique (525 a gauche, 550 a droite)
            1: {"frame": 4, "damage": 18, "block_dmg": 10,
                "hitboxes_right": [(0, 0, 550, None), (-79, -112, 629, 112)],
                "hitboxes_left":  [(-525, 0, 525, None), (-525, -112, 604, 112)]},
            # Attaque 2 : gros degat. Equilibrage : portee inchangee (550), mais
            # degats 29->27 (+ cd 25->28) car son indice etait le plus haut du jeu.
            2: {"frame": 4, "damage": 27, "block_dmg": 20,
                "hitboxes_right": [(0, 0, 550, None)],
                "hitboxes_left":  [(-550, 0, 550, None)]},
        },
    }

    # --- KIT : lame affutee / execution / flow ------------------------------
    def title_dest(self):
        """Nom un cran PLUS BAS (comme Konrad) : la jauge de Flow vit sous la barre de vie."""
        if self.side == "Left":
            return (30, 74)
        return (self.config["title_x_right"], 74)

    def lame_niveau(self):
        """Niveau de la fenetre 'lame affutee' EN COURS (0 = aucune, 1 = apres une
        esquive = Premier sang, 2 = apres avoir TRAVERSE l'ennemi = Passe-lame).
        Fenetre posee par demarrer_esquive, sur l'horloge ACTIVE (figee en hitstop)."""
        if temps_actif_ms() < getattr(self, "_lame_fin", 0):
            return getattr(self, "_lame_niv", 0)
        return 0

    def attack(self, surface, target):
        deja = self.attacking
        super().attack(surface, target)
        if self.attacking and not deja:
            # La fenetre de lame s'applique a l'attaque LANCEE pendant la fenetre :
            # sans ca, la montee de l'anim (~0,5 s avant l'impact) la faisait souvent
            # expirer avant que le coup connecte -> boost jamais ressenti.
            self._lame_atk = self.lame_niveau()
            self._lame_fin = 0                    # fenetre transferee a CETTE attaque
            self._flow_t = temps_actif_ms()       # attaquer ENTRETIENT le Flow

    def mult_degats(self, target):
        m = self.LAME_M.get(getattr(self, "_lame_atk", 0), 1.0)
        self._exec_active = target.health <= self.EXEC_SEUIL * target.max_health
        if self._exec_active:
            m *= self.EXEC_M                      # EXECUTION : achever un ennemi affaibli
        return m

    def coup_touche(self, target, bloque):
        self._lame_derniere = getattr(self, "_lame_atk", 0)   # memorise le coup buff (guide)
        self._lame_atk = 0                        # le buff est CONSOMME par ce coup
        if not bloque:
            self.flow = min(self.FLOW_MAX, getattr(self, "flow", 0) + 1)
            self._flow_t = temps_actif_ms()       # un coup PORTE entretient aussi le Flow

    def cadence_anim(self, cd):
        if self.attacking:                        # FLOW : attaques plus rapides par stack
            return int(cd * (1.0 - self.FLOW_ANIM * getattr(self, "flow", 0)))
        return cd

    def update(self, surface, target):
        # FLOW : perdre de la vie (coup, chip...) remet les stacks a zero.
        hp_avant = getattr(self, "_flow_hp", None)
        if hp_avant is not None and self.health < hp_avant:
            self.flow = 0
        # ... et il S'EVENTE : sans attaquer pendant FLOW_GRACE_MS, -1 stack par
        # FLOW_DECAY_MS (attaquer ou toucher relance le chrono via _flow_t).
        if getattr(self, "flow", 0) > 0:
            t = temps_actif_ms()
            if t - getattr(self, "_flow_t", t) >= self.FLOW_GRACE_MS:
                self.flow -= 1
                self._flow_t = t - self.FLOW_GRACE_MS + self.FLOW_DECAY_MS
        super().update(surface, target)
        self._flow_hp = self.health

    def draw(self, surface):
        # Trainee d'afterimages : pendant l'esquive, ou en mouvement avec du Flow.
        # Les fantomes sont les images DEJA flipees des frames precedentes (aucun
        # cout de scale ; 1 flip supplementaire par frame active seulement).
        trail = getattr(self, "_trail", None)
        if trail is None:
            trail = self._trail = []
        flow = getattr(self, "flow", 0)
        actif = getattr(self, "dodging", False) or (flow > 0 and (self.running or self.attacking))
        if actif:
            e = getattr(self, "echelle", 1.0)
            img = pygame.transform.flip(sprite_nuit(self.image), self.flip, False)
            trail.append([img, (self.rect.x - self.offset[0] * e,
                                self.rect.y - self.offset[1] * e), 3])
        for g in trail:                            # fantomes qui s'estompent (3 frames de vie)
            g[2] -= 1
            g[0].set_alpha(30 * g[2] + 14 * flow)
        trail[:] = [g for g in trail if g[2] > 0][-4:]
        for g in trail:
            surface.blit(g[0], g[1])
        super().draw(surface)
        # Jauge de FLOW : 3 segments SOUS sa barre de vie (gris translucide = vide,
        # blanc opaque = stack). Barres : Left x=20, Right x=1180, largeur 396.
        seg_w, seg_h, gap = 56, 9, 8
        total = 3 * seg_w + 2 * gap
        bx = 20 if self.side == "Left" else 1180 + 396 - total
        jauge = pygame.Surface((total, seg_h), pygame.SRCALPHA)
        for i in range(self.FLOW_MAX):
            x0 = i * (seg_w + gap)
            if i < flow:
                pygame.draw.rect(jauge, (246, 244, 238, 255), (x0, 0, seg_w, seg_h), 0, 3)
            else:
                pygame.draw.rect(jauge, (170, 170, 170, 70), (x0, 0, seg_w, seg_h), 0, 3)
            pygame.draw.rect(jauge, (30, 27, 22, 160), (x0, 0, seg_w, seg_h), 1, 3)
        surface.blit(jauge, (bx, 52))

#----------------------------------------------------------------------------------------------------------------------

class KonradForgeval:
    """Konrad Forgeval - l'ecuyer aux 4 armes.

    Mecanique unique : une seule TOUCHE d'attaque, mais 4 armes entre
    lesquelles on alterne avec une 2e touche. Chaque arme a sa specialite :
      - Rapiere     : rapide, degats faibles
      - Lance       : rapide, tres longue allonge
      - Epee lourde : lente, gros degats
      - Masse       : lente, tres gros degats

    Classe volontairement INDEPENDANTE de Fighter (sa mecanique est trop
    differente), mais elle expose la meme interface que les autres persos
    (mvmt / update / draw / health / max_health / rect / alive ...) pour
    rester compatible avec la boucle de jeu.
    """

    ICON = "assets/characters/Ecuyer/Icon.png"   # icone 26px pour le menu

    # Decoupage de la sprite sheet ecuyer.png : (colonne de depart, nb de frames)
    # par ligne. Les anims ne sont PAS toutes alignees a gauche, d'ou le depart.
    # lignes : 0 idle, 1 rapiere, 2 lance, 3 epee, 4 masse, 5 jump, 6 fall,
    #          7 run, 8 hit, 9 death
    LAYOUT = [(0, 8), (2, 4), (2, 4), (2, 4), (2, 4), (3, 2), (3, 2), (0, 8), (2, 4), (1, 6)]
    IDLE, RAPIERE, LANCE, EPEE, MASSE, JUMP, FALL, RUN, HIT, DEATH = range(10)

    HEALTH = 380    # PV max -- SOURCE UNIQUE (lue par __init__ ET le Custom Dummy du training)
    SWITCH_CD = 36  # cooldown de changement d'arme, en frames (1,2 s a 30 FPS)
    # Konrad LOURD et peu agile : esquive LENTE, invuln tres courte (3f) au debut, cd long -> mauvais
    # esquiveur (il bloque plutot).
    ESQUIVE_PROFIL = {"dur": 15, "speed": 19, "hop": 120, "iframes": 3, "cd": 55}
    ARMURE_ARMES = (3, 4)   # SUPER ARMOR : greatsword + masse ne sont pas interrompues par un coup

    # Fiche de chaque arme :
    #   anim     : ligne d'animation a jouer
    #   cd       : cooldown apres l'attaque (en frames)
    #   anim_cd  : duree d'AFFICHAGE d'une frame d'attaque en ms (plus grand = swing plus lent/long)
    #   frame    : frame d'impact (ou la hitbox est active)
    #   degats   : degats infliges (coup non bloque)
    #   allonge  : portee de la hitbox (px depuis le centre du perso)
    #   hauteur  : hauteur de la hitbox (None = toute la hauteur ; un nombre = bande fine centree)
    #   pierce   : fraction des degats qui passe MALGRE le bouclier (0.1 = 10%)
    #   usure    : usure infligee au bouclier quand le coup est bloque (75 = casse)
    #
    # Roles : Rapiere = rapide/equilibree ; Lance = fine, perce le bouclier, gros degats ;
    #         Epee lourde = enorme degat mais lente + gros cd ; Masse = casse vite le bouclier.
    WEAPONS = {
        1: {"nom": "Rapier",     "anim": RAPIERE, "cd": 6,  "anim_cd": 80,  "frame": 2,
            "degats": 10, "allonge": 360, "hauteur": None, "pierce": 0.10, "usure": 18},
        2: {"nom": "Spear",      "anim": LANCE,   "cd": 20, "anim_cd": 110, "frame": 2,
            "degats": 20, "allonge": 480, "hauteur": 70,   "pierce": 0.45, "usure": 8},
        # allonges CALEES sur l'arc reellement dessine (mesure au pic du slash, f2-f3) :
        # greatsword/masse balaient ~520px devant le perso (la rapiere ~390) -- avant elles
        # avaient quasi la meme allonge que la rapiere, tres en-deca de leur slash visible.
        # hauteur 520 ancree au SOL : leurs slashs overhead culminent ~230px au-dessus de
        # la tete (mesure 147..665 dans la frame) -- le rect seul (290) etait trop bas.
        3: {"nom": "Greatsword", "anim": EPEE,    "cd": 40, "anim_cd": 165, "frame": 3,
            "degats": 38, "allonge": 510, "hauteur": 520, "ancre": "sol", "pierce": 0.12, "usure": 22},
        4: {"nom": "Mace",       "anim": MASSE,   "cd": 30, "anim_cd": 135, "frame": 3,
            "degats": 26, "allonge": 500, "hauteur": 520, "ancre": "sol", "pierce": 0.10, "usure": 40},
    }

    def __init__(self, side, flip_state):
        """Initialise Konrad avec ses 4 armes."""
        sprite_sheet = pygame.image.load("assets/characters/Ecuyer/Sprites/ecuyer.png").convert_alpha()
        self.size = 150
        self.image_scale = 7

        # Position de depart selon le cote
        self.side = side
        if side == "Left":
            x, y = 300, 560
        else:
            x, y = 1100, 560
        self.animation_list = self.load_image(sprite_sheet)
        self.flip = flip_state
        # offset : decalage du sprite par rapport a la hitbox (selon le sens)
        self.offset = [450, 375] if flip_state else [443, 375]   # 375 : pieds PILE au sol (368 = enfonce de 7px)
        self.action = self.IDLE
        self.frame_index = 0
        self.image = self.animation_list[self.action][self.frame_index]
        self.rect = pygame.Rect((x, y, 150, 290))
        self.update_time = temps_ms()

        # Mouvement
        self.vel_y = 0
        self.jump = False
        self.running = False

        # Combat
        self.attacking = False
        self.attack_type = 0       # numero de l'arme avec laquelle on attaque
        self.current_weapon = 1    # arme selectionnee (1..4)
        self.switch_prev = False   # etat precedent de la touche de changement (front montant)
        self.switch_cd = 0         # cooldown restant avant de pouvoir rechanger d'arme
        self.attack_cd = 0
        self.hit = False
        self.damage_dealt = False
        self.health = self.HEALTH
        self.max_health = self.HEALTH
        self.alive = True

        # Blocage (memes regles que les autres persos)
        self.block = False
        self.blocked_hit = False
        self.block_health = 0
        self.block_cd = 0

        # Boucliers (assets communs)
        shield0 = pygame.image.load('assets/fighting assets/block_shield0.png').convert_alpha()
        self.shield_sprite0 = pygame.transform.scale(shield0, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield1 = pygame.image.load('assets/fighting assets/block_shield1.png').convert_alpha()
        self.shield_sprite1 = pygame.transform.scale(shield1, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield2 = pygame.image.load('assets/fighting assets/block_shield2.png').convert_alpha()
        self.shield_sprite2 = pygame.transform.scale(shield2, (self.rect.width * 1.5, self.rect.height * 1.5))
        title = pygame.image.load("assets/characters/Ecuyer/title.png").convert_alpha()
        self.title = pygame.transform.scale(title, (220.45, 45))
        self.title_x_right = 1339.55
        self.weapon_font = pygame.font.Font("assets/fonts/OldLondon.ttf", 40)

    def load_image(self, sprite_sheet):
        """Découpe la sprite sheet en tenant compte de la colonne de depart
        de chaque animation (elles ne sont pas toutes alignees a gauche)."""
        animation_list = []
        for y, (start, count) in enumerate(self.LAYOUT):
            frame_list = []
            for x in range(start, start + count):
                frame = sprite_sheet.subsurface(x*self.size, y*self.size, self.size, self.size)
                frame_list.append(pygame.transform.scale(frame, (self.size*self.image_scale, self.size*self.image_scale)))
            animation_list.append(frame_list)
        return animation_list

    def mvmt(self, surface, target):
        """Entrees clavier, gravite, deplacements, changement d'arme et attaque."""
        e = getattr(self, "echelle", 1.0)      # echelle de la map (zoom coherent)
        speed_base = 22 * e
        gravit = 1.9 * e
        jump = 23 * e   # saut volontairement bas (la hauteur varie avec le carre de cette valeur)
        dx = 0
        dy = 0
        self.running = False
        self.block = False

        # Destruction du bouclier
        bouclier_tick(self)      # regen de l'usure hors blocage + garde BRISEE = sonne

        if self.attack_cd > 0:
            self.attack_cd -= 1
        if self.switch_cd > 0:
            self.switch_cd -= 1

        if self.jump:
            speed = speed_base / 1.5
        else:
            speed = speed_base

        if self.block_cd > 0:
            self.block_cd -= 1
            speed = speed_base / 2
            jump = jump / 1.5

        inp = getattr(self, "inputs", None) or Inputs()   # move1 = attaquer, move2 = changer d'arme
        BLOCK = inp.block; L = inp.left
        R = inp.right; U = inp.up
        ATK = inp.move1; SWITCH = inp.move2

        # Plus de contrôle (ex: partie terminee) : on ignore les entrees clavier
        if not getattr(self, "controls_enabled", True):
            BLOCK = L = R = U = ATK = SWITCH = False

        # Changement d'arme : uniquement sur l'appui (front montant), pas en
        # maintien, si on peut agir ET si le cooldown de changement est ecoule.
        if (SWITCH and not self.switch_prev and not self.attacking and self.alive
                and not self.hit and self.switch_cd == 0):
            self.current_weapon = self.current_weapon % 4 + 1
            self.switch_cd = self.SWITCH_CD
        self.switch_prev = SWITCH

        # ESQUIVE (Haut) : bond arriere INVULNERABLE (remplace le saut vertical inutile).
        demarrer_esquive(self, U, e, target)
        if self.dodging:
            dx = esquive_dx(self, e)
            self.vel_y = 0
            dy = (getattr(self, "GROUND", SOL) - self._dodge_lift) - self.rect.bottom   # petit saut en cloche
            self.jump = self._dodge_lift > 0.5     # anim de saut pendant le bond
            if getattr(self, "_dodge_trav", False):
                self.running = True                # traversee (Kenshi) : DASH rasant -> anim de course
                self.dashing = True                #   + poussiere de dash (blocs dash de TSOG Game)
        # Traiter les inputs seulement si disponible
        elif not self.attacking and self.alive and not self.hit and not self.block:
            if BLOCK and not self.jump and self.block_cd == 0:
                self.block = True
                speed = 0
                jump = 0
            if L:
                dx = -speed
                self.flip = True
                self.offset = [450, 375]
                self.running = True
            if R:
                dx = speed
                self.flip = False
                self.offset = [443, 375]
                self.running = True

            self.vel_y += gravit
            dy += self.vel_y

            # Attaque : une seule touche ; l'arme courante determine tout
            if ATK and not self.block and self.attack_cd == 0:
                dy = 0
                self.attacking = True
                self.damage_dealt = False
                self.attack_type = self.current_weapon

        # Limites de l'ecran
        if self.rect.left + dx < 0:
            dx = -self.rect.left
        if self.rect.right + dx > 1600:
            dx = 1600 - self.rect.right
        if self.rect.top + dy < 0:
            dy = -self.rect.top
        if self.rect.bottom + dy > SOL:
            dy = SOL - self.rect.bottom
            self.jump = False

        self.rect.x += dx
        self.rect.y += dy

    def update(self, surface, target):
        """Met a jour l'animation selon l'etat (priorite du haut vers le bas)."""
        if self.hit and (grace_active(self) or armure_lourde(self)):
            self.hit = False               # anti stun-lock / super armor : degats subis, pas de stun
            self._hit_amorti = True        # ... mais les SONS d'impact jouent quand meme (feedback)
        if self.health <= 0:
            self.health = 0
            self.alive = False
            self.update_action(self.DEATH)
        elif self.hit:
            self.update_action(self.HIT)
        elif self.block:
            self.update_action(self.IDLE)
        elif self.attacking:
            self.update_action(self.WEAPONS[self.attack_type]["anim"])
        elif self.jump:
            self.update_action(self.JUMP)
        elif self.running:
            self.update_action(self.RUN)
        else:
            self.update_action(self.IDLE)

        # Vitesse d'animation : par defaut 99 ms/frame, mais propre a chaque
        # arme pendant son attaque (l'epee lourde a un swing plus lent/long).
        animation_cd = 99
        if self.action in (self.RAPIERE, self.LANCE, self.EPEE, self.MASSE):
            animation_cd = self.WEAPONS[self.action]["anim_cd"]
        self.image = self.animation_list[self.action][self.frame_index]
        if temps_ms() - self.update_time > animation_cd:
            self.frame_index += 1
            self.update_time = temps_ms()

        if self.attacking:
            self.check_collision(surface, target)

        # Fin d'animation
        if self.frame_index >= len(self.animation_list[self.action]):
            if not self.alive:
                self.frame_index = len(self.animation_list[self.action]) - 1
            else:
                self.frame_index = 0
                # Fin d'une attaque (l'action courante est une anim d'arme)
                if self.action in (self.RAPIERE, self.LANCE, self.EPEE, self.MASSE):
                    cd = self.WEAPONS[self.attack_type]["cd"]
                    if self.blocked_hit:
                        self.attack_cd = int(cd * 1.25)
                        self.blocked_hit = False
                    else:
                        self.attack_cd = cd
                    self.attacking = False
                # Fin de stun
                if self.action == self.HIT:
                    self.hit = False
                    self._grace = temps_actif_ms() + GRACE_MS   # anti stun-lock
                    self.attacking = False
                    self.attack_cd = 15

    def update_action(self, new_action):
        """Change l'action et reinitialise l'animation."""
        if new_action != self.action:
            self.action = new_action
            self.frame_index = 0
            self.update_time = temps_ms()

    def check_collision(self, surface, target):
        """Hitbox/degats de l'arme en cours, a sa frame d'impact."""
        w = self.WEAPONS[self.attack_type]
        if self.action == w["anim"] and self.frame_index == w["frame"]:
            e = getattr(self, "echelle", 1.0)
            allonge = w["allonge"] * e
            # Hitbox : pleine hauteur du rect (None), bande CENTREE (nombre), ou bande
            # ANCREE AU SOL ("ancre": "sol") qui monte au-dessus de la tete -- pour les
            # arcs OVERHEAD (greatsword/masse, slash mesure ~520px de haut, bas au sol).
            if w["hauteur"] is None:
                hb_y = self.rect.y
                hb_h = self.rect.height
            else:
                hb_h = int(w["hauteur"] * e)
                if w.get("ancre") == "sol":
                    hb_y = self.rect.bottom - hb_h
                else:
                    hb_y = self.rect.centery - hb_h // 2
            if self.flip == False:
                attacking_rect = pygame.Rect(self.rect.centerx, hb_y, allonge, hb_h)
            else:
                attacking_rect = pygame.Rect(self.rect.centerx - allonge, hb_y, allonge, hb_h)
            debug_box(attacking_rect, "damage box")
            if attacking_rect.colliderect(target.rect) and not self.damage_dealt:
                damage = w["degats"]
                # Le bouclier ne protege que du cote ou la cible regarde
                attaquant_a_gauche = self.rect.centerx < target.rect.centerx
                bloque_de_face = (attaquant_a_gauche == target.flip)
                if esquive(target):
                    self.damage_dealt = True            # teleport : le coup RATE (esquive totale)
                elif getattr(target, "spinning", False):
                    # cible en TOUPIE = INVULNERABLE ; l'attaquant prend l'effet "coup dans un
                    # bouclier" (blocked_hit -> son cd d'attaque rallonge), aucun degat/stun.
                    self.damage_dealt = True
                    self.blocked_hit = True
                elif target.block and bloque_de_face and parade_parfaite(target):
                    riposte_parade(self, target)        # PARADE PARFAITE : rien pour la cible
                elif target.block and bloque_de_face:
                    # pierce = part des degats qui passe quand meme ; usure = casse du bouclier
                    target.health -= damage * w["pierce"]
                    target.block_health += w["usure"]
                    self.damage_dealt = True
                    self.blocked_hit = True
                else:
                    target.health -= damage
                    target.hit = True
                    self.damage_dealt = True
                    poussee(target, self.rect.centerx, damage)       # recul d'impact

    def title_dest(self):
        """Position (coin haut-gauche) du nom a sa place normale (sous la barre de cd)."""
        if self.side == "Left":
            return (30, 74)
        return (self.title_x_right, 74)

    def draw(self, surface):
        """Affiche Konrad, son nom, l'indicateur joueur, l'arme et le bouclier."""
        debug_box(self.rect, "hitbox")
        debug_cds(surface, self)
        e = getattr(self, "echelle", 1.0)
        img = pygame.transform.flip(sprite_nuit(self.image), self.flip, False)
        if grace_active(self):
            img.set_alpha(130 if (temps_ms() // 66) % 2 else 220)   # grace : le sprite clignote
        surface.blit(img, (self.rect.x - self.offset[0]*e, self.rect.y - self.offset[1]*e))

        # Nom du perso (sauf masque pendant l'intro)
        if not getattr(self, "title_hidden", False):
            surface.blit(self.title, self.title_dest())

        # Triangle indicateur de joueur (rouge a gauche, bleu a droite)
        indic_color = (220, 40, 40) if self.side == "Left" else (40, 90, 220)
        cx = self.rect.centerx
        top = self.rect.top
        demi_largeur = 18
        hauteur = 26
        ecart = 14
        pointe = (cx, top - ecart)
        base_g = (cx - demi_largeur, top - ecart - hauteur)
        base_d = (cx + demi_largeur, top - ecart - hauteur)
        pygame.draw.polygon(surface, indic_color, [base_g, base_d, pointe])
        pygame.draw.polygon(surface, (255, 255, 255), [base_g, base_d, pointe], 3)

        # Indicateur de l'arme selectionnee, au-dessus du triangle
        nom = self.WEAPONS[self.current_weapon]["nom"]
        txt = self.weapon_font.render(nom, True, (205, 170, 69))
        txt_rect = txt.get_rect(center=(cx, top - ecart - hauteur - 28))
        surface.blit(txt, txt_rect)

        # Bouclier selon son usure (montre = bloque OU maintenu apres un coup pris de dos en garde)
        montre = self.block or temps_ms() < getattr(self, "_garde_hold", 0)
        shield = None
        if montre and self.block_health >= BOUCLIER_MAX*0.66:
            shield = self.shield_sprite2
        elif montre and BOUCLIER_MAX*0.33 <= self.block_health < BOUCLIER_MAX*0.66:
            shield = self.shield_sprite1
        elif montre and self.block_health < BOUCLIER_MAX*0.33:
            shield = self.shield_sprite0
        if shield is not None:
            simg = pygame.transform.flip(sprite_nuit(shield), self.flip, False)
            sx = self.rect.centerx - simg.get_width() // 2
            sy = self.rect.centery - simg.get_height() // 2
            surface.blit(simg, (sx, sy))

        # Barre de cooldown de changement d'arme, sous la barre de vie (et sous
        # le nom). Elle se remplit pendant le cd : vide = vient de changer,
        # pleine = prete a rechanger.
        bar_x = 20 if self.side == "Left" else 1180   # aligne avec la barre de vie
        bar_y = 54                                    # juste sous la barre de vie (le nom est en dessous)
        bar_w = 396
        bar_h = 14
        ratio = 1 - self.switch_cd / self.SWITCH_CD    # 0 -> 1 au fil du cd
        prete = (self.switch_cd == 0)
        couleur = (120, 200, 110) if prete else (90, 170, 220)  # vert si prete, bleu si en charge
        pygame.draw.rect(surface, (28, 28, 46), (bar_x, bar_y, bar_w, bar_h))
        pygame.draw.rect(surface, couleur, (bar_x, bar_y, bar_w * ratio, bar_h))
        pygame.draw.rect(surface, (205, 170, 69), (bar_x, bar_y, bar_w, bar_h), 2)

#----------------------------------------------------------------------------------------------------------------------

class Arinya:
    """Arinya - la chasseresse a la lance.

    Deux mecaniques uniques :
      1) COMBO au corps a corps : la touche d'attaque lance attack1 (coup
         haut) ; si on re-appuie avec le bon timing (entre 0,3 s et 0,4 s
         apres le 1er appui), attack2 (coup bas) s'enchaine directement.
      2) LANCER DE LANCE charge : maintenir la 2e touche charge une barre ;
         plus on maintient longtemps, plus la lance part loin et fort. Une
         fois lancee, Arinya n'a PLUS de lance : elle utilise ses sprites
         "sans lance", ne peut plus attaquer/lancer, et doit aller RAMASSER
         la lance au sol pour pouvoir recombattre.

    Classe volontairement INDEPENDANTE de Fighter, mais elle expose la meme
    interface (mvmt / update / draw / health / max_health / rect / alive...)
    pour rester compatible avec la boucle de jeu. La lance lancee est geree
    entierement a l'interieur de cette classe (pas de modif de la boucle).

    Decoupage de Huntress.png : les lignes n'ont PAS un pas vertical regulier,
    donc on stocke directement le y de depart de chaque animation (la frame
    fait 150 px, les pieds tombent sur y=97 dans la frame).
    """

    ICON = "assets/characters/Huntress/Icon.png"   # icone 26px pour le menu

    SIZE = 150
    IMAGE_SCALE = 7

    # Index des animations dans self.animation_list
    (IDLE, IDLE_WS, RUN, RUN_WS, ATTACK1, ATTACK2, LANCEMENT,
     JUMP, JUMP_WS, FALL, FALL_WS, TAKE_HIT, TAKE_HIT_WS,
     DEATH, DEATH_WS) = range(15)

    # (x_depart_px, nombre de frames, y de depart dans la sheet).
    # ATTENTION : la sheet CENTRE chaque ligne sur la largeur (8 cases de
    # 150 px). Une ligne a nombre PAIR de frames retombe pile sur la grille
    # de 150 ; une ligne a nombre IMPAIR (attack=5, lancement=7) est decalee
    # d'une DEMI-CASE (75 px). D'ou un x de depart en pixels et non en colonne.
    LAYOUT = [
        (  0, 8,    0),   # IDLE
        (  0, 8,  150),   # IDLE_WS
        (  0, 8,  300),   # RUN
        (  0, 8,  450),   # RUN_WS
        (225, 5,  600),   # ATTACK1 (impair -> +75 ; impact frame 3)
        (225, 5,  750),   # ATTACK2 (impair -> +75 ; impact frame 3)
        ( 75, 7,  900),   # LANCEMENT (impair -> +75 ; lance lachee frame 6, mains vides)
        (450, 2, 1086),   # JUMP
        (450, 2, 1236),   # JUMP_WS
        (450, 2, 1389),   # FALL
        (450, 2, 1539),   # FALL_WS
        (300, 4, 1690),   # TAKE_HIT
        (300, 4, 1840),   # TAKE_HIT_WS
        (  0, 8, 1990),   # DEATH
        (  0, 8, 2140),   # DEATH_WS
    ]

    # Equivalent "sans lance" de chaque anim (pour l'etat sans lance)
    WS = {IDLE: IDLE_WS, RUN: RUN_WS, JUMP: JUMP_WS, FALL: FALL_WS,
          TAKE_HIT: TAKE_HIT_WS, DEATH: DEATH_WS}

    # Lance : 1 png "au sol" + 4 frames "qui vole", chacune 60x20 px dans la
    # sheet (le fer visible ~42x5 est centre dans la boite). Affichee x SPEAR_SCALE.
    SPEAR_PNG_SRC = (570, 1050, 60, 20)
    SPEAR_FLY_SRC = [(480, 1070, 60, 20), (540, 1070, 60, 20),
                     (600, 1070, 60, 20), (660, 1070, 60, 20)]
    SPEAR_SCALE = 7          # = IMAGE_SCALE (meme echelle que le perso)
    SPEAR_BOX_W = 420        # 60 * SPEAR_SCALE
    SPEAR_BOX_H = 140        # 20 * SPEAR_SCALE
    # Hitbox orientee de la lance (suit l'angle de vol)
    SPEAR_LEN = 294          # longueur visible du fer
    SPEAR_HALF = 140         # demi-longueur (centre -> pointe)
    SPEAR_THICK = 16         # epaisseur de la hitbox (fine et precise)
    SPEAR_PLANT_DEPTH = 22   # profondeur d'enfoncement de la pointe dans le sol
    # Cadence de l'animation de vol (4 frames) : depend de la FORCE du lancer
    # (faible force -> rotation tres lente, pleine charge -> rotation tres rapide).
    SPEAR_ANIM_MS_SLOW = 150  # ms/frame a force minimale (tourne tres lentement)
    SPEAR_ANIM_MS_FAST = 10   # ms/frame a pleine charge (avance plusieurs frames/tick)
    GROUND = 850             # niveau du sol (comme la boucle de jeu)

    # Attaques au corps a corps (coups de lance) : hitbox = bande depuis le
    # centre vers l'avant. attack1 = coup leger, attack2 = finisher plus fort.
    # Equilibrage 2026-07 : melee REDUIT (12->10, 18->13). Arinya a un DASH + une lance
    # CHARGEE a distance (10-44) -> son kit est deja riche ; son melee etait le + haut du
    # jeu (dps 20/24.5), incoherent. Cible du modele dps~16.5 (cycles 0.60/0.73 s).
    ATTACKS = {
        1: {"anim": ATTACK1, "frame": 3, "allonge": 330, "damage": 10,
            "block_dmg": 12, "cd": 8},
        2: {"anim": ATTACK2, "frame": 3, "allonge": 350, "damage": 13,
            "block_dmg": 16, "cd": 12},
    }
    HEALTH = 360             # PV max -- SOURCE UNIQUE (lue par __init__ ET le Custom Dummy)
    # Fenetre du combo : 2e appui entre COMBO_MIN et COMBO_MAX apres le 1er.
    COMBO_MIN = 0.30
    COMBO_MAX = 0.42

    # Lancer charge
    CHARGE_MAX = 22          # frames de maintien pour une charge pleine (~0,75 s)
    SPEAR_G = 1.45          # gravite : assez pour retomber avant la bordure malgre la vitesse
    SPEAR_PIERCE = 0.25      # part des degats qui passe si la cible bloque de face
    SPEAR_USURE = 30         # usure infligee au bouclier si bloquee
    SPEAR_PARADE = 0         # FRAME PERFECT : la lance ne REBONDIT que si le bouclier est leve
                             #   EXACTEMENT a la frame de l'impact (block_age == 0) ; sinon bloquee sans renvoi

    # Dash : double-tap d'une fleche (gauche/droite) dans une fenetre precise,
    # avec une barre de recharge avant de pouvoir redasher.
    DASH_WINDOW = 250        # ms max entre les 2 appuis (double-tap)
    DASH_SPEED = 62          # vitesse du dash (px/frame) - rapide
    DASH_DUR = 7             # duree du dash (frames) -> ~434 px (va plus loin)
    DASH_CD = 18             # recharge du dash (frames, ~0,6 s) - tres court
    DASH_CD_HIT = 45         # recharge ALLONGEE quand elle se fait frapper (~1,5 s)
    # Arinya TRES AGILE : esquive VIVE (comme Kenshi), invuln breve 4f au debut.
    ESQUIVE_PROFIL = {"dur": 9, "speed": 34, "hop": 150, "iframes": 4, "cd": 36}

    def __init__(self, side, flip_state):
        sprite_sheet = pygame.image.load(
            "assets/characters/Huntress/Sprites/Huntress.png").convert_alpha()
        self.side = side
        if side == "Left":
            x, y = 300, 560
        else:
            x, y = 1100, 560
        self.animation_list = self.load_image(sprite_sheet)
        # Sprites de la lance (pointent vers la droite), mis a l'echelle
        box = (self.SPEAR_BOX_W, self.SPEAR_BOX_H)
        self.spear_png = pygame.transform.scale(sprite_sheet.subsurface(self.SPEAR_PNG_SRC), box)
        self.spear_fly = [pygame.transform.scale(sprite_sheet.subsurface(s), box)
                          for s in self.SPEAR_FLY_SRC]

        self.flip = flip_state
        # offsets (sprite vs hitbox) selon le sens
        self.offset_right = [474, 389]
        self.offset_left = [446, 389]
        self.offset = list(self.offset_left) if flip_state else list(self.offset_right)
        self.action = self.IDLE
        self.frame_index = 0
        self.image = self.animation_list[self.action][self.frame_index]
        self.rect = pygame.Rect((x, y, 130, 290))
        self.update_time = temps_ms()

        # Mouvement
        self.vel_y = 0
        self.jump = False
        self.running = False

        # Dash (double-tap d'une fleche)
        self.dashing = False
        self.dash_timer = 0
        self.dash_dir = 0
        self.dash_cd = 0
        self.dash_tap_time = 0     # instant du dernier appui directionnel (ms)
        self.dash_tap_dir = 0      # direction du dernier appui (-1 / +1)
        self.L_prev = False        # fronts montants des fleches (pour le double-tap)
        self.R_prev = False
        self.hit_prev = False      # pour detecter le moment ou elle se fait toucher

        # Combat corps a corps + combo
        self.attacking = False
        self.attack_type = 0
        self.attack_cd = 0
        self.damage_dealt = False
        self.atk_prev = False          # front montant de la touche d'attaque
        self.combo_first_time = 0      # instant du 1er appui (ms)
        self.combo_queued = False      # attack2 demande pendant attack1

        # Lance / lancer charge
        self.has_spear = True
        self.charging = False
        self.charge = 0
        self.throw_prev = False        # etat precedent de la touche de lancer
        self.throwing = False
        self.throw_released = False     # la lance a-t-elle deja quitte la main ?
        self.spear = None               # dict de la lance lancee, sinon None

        # Etat general
        self.hit = False
        self.health = self.HEALTH
        self.max_health = self.HEALTH
        self.alive = True

        # Blocage (memes regles que les autres persos)
        self.block = False
        self.blocked_hit = False
        self.block_health = 0
        self.block_cd = 0

        shield0 = pygame.image.load('assets/fighting assets/block_shield0.png').convert_alpha()
        self.shield_sprite0 = pygame.transform.scale(shield0, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield1 = pygame.image.load('assets/fighting assets/block_shield1.png').convert_alpha()
        self.shield_sprite1 = pygame.transform.scale(shield1, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield2 = pygame.image.load('assets/fighting assets/block_shield2.png').convert_alpha()
        self.shield_sprite2 = pygame.transform.scale(shield2, (self.rect.width * 1.5, self.rect.height * 1.5))
        title = pygame.image.load("assets/characters/Huntress/title.png").convert_alpha()
        ratio_t = 45 / title.get_height()
        self.title = pygame.transform.scale(title, (int(title.get_width() * ratio_t), 45))

    def load_image(self, sprite_sheet):
        """Decoupe chaque animation a partir de (x_depart_px, nombre, y_depart).
        Le x est en PIXELS pour gerer le decalage demi-case des lignes a
        nombre impair de frames (voir LAYOUT)."""
        animation_list = []
        for (xstart, count, ystart) in self.LAYOUT:
            frame_list = []
            for i in range(count):
                frame = sprite_sheet.subsurface(xstart + i * self.SIZE, ystart, self.SIZE, self.SIZE)
                frame_list.append(pygame.transform.scale(
                    frame, (self.SIZE * self.IMAGE_SCALE, self.SIZE * self.IMAGE_SCALE)))
            animation_list.append(frame_list)
        return animation_list

    def anim_for(self, base):
        """Renvoie la variante 'sans lance' si Arinya n'a plus sa lance."""
        if not self.has_spear and base in self.WS:
            return self.WS[base]
        return base

    def mvmt(self, surface, target):
        """Entrees clavier, gravite, deplacement, combo et charge du lancer."""
        e = getattr(self, "echelle", 1.0)      # echelle de la map (zoom coherent)
        speed_base = 24 * e        # un peu moins vite que Kenshi (25)
        gravit = 1.9 * e
        jump = 30 * e              # saut un peu plus haut (proche de Kenshi)
        dx = 0
        dy = 0
        self.running = False
        self.block = False

        bouclier_tick(self)      # regen de l'usure hors blocage + garde BRISEE = sonne
        if self.attack_cd > 0:
            self.attack_cd -= 1
        if self.dash_cd > 0:
            self.dash_cd -= 1

        if self.jump:
            speed = speed_base / 1.5
        else:
            speed = speed_base
        if self.block_cd > 0:
            self.block_cd -= 1
            speed = speed_base / 2
            jump = jump / 1.5

        inp = getattr(self, "inputs", None) or Inputs()   # move1 = attaque/combo, move2 = lancer la lance
        BLOCK = inp.block; L = inp.left
        R = inp.right; U = inp.up
        ATK = inp.move1; THROW = inp.move2

        # Plus de contrôle (ex: partie terminee) : on ignore les entrees clavier
        if not getattr(self, "controls_enabled", True):
            BLOCK = L = R = U = ATK = THROW = False

        now = temps_ms()
        now_combo = temps_actif_ms()   # horloge ACTIVE (figee en hitstop) -> fenetre de combo jouable
        peut_agir = self.alive and not self.hit

        # --- Combo corps a corps (sur front montant de ATK) ---
        if ATK and not self.atk_prev and peut_agir and self.has_spear:
            if not self.attacking and self.attack_cd == 0 and not self.charging and not self.throwing:
                # 1er coup
                self.attacking = True
                self.attack_type = 1
                self.frame_index = 0
                self.damage_dealt = False
                self.combo_first_time = now_combo
                self.combo_queued = False
            elif self.attacking and self.attack_type == 1 and not self.combo_queued:
                # 2e appui : valable seulement dans la fenetre de combo
                dt = (now_combo - self.combo_first_time) / 1000.0
                if self.COMBO_MIN <= dt <= self.COMBO_MAX:
                    self.combo_queued = True
        self.atk_prev = ATK

        # --- Lancer de la lance ---
        dispo_lancer = (peut_agir and self.has_spear and not self.attacking
                        and not self.throwing and self.attack_cd == 0)
        if THROW and not self.throw_prev and self.jump and dispo_lancer:
            # En l'air : appui = lancer IMMEDIAT a la force minimale (charge 0)
            self.charge = 0
            self.charging = False
            self.throwing = True
            self.throw_released = False
            self.frame_index = 0
            self.update_time = now
        elif THROW and dispo_lancer and not self.jump:
            # Au sol : maintien = charge (relacher = lancer selon la charge)
            self.charging = True
            self.charge = min(self.CHARGE_MAX, self.charge + 1)
        # Relachement au sol -> lancer selon la charge accumulee
        if self.throw_prev and not THROW and self.charging:
            self.charging = False
            self.throwing = True
            self.throw_released = False
            self.frame_index = 0
            self.update_time = now
        self.throw_prev = THROW

        # --- Dash : double-tap d'une fleche (gauche/droite) dans la fenetre ---
        L_press = L and not self.L_prev
        R_press = R and not self.R_prev
        self.L_prev, self.R_prev = L, R
        if (not self.dashing and self.dash_cd == 0 and peut_agir
                and not self.attacking and not self.throwing and not self.charging):
            for press, d in ((L_press, -1), (R_press, 1)):
                if press:
                    if self.dash_tap_dir == d and (now - self.dash_tap_time) <= self.DASH_WINDOW:
                        # 2e appui rapide dans la meme direction -> DASH
                        self.dashing = True
                        self.dash_timer = self.DASH_DUR
                        self.dash_dir = d
                        self.dash_cd = self.DASH_CD
                        self.dash_tap_dir = 0
                    else:
                        # 1er appui : on memorise direction + instant
                        self.dash_tap_time = now
                        self.dash_tap_dir = d

        # ESQUIVE (Haut) : bond arriere invulnerable (remplace le saut ; le dash avant garde la priorite).
        demarrer_esquive(self, U, e, target)
        if self.dashing:
            # Dash en cours : forte vitesse horizontale, ignore les autres entrees
            dx = self.dash_dir * self.DASH_SPEED
            self.running = True
            if self.dash_dir < 0:
                self.flip = True
                self.offset = list(self.offset_left)
            else:
                self.flip = False
                self.offset = list(self.offset_right)
            self.dash_timer -= 1
            if self.dash_timer <= 0:
                self.dashing = False
        elif self.dodging:
            dx = esquive_dx(self, e)
            self.vel_y = 0
            dy = (getattr(self, "GROUND", SOL) - self._dodge_lift) - self.rect.bottom   # petit saut en cloche
            self.jump = self._dodge_lift > 0.5     # anim de saut pendant le bond
        # Sinon, deplacement normal si disponible
        elif peut_agir and not self.attacking and not self.throwing and not self.charging:
            if BLOCK and not self.jump and self.block_cd == 0:
                self.block = True
                speed = 0
                jump = 0
            if L:
                dx = -speed
                self.flip = True
                self.offset = list(self.offset_left)
                self.running = True
            if R:
                dx = speed
                self.flip = False
                self.offset = list(self.offset_right)
                self.running = True

        # Pendant la CHARGE du lancer : reste plante mais peut se RETOURNER pour
        # viser a gauche/a droite (la lance partira dans le sens regarde).
        if self.charging and peut_agir:
            if L:
                self.flip = True
                self.offset = list(self.offset_left)
            elif R:
                self.flip = False
                self.offset = list(self.offset_right)

        # Gravite (toujours, sauf quand on charge au sol : on reste plante, ou pendant l'esquive
        # dont l'arc du petit saut pilote deja dy).
        if self.dodging:
            pass
        elif not self.charging:
            self.vel_y += gravit
            dy += self.vel_y
        else:
            dx = 0

        # Limites de l'ecran
        if self.rect.left + dx < 0:
            dx = -self.rect.left
        if self.rect.right + dx > 1600:
            dx = 1600 - self.rect.right
        if self.rect.top + dy < 0:
            dy = -self.rect.top
        if self.rect.bottom + dy > self.GROUND:
            dy = self.GROUND - self.rect.bottom
            self.jump = False

        self.rect.x += dx
        self.rect.y += dy

    def update(self, surface, target):
        """Met a jour l'animation, les collisions, le combo et la lance."""
        if self.hit and (grace_active(self) or armure_lourde(self)):
            self.hit = False               # anti stun-lock / super armor : degats subis, pas de stun
            self._hit_amorti = True        # ... mais les SONS d'impact jouent quand meme (feedback)
        # Au moment precis ou elle se fait toucher : ALLONGER la recharge du
        # dash, MAIS seulement si elle se fait punir "en plein usage" du dash,
        # c.-a-d. pendant un dash OU alors que son cd classique n'est pas fini.
        # Si son dash est deja pret, un coup n'allonge rien.
        if self.hit and not self.hit_prev and (self.dashing or self.dash_cd > 0):
            self.dash_cd = max(self.dash_cd, self.DASH_CD_HIT)
        self.hit_prev = self.hit

        # Si elle vient d'etre touchee : annuler charge/throw/attaque/dash en
        # cours. On REMET LA CHARGE A ZERO (sinon la prochaine charge reprendrait
        # la ou elle s'etait arretee au moment du coup).
        if self.hit:
            self.charging = False
            self.charge = 0
            if self.throwing and not self.throw_released:
                self.throwing = False
            self.attacking = False
            self.dashing = False

        # --- Choix de l'action (priorite du haut vers le bas) ---
        if self.health <= 0:
            self.health = 0
            self.alive = False
            self.update_action(self.anim_for(self.DEATH))
        elif self.hit:
            self.update_action(self.anim_for(self.TAKE_HIT))
        elif self.throwing:
            self.update_action(self.LANCEMENT)
        elif self.charging:
            self.update_action(self.LANCEMENT)
        elif self.attacking:
            self.update_action(self.ATTACKS[self.attack_type]["anim"])
        elif self.block:
            self.update_action(self.anim_for(self.IDLE))
        elif self.jump:
            if self.vel_y < 0:
                self.update_action(self.anim_for(self.JUMP))
            else:
                self.update_action(self.anim_for(self.FALL))
        elif self.running:
            self.update_action(self.anim_for(self.RUN))
        else:
            self.update_action(self.anim_for(self.IDLE))

        # Pendant la charge : pose figee (lance armee), pas d'avance d'anim
        if self.charging:
            self.frame_index = 1
            self.image = self.animation_list[self.action][self.frame_index]
            self.update_spear(target)
            return

        # Vitesse d'animation selon l'action (globalement plus rapide)
        if self.action in (self.ATTACK1, self.ATTACK2):
            animation_cd = 58
        elif self.action == self.LANCEMENT:
            animation_cd = 42
        else:
            animation_cd = 80

        self.image = self.animation_list[self.action][self.frame_index]
        # Lacher de la lance quand la frame AFFICHEE est celle des mains vides
        # (frame 6 : la lance a quitte la main). On teste AVANT d'incrementer
        # frame_index, sinon l'image montree au spawn serait encore la frame
        # precedente (lance en main) -> on verrait la lance en main ET lancee.
        if self.throwing and not self.throw_released and self.frame_index >= 6:
            self.lancer_lance()
            self.throw_released = True

        if temps_ms() - self.update_time > animation_cd:
            self.frame_index += 1
            self.update_time = temps_ms()

        # Collisions du corps a corps
        if self.attacking:
            self.check_collision(surface, target)

        # Gestion de la lance lancee (vol, collision, ramassage)
        self.update_spear(target)

        # --- Fin d'animation ---
        if self.frame_index >= len(self.animation_list[self.action]):
            if not self.alive:
                self.frame_index = len(self.animation_list[self.action]) - 1
            else:
                self.frame_index = 0
                if self.action in (self.ATTACK1, self.ATTACK2):
                    if self.action == self.ATTACK1 and self.combo_queued:
                        # Enchainement direct sur attack2
                        self.attack_type = 2
                        self.combo_queued = False
                        self.damage_dealt = False
                        self.action = self.ATTACK2
                        self.update_time = temps_ms()
                    else:
                        cd = self.ATTACKS[self.attack_type]["cd"]
                        if self.blocked_hit:
                            cd = int(cd * 1.25)
                            self.blocked_hit = False
                        self.attacking = False
                        self.attack_cd = cd
                        self.combo_queued = False
                elif self.action == self.LANCEMENT and self.throwing:
                    # Fin du lancer : plus de lance en main
                    self.throwing = False
                    self.charge = 0
                if self.action in (self.TAKE_HIT, self.TAKE_HIT_WS):
                    self.hit = False
                    self._grace = temps_actif_ms() + GRACE_MS   # anti stun-lock
                    self.attacking = False
                    self.attack_cd = 15

    def update_action(self, new_action):
        if new_action != self.action:
            self.action = new_action
            self.frame_index = 0
            self.update_time = temps_ms()

    def check_collision(self, surface, target):
        """Hitbox/degats du coup de lance en cours, a sa frame d'impact."""
        atk = self.ATTACKS[self.attack_type]
        if self.action == atk["anim"] and self.frame_index == atk["frame"]:
            allonge = atk["allonge"] * getattr(self, "echelle", 1.0)
            if self.flip == False:
                attacking_rect = pygame.Rect(self.rect.centerx, self.rect.y, allonge, self.rect.height)
            else:
                attacking_rect = pygame.Rect(self.rect.centerx - allonge, self.rect.y, allonge, self.rect.height)
            debug_box(attacking_rect, "damage box")
            if attacking_rect.colliderect(target.rect) and not self.damage_dealt:
                damage = atk["damage"]
                attaquant_a_gauche = self.rect.centerx < target.rect.centerx
                bloque_de_face = (attaquant_a_gauche == target.flip)
                if esquive(target):
                    self.damage_dealt = True            # teleport : le coup RATE (esquive totale)
                elif getattr(target, "spinning", False):
                    # cible en TOUPIE = INVULNERABLE ; l'attaquant prend l'effet "coup dans un
                    # bouclier" (blocked_hit -> son cd d'attaque rallonge), aucun degat/stun.
                    self.damage_dealt = True
                    self.blocked_hit = True
                elif target.block and bloque_de_face and parade_parfaite(target):
                    riposte_parade(self, target)        # PARADE PARFAITE : rien pour la cible
                elif target.block and bloque_de_face:
                    target.health -= damage * BLOC_CHIP
                    target.block_health += atk["block_dmg"] * 0.9
                    self.damage_dealt = True
                    self.blocked_hit = True
                else:
                    target.health -= damage
                    target.hit = True
                    self.damage_dealt = True
                    poussee(target, self.rect.centerx, damage)       # recul d'impact

    def lancer_lance(self):
        """Cree la lance projectile a partir de la charge accumulee.
        Position/vitesse en CENTRE (la lance tourne autour de son centre)."""
        ratio = self.charge / self.CHARGE_MAX
        direction = -1 if self.flip else 1
        e = getattr(self, "echelle", 1.0)   # vitesses/positions a l'echelle (SPEAR_G aussi)
        # Vitesse : ECART marque selon la force (charge faible = lente, pleine
        # = tres rapide). vx domine -> trajectoire tendue et rapide.
        vx = (6 + 52 * ratio) * direction * e
        vy = (1 - 5 * ratio) * e           # faible charge tombe vite, pleine = a peine montante
        damage = 10 + 34 * ratio        # buff (etait 8 + 32*ratio) -> 10 a 44
        cx = self.rect.centerx + direction * 20 * e
        cy = self.rect.y + 80 * e
        angle = math.degrees(math.atan2(-vy, vx))
        # Cadence d'anim de vol proportionnelle a la force (plus fort = plus vite)
        anim_ms = self.SPEAR_ANIM_MS_SLOW + (self.SPEAR_ANIM_MS_FAST - self.SPEAR_ANIM_MS_SLOW) * ratio
        self.spear = {"cx": cx, "cy": cy, "vx": vx, "vy": vy, "angle": angle,
                      "damage": damage, "grounded": False, "hit_done": False,
                      "bounced": False, "frame": 0, "anim_t": temps_ms(),
                      "anim_ms": anim_ms}
        self.has_spear = False

    def spear_rect(self):
        """Hitbox = boite englobante de la lance orientee selon son angle."""
        s = self.spear
        rad = math.radians(s["angle"])
        w = abs(math.cos(rad)) * self.SPEAR_LEN + self.SPEAR_THICK
        h = abs(math.sin(rad)) * self.SPEAR_LEN + self.SPEAR_THICK
        r = pygame.Rect(0, 0, int(w), int(h))
        r.center = (int(s["cx"]), int(s["cy"]))
        return r

    def planter_lance(self, uy):
        """Fige la lance plantee dans le sol : meme angle de chute, pointe
        enfoncee de SPEAR_PLANT_DEPTH sous le niveau du sol."""
        s = self.spear
        s["grounded"] = True
        s["cy"] = (self.GROUND + self.SPEAR_PLANT_DEPTH) - uy * self.SPEAR_HALF

    def update_spear(self, target):
        """Physique de la lance lancee + collision cible + ramassage au sol.
        La lance s'oriente selon sa vitesse et se plante au sol a son angle."""
        s = self.spear
        if s is None:
            return
        if not s["grounded"]:
            # animation de vol (4 frames) : cadence selon la force du lancer.
            # while (et non if) -> peut avancer PLUSIEURS frames par tick, pour
            # tourner tres vite a pleine force (sinon plafonnee a 1 frame/tick
            # a 30 FPS et l'ecart lent/rapide reste faible).
            now = temps_ms()
            step = max(1, int(s["anim_ms"]))
            while now - s["anim_t"] >= step:
                s["frame"] = (s["frame"] + 1) % 4
                s["anim_t"] += step
            # integration
            s["vy"] += self.SPEAR_G
            s["cx"] += s["vx"]
            s["cy"] += s["vy"]
            # orientation suivant le vecteur vitesse
            speed = math.hypot(s["vx"], s["vy"]) or 1.0
            uy = s["vy"] / speed
            s["angle"] = math.degrees(math.atan2(-s["vy"], s["vx"]))
            srect = self.spear_rect()
            dmg = s["damage"]
            if not s["hit_done"]:                      # lance encore dangereuse -> damage box
                debug_box(srect, "damage box")
            # Phase ALLER : la lance peut toucher la CIBLE (l'adversaire)
            if (not s["hit_done"] and not s["bounced"] and srect.colliderect(target.rect)
                    and not esquive(target)):            # teleport : la lance le TRAVERSE
                attaquant_a_gauche = s["cx"] < target.rect.centerx
                bloque_de_face = (attaquant_a_gauche == target.flip)
                if getattr(target, "spinning", False):
                    s["hit_done"] = True                 # toupie INVULNERABLE : lance consommee, 0 degat
                elif target.block and bloque_de_face:
                    # Bloquee de face : petit chip + usure dans tous les cas.
                    target.health -= dmg * self.SPEAR_PIERCE
                    target.block_health += self.SPEAR_USURE
                    if getattr(target, "block_age", 999) <= self.SPEAR_PARADE:
                        # PARADE : bouclier leve quasi a l'impact -> la lance REBONDIT
                        # (repart en arriere + saut). Devient dangereuse pour le lanceur.
                        speed = math.hypot(s["vx"], s["vy"])
                        s["vx"] = -s["vx"] * 0.5
                        s["vy"] = -speed * 0.3
                        s["bounced"] = True
                    else:
                        # Bouclier deja leve (pas une parade) -> bloquee mais PAS
                        # renvoyee : la lance est consommee (traverse puis tombe).
                        s["hit_done"] = True
                else:
                    target.health -= dmg
                    target.hit = True
                    poussee(target, s["cx"], dmg)      # recul d'impact
                    s["hit_done"] = True   # traverse puis tombe au sol
            # Phase RETOUR (apres rebond) : la lance peut blesser LE LANCEUR
            elif s["bounced"] and srect.colliderect(self.rect):
                a_gauche = s["cx"] < self.rect.centerx
                face = (a_gauche == self.flip)
                if self.block and face:
                    self.health -= dmg * self.SPEAR_PIERCE
                    self.block_health += self.SPEAR_USURE
                else:
                    self.health -= dmg
                    self.hit = True
                s["bounced"] = False
                s["hit_done"] = True   # consommee : ne touche plus personne
            # Bords de l'ecran : stoppe l'horizontale, la lance retombe droit
            if s["cx"] < 60:
                s["cx"] = 60
                s["vx"] = 0
            elif s["cx"] > 1540:
                s["cx"] = 1540
                s["vx"] = 0
            # Plantage quand la pointe atteint le sol
            if s["cy"] + uy * self.SPEAR_HALF >= self.GROUND:
                self.planter_lance(uy)
        else:
            # Au sol : Arinya la ramasse en marchant dessus
            if not self.has_spear and self.alive and self.rect.colliderect(self.spear_rect()):
                self.has_spear = True
                self.spear = None

    def title_dest(self):
        """Position (coin haut-gauche) du nom a sa place normale."""
        if self.side == "Left":
            return (30, 50)
        return (1600 - 30 - self.title.get_width(), 50)

    def combo_timing(self):
        """SOURCE UNIQUE pour le Combo Trainer : le 2e coup se juge en TEMPS depuis le 1er
        appui (combo_first_time), MEMES constantes que mvmt (COMBO_MIN..COMBO_MAX).
        (total, debut, fin EXCLUSIVE, position) en secondes, ou None."""
        if not (self.attacking and getattr(self, "attack_type", 0) == 1) or self.combo_queued:
            return None
        ecoule = (temps_actif_ms() - self.combo_first_time) / 1000.0
        return (self.COMBO_MAX * 1.25, self.COMBO_MIN, self.COMBO_MAX, ecoule)

    def draw(self, surface):
        """Affiche Arinya, son nom, l'indicateur, le bouclier, la barre de
        charge et la lance (en vol ou au sol)."""
        debug_box(self.rect, "hitbox")
        debug_cds(surface, self)
        e = getattr(self, "echelle", 1.0)
        img = pygame.transform.flip(sprite_nuit(self.image), self.flip, False)
        if grace_active(self):
            img.set_alpha(130 if (temps_ms() // 66) % 2 else 220)   # grace : le sprite clignote
        surface.blit(img, (self.rect.x - self.offset[0]*e, self.rect.y - self.offset[1]*e))

        # Nom (sauf masque pendant l'intro)
        if not getattr(self, "title_hidden", False):
            surface.blit(self.title, self.title_dest())

        # Triangle indicateur de joueur (rouge a gauche, bleu a droite)
        indic_color = (220, 40, 40) if self.side == "Left" else (40, 90, 220)
        cx = self.rect.centerx
        top = self.rect.top
        demi_largeur = 18
        hauteur = 26
        ecart = 14
        pointe = (cx, top - ecart)
        base_g = (cx - demi_largeur, top - ecart - hauteur)
        base_d = (cx + demi_largeur, top - ecart - hauteur)
        pygame.draw.polygon(surface, indic_color, [base_g, base_d, pointe])
        pygame.draw.polygon(surface, (255, 255, 255), [base_g, base_d, pointe], 3)

        # Bouclier selon son usure (montre = bloque OU maintenu apres un coup pris de dos en garde)
        montre = self.block or temps_ms() < getattr(self, "_garde_hold", 0)
        shield = None
        if montre and self.block_health >= BOUCLIER_MAX*0.66:
            shield = self.shield_sprite2
        elif montre and BOUCLIER_MAX*0.33 <= self.block_health < BOUCLIER_MAX*0.66:
            shield = self.shield_sprite1
        elif montre and self.block_health < BOUCLIER_MAX*0.33:
            shield = self.shield_sprite0
        if shield is not None:
            simg = pygame.transform.flip(sprite_nuit(shield), self.flip, False)
            sx = self.rect.centerx - simg.get_width() // 2
            sy = self.rect.centery - simg.get_height() // 2
            surface.blit(simg, (sx, sy))

        # Barre de charge du lancer, au-dessus de la tete pendant la charge
        if self.charging:
            bar_w = 120
            bar_h = 12
            bx = cx - bar_w // 2
            by = top - ecart - hauteur - 30
            ratio = self.charge / self.CHARGE_MAX
            # couleur du vert (faible) au rouge (charge pleine)
            couleur = (int(80 + 175 * ratio), int(200 - 120 * ratio), 60)
            pygame.draw.rect(surface, (28, 28, 46), (bx, by, bar_w, bar_h))
            pygame.draw.rect(surface, couleur, (bx, by, int(bar_w * ratio), bar_h))
            pygame.draw.rect(surface, (205, 170, 69), (bx, by, bar_w, bar_h), 2)

        # (recharge du dash visible via le panneau debug_cds, menu Display > Debug)

        # Lance lancee : png plantee au sol, sinon frame de vol ; tournee
        # selon son angle (pique vers le bas quand elle tombe).
        if self.spear is not None:
            s = self.spear
            base = self.spear_png if s["grounded"] else self.spear_fly[s["frame"]]
            rotated = pygame.transform.rotate(sprite_nuit(base), s["angle"])   # base teintee (cachee) puis tournee
            r = rotated.get_rect(center=(int(s["cx"]), int(s["cy"])))
            surface.blit(rotated, r.topleft)


#----------------------------------------------------------------------------------------------------------------------

class Stormr:
    """Stormr - le guerrier nordique a l'epee electrique.

    Mecanique unique : ses coups CHARGENT statiquement l'adversaire (jauge sur la
    CIBLE, affichee par des etincelles ; plus la cible est chargee, plus elles
    crepitent vite). A pleine charge, via un combo au timing precis
    (attack1 -> attack2 -> attack1), Stormr declenche attack3 : la FOUDRE tombe
    du ciel sur la cible (gros burst) si le coup touche, sinon la cible se
    decharge entierement. Peu de degats au corps a corps, peu de PV (glass
    cannon facon Kenshi), mais un enorme burst avec la foudre.

    Classe standalone (comme Arinya/Konrad), expose la meme interface.
    """

    SIZE = 162
    IMAGE_SCALE = 6

    (IDLE, ATTACK1, ATTACK2, ATTACK3, RUN, JUMP, FALL, TAKE_HIT, DEATH) = range(9)
    # Frames (numerotees a partir de 1) ou le HALO d'eclair s'affiche, par attaque.
    HALO_FRAMES = {ATTACK1: (4, 5, 6, 7), ATTACK2: (3, 4, 5), ATTACK3: (5, 6, 7)}
    # (nb de frames, y de depart). Toutes les lignes alignees a gauche, 162px.
    LAYOUT = [
        (10,    0),   # IDLE
        ( 7,  162),   # ATTACK1
        ( 7,  324),   # ATTACK2
        ( 8,  486),   # ATTACK3
        ( 8,  648),   # RUN
        ( 3,  810),   # JUMP
        ( 3,  972),   # FALL
        ( 4, 1134),   # TAKE_HIT (avec la frame blanche)
        ( 7, 1296),   # DEATH
    ]

    ICON = "assets/characters/Nordic/icon.png"

    # Attaques corps a corps : PEU de degats (le burst vient de la foudre).
    # frame = frame d'impact ; allonge = portee depuis le centre.
    # frame = frame d'IMPACT (calee sur le pic visuel du slash). Les slashs
    # BALAIENT des deux cotes : la hitbox a une portee AVANT (sens regarde) et
    # une portee ARRIERE (derriere le perso), mesurees au pixel sur le croissant.
    # attack2 (grand U) frappe loin DERRIERE ; attack3 (descendante) que devant.
    ATTACKS = {
        ATTACK1: {"frame": 4, "avant": 285, "arriere": 115, "damage": 10, "block_dmg": 12, "cd": 10, "anim_cd": 52},
        ATTACK2: {"frame": 2, "avant": 270, "arriere": 360, "damage": 13, "block_dmg": 14, "cd": 12, "anim_cd": 52},
        ATTACK3: {"frame": 4, "avant": 320, "arriere": 0,   "damage": 8,  "block_dmg": 10, "cd": 22, "anim_cd": 56},
    }

    # Fenetres du combo attack1 -> attack2 -> attack1 (secondes, depuis l'appui
    # precedent). RESSERREES (~3 frames) : combo plus dur a enchainer = nerf de
    # son DPS (le burst de foudre est tres punitif).
    HEALTH = 320                          # PV max -- SOURCE UNIQUE (__init__ + Custom Dummy)
    ESQUIVE_PROFIL = None                 # Stormr = agilite NORMALE -> constantes ESQUIVE_* par defaut
    COMBO1_MIN, COMBO1_MAX = 0.30, 0.39   # attack2 apres attack1
    # finisher (M1) apres M2 : la fenetre doit tomber PENDANT attack2 (qui dure ~0,47s apres M2)
    # sinon l'appui arrive quand attack2 est deja finie -> combo injouable. ~3 frames, fin de attack2.
    COMBO2_MIN, COMBO2_MAX = 0.36, 0.46

    # Charge statique infligee a la CIBLE
    CHARGE_MAX = 100
    CHARGE_CHARGED = 90      # seuil "charge a fond" (le finisher reste dispo dans
                            #   cette zone malgre la decroissance lente de la charge)
    CHARGE_A1 = 16           # charge ajoutee par un attack1 qui touche
    CHARGE_A2 = 18           # par un attack2
    CHARGE_DECAY = 0.07      # decroissance lente par frame
    CHARGE_HIT_LOSS = 28     # perte quand la cible frappe Stormr

    # Foudre
    LIGHTNING_DMG = 55       # gros burst
    LIGHTNING_W = 150        # largeur a l'ecran de l'eclair

    def __init__(self, side, flip_state):
        sheet = pygame.image.load("assets/characters/Nordic/Sprites/Nordic.png").convert_alpha()
        self.side = side
        if side == "Left":
            x, y = 300, 580
        else:
            x, y = 1100, 580
        self.animation_list = self.load_image(sheet)

        # Particules electriques (bandes de frames carrees 64px)
        self.light_frames = self._slice_strip("assets/particles/lightning.png")
        self.charging_frames = self._slice_strip("assets/particles/charging static.png")
        self.charged_frames = self._slice_strip("assets/particles/charged static.png")
        self._spark_cache = {}   # (charge_pleine, idx, taille) -> etincelle mise a l'echelle
        self._bolt_cache = {}    # (idx, hauteur) -> eclair recadre + mis a l'echelle

        self.flip = flip_state
        self.offset_right = [450, 336]
        self.offset_left = [402, 336]
        self.offset = list(self.offset_left) if flip_state else list(self.offset_right)
        self.action = self.IDLE
        self.frame_index = 0
        self.image = self.animation_list[self.action][self.frame_index]
        self.rect = pygame.Rect((x, y, 120, 270))
        self.update_time = temps_ms()

        # Mouvement
        self.vel_y = 0
        self.jump = False
        self.running = False

        # Combat / combo
        self.attacking = False
        self.attack_action = self.ATTACK1
        self.attack_cd = 0
        self.damage_dealt = False
        self.a1_prev = False
        self.a2_prev = False
        self.t_atk1 = 0          # instant du dernier appui attack1 (ms)
        self.t_atk2 = 0          # instant du dernier appui attack2 (ms)
        self.combo_step = 0      # 0 rien, 1 apres attack1, 2 apres attack2
        self.combo_queued = None # "atk2" / "atk3" mis en file pour s'enchainer
        self.a3_hit = False      # attack3 a-t-il touche ?

        # Charge statique sur la cible
        self.enemy_charge = 0.0
        self.static_frame = 0.0
        self.target = None       # ref vers l'adversaire (pour dessiner la static)
        self.hit_prev = False

        # Foudre active
        self.lightning = None    # {x, frame, dmg_done}

        # Etat general
        self.hit = False
        self.health = self.HEALTH        # moins de PV qu'un perso classique (facon Kenshi)
        self.max_health = self.HEALTH
        self.alive = True

        # Blocage (memes regles que les autres persos)
        self.block = False
        self.blocked_hit = False
        self.block_health = 0
        self.block_cd = 0
        shield0 = pygame.image.load('assets/fighting assets/block_shield0.png').convert_alpha()
        self.shield_sprite0 = pygame.transform.scale(shield0, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield1 = pygame.image.load('assets/fighting assets/block_shield1.png').convert_alpha()
        self.shield_sprite1 = pygame.transform.scale(shield1, (self.rect.width * 1.5, self.rect.height * 1.5))
        shield2 = pygame.image.load('assets/fighting assets/block_shield2.png').convert_alpha()
        self.shield_sprite2 = pygame.transform.scale(shield2, (self.rect.width * 1.5, self.rect.height * 1.5))
        title = pygame.image.load("assets/characters/Nordic/title.png").convert_alpha()
        ratio_t = 45 / title.get_height()
        self.title = pygame.transform.scale(title, (int(title.get_width() * ratio_t), 45))

    def load_image(self, sheet):
        """Decoupe chaque ligne (nb de frames, y) ; lignes alignees a gauche."""
        animation_list = []
        for (count, ystart) in self.LAYOUT:
            frame_list = []
            for i in range(count):
                frame = sheet.subsurface(i * self.SIZE, ystart, self.SIZE, self.SIZE)
                frame_list.append(pygame.transform.scale(
                    frame, (self.SIZE * self.IMAGE_SCALE, self.SIZE * self.IMAGE_SCALE)))
            animation_list.append(frame_list)
        return animation_list

    def _slice_strip(self, chemin):
        """Bande horizontale de frames carrees -> liste de surfaces."""
        img = pygame.image.load(chemin).convert_alpha()
        h = img.get_height()
        return [img.subsurface(i * h, 0, h, h).copy() for i in range(img.get_width() // h)]

    def title_dest(self):
        if self.side == "Left":
            return (30, 50)
        return (1600 - 30 - self.title.get_width(), 50)

    def mvmt(self, surface, target):
        self.target = target
        e = getattr(self, "echelle", 1.0)      # echelle de la map (zoom coherent)
        speed_base = 24 * e
        gravit = 2.0 * e
        jump = 31 * e
        dx = 0
        dy = 0
        self.running = False
        self.block = False

        bouclier_tick(self)      # regen de l'usure hors blocage + garde BRISEE = sonne
        if self.attack_cd > 0:
            self.attack_cd -= 1

        if self.jump:
            speed = speed_base / 1.5
        else:
            speed = speed_base
        if self.block_cd > 0:
            self.block_cd -= 1
            speed = speed_base / 2
            jump = jump / 1.5

        inp = getattr(self, "inputs", None) or Inputs()   # move1 = attaque 1, move2 = attaque 2 (combo)
        BLOCK = inp.block; L = inp.left
        R = inp.right; U = inp.up
        A1 = inp.move1; A2 = inp.move2

        if not getattr(self, "controls_enabled", True):
            BLOCK = L = R = U = A1 = A2 = False

        now = temps_actif_ms()   # horloge ACTIVE (figee en hitstop) -> fenetres de combo jouables
        peut_agir = self.alive and not self.hit

        a1_press = A1 and not self.a1_prev
        a2_press = A2 and not self.a2_prev
        self.a1_prev, self.a2_prev = A1, A2

        # --- Attaques + combo (les appuis sont bufferises meme pendant un coup) ---
        if peut_agir and not self.block:
            if a1_press:
                if not self.attacking and self.attack_cd == 0:
                    self._start_attack(self.ATTACK1)
                    self.t_atk1 = now
                    self.combo_step = 1
                    self.combo_queued = None
                elif (self.attacking and self.attack_action == self.ATTACK2 and self.combo_step == 2
                      and self.COMBO2_MIN <= (now - self.t_atk2) / 1000.0 <= self.COMBO2_MAX
                      and self.enemy_charge >= self.CHARGE_CHARGED):
                    self.combo_queued = "atk3"     # finisher foudre (cible chargee a fond)
            elif a2_press:
                if not self.attacking and self.attack_cd == 0:
                    self._start_attack(self.ATTACK2)
                    self.combo_step = 0
                elif (self.attacking and self.attack_action == self.ATTACK1 and self.combo_step == 1
                      and self.COMBO1_MIN <= (now - self.t_atk1) / 1000.0 <= self.COMBO1_MAX):
                    self.combo_queued = "atk2"
                    self.t_atk2 = now
                    self.combo_step = 2

        # ESQUIVE (Haut) : bond arriere INVULNERABLE (remplace le saut vertical inutile).
        demarrer_esquive(self, U, e, target)
        if self.dodging:
            dx = esquive_dx(self, e)
            self.vel_y = 0
            dy = (getattr(self, "GROUND", SOL) - self._dodge_lift) - self.rect.bottom   # petit saut en cloche
            self.jump = self._dodge_lift > 0.5     # anim de saut pendant le bond
            if getattr(self, "_dodge_trav", False):
                self.running = True                # traversee (Kenshi) : DASH rasant -> anim de course
                self.dashing = True                #   + poussiere de dash (blocs dash de TSOG Game)
        # --- Deplacement (pas pendant une attaque) ---
        elif peut_agir and not self.attacking and not self.block:
            if BLOCK and not self.jump and self.block_cd == 0:
                self.block = True
                speed = 0
                jump = 0
            if L:
                dx = -speed
                self.flip = True
                self.offset = list(self.offset_left)
                self.running = True
            if R:
                dx = speed
                self.flip = False
                self.offset = list(self.offset_right)
                self.running = True

        if not self.dodging:                     # l'arc du petit saut d'esquive pilote deja dy
            self.vel_y += gravit
            dy += self.vel_y

        if self.rect.left + dx < 0:
            dx = -self.rect.left
        if self.rect.right + dx > 1600:
            dx = 1600 - self.rect.right
        if self.rect.top + dy < 0:
            dy = -self.rect.top
        if self.rect.bottom + dy > SOL:
            dy = SOL - self.rect.bottom
            self.jump = False

        self.rect.x += dx
        self.rect.y += dy

    def _start_attack(self, action):
        self.attacking = True
        self.attack_action = action
        self.frame_index = 0
        self.damage_dealt = False
        self.update_time = temps_ms()
        if action == self.ATTACK3:
            self.a3_hit = False

    def update(self, surface, target):
        self.target = target
        if self.hit and (grace_active(self) or armure_lourde(self)):
            self.hit = False               # anti stun-lock / super armor : degats subis, pas de stun
            self._hit_amorti = True        # ... mais les SONS d'impact jouent quand meme (feedback)

        # Vient de se faire toucher : perd une partie de la charge accumulee sur
        # l'ennemi, et l'attaque en cours est interrompue.
        if self.hit and not self.hit_prev:
            self.enemy_charge = max(0.0, self.enemy_charge - self.CHARGE_HIT_LOSS)
            self.attacking = False
        self.hit_prev = self.hit

        # Decroissance lente de la charge
        if self.enemy_charge > 0:
            self.enemy_charge = max(0.0, self.enemy_charge - self.CHARGE_DECAY)

        # Animation des etincelles : plus la cible est chargee, plus c'est rapide
        ratio = self.enemy_charge / self.CHARGE_MAX
        if self.enemy_charge >= self.CHARGE_CHARGED:
            self.static_frame += 0.6
        elif ratio > 0:
            self.static_frame += 0.12 + 0.38 * ratio
        else:
            self.static_frame = 0.0

        # --- Choix de l'action ---
        if self.health <= 0:
            self.health = 0
            self.alive = False
            self.update_action(self.DEATH)
        elif self.hit:
            self.update_action(self.TAKE_HIT)
        elif self.attacking:
            self.update_action(self.attack_action)
        elif self.block:
            self.update_action(self.IDLE)
        elif self.jump:
            self.update_action(self.JUMP if self.vel_y < 0 else self.FALL)
        elif self.running:
            self.update_action(self.RUN)
        else:
            self.update_action(self.IDLE)

        animation_cd = self.ATTACKS[self.attack_action]["anim_cd"] if self.attacking else 90
        self.image = self.animation_list[self.action][self.frame_index]
        if temps_ms() - self.update_time > animation_cd:
            self.frame_index += 1
            self.update_time = temps_ms()

        if self.attacking:
            self.check_collision(surface, target)
            # VRAI COMBO : une fois la frame d'impact passee, on annule la recovery et
            # on enchaine tout de suite le coup suivant en file -> il touche PENDANT le
            # stun de l'adversaire (avant qu'il sorte du hit et puisse bloquer/esquiver).
            if self.combo_queued and self.frame_index > self.ATTACKS[self.attack_action]["frame"]:
                if self.combo_queued == "atk2":
                    self.combo_queued = None
                    self._start_attack(self.ATTACK2)
                elif self.combo_queued == "atk3":
                    self.combo_queued = None
                    self._start_attack(self.ATTACK3)

        self.maj_lightning(target)

        # --- Fin d'animation ---
        if self.frame_index >= len(self.animation_list[self.action]):
            if not self.alive:
                self.frame_index = len(self.animation_list[self.action]) - 1
            else:
                self.frame_index = 0
                if self.attacking:
                    a = self.attack_action
                    # attack3 qui a rate -> decharge l'ennemi entierement
                    if a == self.ATTACK3 and not self.a3_hit:
                        self.enemy_charge = 0.0
                    # enchainement du combo si un coup est en file
                    if self.combo_queued == "atk2":
                        self.combo_queued = None
                        self._start_attack(self.ATTACK2)
                    elif self.combo_queued == "atk3":
                        self.combo_queued = None
                        self._start_attack(self.ATTACK3)
                    else:
                        cd = self.ATTACKS[a]["cd"]
                        if self.blocked_hit:
                            cd = int(cd * 1.25)
                            self.blocked_hit = False
                        self.attacking = False
                        self.attack_cd = cd
                        self.combo_step = 0
                if self.action == self.TAKE_HIT:
                    self.hit = False
                    self._grace = temps_actif_ms() + GRACE_MS   # anti stun-lock
                    self.attacking = False
                    self.attack_cd = 12

    def update_action(self, new_action):
        if new_action != self.action:
            self.action = new_action
            self.frame_index = 0
            self.update_time = temps_ms()

    def check_collision(self, surface, target):
        atk = self.ATTACKS[self.attack_action]
        if self.action == self.attack_action and self.frame_index == atk["frame"] and not self.damage_dealt:
            # Le slash balaie des deux cotes -> la box couvre avant + arriere.
            e = getattr(self, "echelle", 1.0)
            av = atk["avant"] * e; ar = atk["arriere"] * e
            if self.flip == False:        # face a droite : avant=droite, arriere=gauche
                rect = pygame.Rect(self.rect.centerx - ar, self.rect.y, ar + av, self.rect.height)
            else:                         # face a gauche : avant=gauche, arriere=droite
                rect = pygame.Rect(self.rect.centerx - av, self.rect.y, av + ar, self.rect.height)
            debug_box(rect, "damage box")
            if rect.colliderect(target.rect):
                damage = atk["damage"]
                attaquant_a_gauche = self.rect.centerx < target.rect.centerx
                bloque_de_face = (attaquant_a_gauche == target.flip)
                if esquive(target):
                    self.damage_dealt = True            # teleport : le coup RATE (esquive totale)
                elif getattr(target, "spinning", False):
                    # cible en TOUPIE = INVULNERABLE ; l'attaquant prend l'effet "coup dans un
                    # bouclier" (blocked_hit -> son cd d'attaque rallonge), aucun degat/stun.
                    self.damage_dealt = True
                    self.blocked_hit = True
                elif target.block and bloque_de_face and parade_parfaite(target):
                    riposte_parade(self, target)        # PARADE PARFAITE : rien pour la cible
                elif target.block and bloque_de_face:
                    target.health -= damage * BLOC_CHIP
                    target.block_health += atk["block_dmg"] * 0.9
                    self.damage_dealt = True
                    self.blocked_hit = True
                else:
                    target.health -= damage
                    target.hit = True
                    self.damage_dealt = True
                    poussee(target, self.rect.centerx, damage)       # recul d'impact
                    if self.attack_action == self.ATTACK1:
                        self.enemy_charge = min(self.CHARGE_MAX, self.enemy_charge + self.CHARGE_A1)
                    elif self.attack_action == self.ATTACK2:
                        self.enemy_charge = min(self.CHARGE_MAX, self.enemy_charge + self.CHARGE_A2)
                    elif self.attack_action == self.ATTACK3:
                        # finisher qui touche -> FOUDRE + decharge
                        self.a3_hit = True
                        self.declencher_foudre(target)
                        self.enemy_charge = 0.0
            else:
                self.damage_dealt = True   # la frame d'impact est passee (rate)

    def declencher_foudre(self, target):
        self.lightning = {"x": target.rect.centerx, "frame": 0.0, "dmg_done": False}

    def maj_lightning(self, target):
        if self.lightning is None:
            return
        l = self.lightning
        l["frame"] += 0.5
        if not l["dmg_done"] and l["frame"] >= 2:
            if not getattr(target, "spinning", False) and not esquive(target):   # toupie/teleport
                target.health -= self.LIGHTNING_DMG
                target.hit = True
            l["dmg_done"] = True
        if l["frame"] >= len(self.light_frames):
            self.lightning = None

    def combo_timing(self):
        """SOURCE UNIQUE pour le Combo Trainer : les enchainements de Stormr se jugent en
        TEMPS depuis l'appui du coup precedent (MEMES constantes que mvmt) : atk1->atk2 =
        COMBO1_MIN..MAX depuis t_atk1 ; atk2->finisher = COMBO2_MIN..MAX depuis t_atk2.
        (total, debut, fin EXCLUSIVE, position) en secondes, ou None."""
        if not self.attacking or self.combo_queued:
            return None
        if self.attack_action == self.ATTACK1 and self.combo_step == 1:
            mn, mx, t0 = self.COMBO1_MIN, self.COMBO1_MAX, self.t_atk1
        elif self.attack_action == self.ATTACK2 and self.combo_step == 2:
            mn, mx, t0 = self.COMBO2_MIN, self.COMBO2_MAX, self.t_atk2
        else:
            return None
        return (mx * 1.25, mn, mx, (temps_actif_ms() - t0) / 1000.0)

    def draw(self, surface):
        """Dessine Stormr lui-meme (les effets sur la cible : draw_static_overlay)."""
        debug_box(self.rect, "hitbox")
        debug_cds(surface, self)
        # flip() renvoie toujours une NOUVELLE surface -> on peut compositer les
        # eclairs dessus sans toucher au cache de teinte.
        e = getattr(self, "echelle", 1.0)
        img = pygame.transform.flip(sprite_nuit(self.image), self.flip, False)
        if grace_active(self):
            img.set_alpha(130 if (temps_ms() // 66) % 2 else 220)   # grace : le sprite clignote
        # Pendant une attaque : on rajoute les SLASHS cyan a pleine lumiere (overlay
        # cache, pas de numpy par frame) et on memorise leur quantite (_halo_force).
        self._halo_force = 0.0
        if self.action in (self.ATTACK1, self.ATTACK2, self.ATTACK3):
            ecl, count = eclair_overlay(self.image)
            if ecl is not None:
                img.blit(pygame.transform.flip(ecl, self.flip, False), (0, 0))
            # Halo seulement si active pour la map + sur les frames choisies.
            if HALO_STORMR and (self.frame_index + 1) in self.HALO_FRAMES.get(self.action, ()):
                self._halo_force = min(1.0, count / SEUIL_HALO)
        surface.blit(img, (self.rect.x - self.offset[0]*e, self.rect.y - self.offset[1]*e))

        if not getattr(self, "title_hidden", False):
            surface.blit(self.title, self.title_dest())

        indic_color = (220, 40, 40) if self.side == "Left" else (40, 90, 220)
        cx = self.rect.centerx
        top = self.rect.top
        demi_largeur = 18; hauteur = 26; ecart = 14
        pointe = (cx, top - ecart)
        base_g = (cx - demi_largeur, top - ecart - hauteur)
        base_d = (cx + demi_largeur, top - ecart - hauteur)
        pygame.draw.polygon(surface, indic_color, [base_g, base_d, pointe])
        pygame.draw.polygon(surface, (255, 255, 255), [base_g, base_d, pointe], 3)

        montre = self.block or temps_ms() < getattr(self, "_garde_hold", 0)   # maintien apres coup de dos
        shield = None
        if montre and self.block_health >= BOUCLIER_MAX * 0.66:
            shield = self.shield_sprite2
        elif montre and BOUCLIER_MAX * 0.33 <= self.block_health < BOUCLIER_MAX * 0.66:
            shield = self.shield_sprite1
        elif montre and self.block_health < BOUCLIER_MAX * 0.33:
            shield = self.shield_sprite0
        if shield is not None:
            # teinte d'abord (sprite_nuit cache la frame FIXE) PUIS flip -> pas de copy+fill/frame
            simg = pygame.transform.flip(sprite_nuit(shield), self.flip, False)
            surface.blit(simg, (self.rect.centerx - simg.get_width() // 2,
                                self.rect.centery - simg.get_height() // 2))

    def draw_static_overlay(self, surface):
        """Effets electriques de Stormr SUR LA CIBLE (etincelles + jauge + foudre).
        Appele apres le dessin des deux combattants pour passer par-dessus."""
        # Halo lumineux pendant ses slashs : additif -> il eclaire localement (contre
        # le filtre nuit), pulse avec la quantite d'eclair. Centre BIAISE vers l'avant
        # (sens du slash) pour couvrir aussi l'adversaire, pas seulement Stormr.
        f = getattr(self, "_halo_force", 0.0)
        if f > 0.03:
            halo = halo_variante(f)
            cx = self.rect.centerx + (-1 if self.flip else 1) * 200
            rect = halo.get_rect(center=(cx, self.rect.centery))
            surface.blit(halo, rect.topleft, special_flags=pygame.BLEND_RGB_ADD)

        t = self.target
        # Etincelles + jauge de charge sur la cible
        if t is not None and t.alive and self.enemy_charge > 0:
            ratio = self.enemy_charge / self.CHARGE_MAX
            charge_pleine = self.enemy_charge >= self.CHARGE_CHARGED
            frames = self.charged_frames if charge_pleine else self.charging_frames
            idx = int(self.static_frame) % len(frames)
            taille = int(t.rect.width * 1.6)               # constant (rect.width fixe) -> cachable
            cle = (charge_pleine, idx, taille)
            fr = self._spark_cache.get(cle)
            if fr is None:
                fr = pygame.transform.scale(frames[idx], (taille, taille))
                self._spark_cache[cle] = fr
            surface.blit(fr, (t.rect.centerx - taille // 2, t.rect.centery - taille // 2))
            # jauge au-dessus de la tete de la cible
            bw, bh = 110, 10
            bx = t.rect.centerx - bw // 2
            by = t.rect.top - 54
            couleur = (90, 220, 255) if charge_pleine else (90, 150, 230)
            pygame.draw.rect(surface, (28, 28, 46), (bx, by, bw, bh))
            pygame.draw.rect(surface, couleur, (bx, by, int(bw * ratio), bh))
            pygame.draw.rect(surface, (205, 170, 69), (bx, by, bw, bh), 2)

        # Foudre qui tombe du ciel sur la cible : du HAUT de l'ecran aux PIEDS de la cible.
        if self.lightning is not None:
            l = self.lightning
            idx = min(int(l["frame"]), len(self.light_frames) - 1)
            LH = (t.rect.bottom if t is not None else 850)   # niveau du sol / pieds
            cle = (idx, LH)
            bolt = self._bolt_cache.get(cle)
            if bolt is None:
                src = self.light_frames[idx]
                bb = src.get_bounding_rect()    # retire les marges transparentes haut/bas
                if bb.height >= 2:              # -> l'eclair remplit toute la hauteur
                    src = src.subsurface((0, bb.top, src.get_width(), bb.height))
                bolt = pygame.transform.scale(src, (self.LIGHTNING_W, LH))
                self._bolt_cache[cle] = bolt
            surface.blit(bolt, (int(l["x"] - self.LIGHTNING_W / 2), 0))


#----------------------------------------------------------------------------------------------------------------------
#  PERSOS MULTI-SHEETS : The King (Oswald) et Old Knight of the crown (Barrion).
#  Frames NON carrees, reparties sur PLUSIEURS sprite sheets, anims tres detaillees
#  (bcp de frames). Reutilisent toute la logique de Fighter (mvmt, collisions, block,
#  draw) ; seul le CHARGEMENT change (multi-sheet non carre) + cadence + le nom.
#  [FONDATION] : idle/run/attack1/attack2/take_hit/death OK. Les mecaniques speciales
#  (teleport, combos, spin, jump-attacks, marteau lance, glow) viennent aux etapes 3-4.
#----------------------------------------------------------------------------------------------------------------------

def _pivot_pieds(frame):
    """(centre_x du corps aux pieds, bas) de la silhouette d'une frame, en px.
    Le centre-x est la MEDIANE PONDEREE par le nombre de pixels opaques par colonne, sur
    la bande basse (pieds/jambes) : une arme/chaine FINE tendue ou trainant au sol (peu de
    pixels) ne deplace donc PAS le centre du corps. Renvoie (None, None) si frame vide."""
    bb = frame.get_bounding_rect()
    if bb.width < 2 or bb.height < 2:
        return (None, None)
    h = max(2, int(bb.height * 0.22))                 # ~22% du bas = pieds/jambes
    y0, y1 = bb.bottom - h, bb.bottom
    try:
        a = pygame.surfarray.array_alpha(frame)       # (largeur, hauteur)
        poids = (a[bb.x:bb.x + bb.width, y0:y1] > 40).sum(axis=1)   # nb pixels opaques / colonne
        tot = int(poids.sum())
        if tot <= 0:
            return (float(bb.centerx), float(bb.bottom))
        acc = 0
        cx = bb.centerx
        for i in range(len(poids)):
            acc += int(poids[i])
            if acc * 2 >= tot:                         # mediane ponderee
                cx = bb.x + i
                break
    except Exception:
        cx = bb.centerx
    return (float(cx), float(bb.bottom))


class Fighter2(Fighter):
    """Fighter dont les frames viennent de PLUSIEURS sprite sheets a frames NON carrees.
    CONFIG["sheets"] = liste de {"path","fw","fh","steps":[nb_frames_par_ligne...]}
    concatenees a plat dans animation_list ; CONFIG["actions"] mappe nom -> index a plat ;
    CONFIG["echelle_sprite"] = agrandissement des frames."""

    def __init__(self, side, flip_state):
        c = self.CONFIG
        self.config = c
        self.actions = c["actions"]
        # Cadence par action (ms/frame) : nom d'action -> index -> cd (lu par Fighter.update).
        self._cd_idx = {self.actions[n]: cd for n, cd in c.get("cadences", {}).items()
                        if n in self.actions}
        self.side = side
        x, y = c["pos_left"] if side == "Left" else c["pos_right"]
        self.flip = flip_state
        self.offset = list(c["offset_face_left"]) if flip_state else list(c["offset_face_right"])
        self.animation_list = self._charger_sheets(c["sheets"], c["echelle_sprite"])
        # Suppression de frames parasites : {nom_action: [index, ...]} (indices dans l'ordre
        # d'origine ; supprimes en ordre decroissant pour ne pas decaler les suivants).
        for nom, idxs in c.get("frames_supprimees", {}).items():
            if nom in self.actions:
                anim = self.animation_list[self.actions[nom]]
                for i in sorted(idxs, reverse=True):
                    if 0 <= i < len(anim):
                        del anim[i]
        self._calc_offsets(c["rect_size"][0], c["rect_size"][1])   # alignement pieds par anim
        self.action = self.actions["idle"]
        self.frame_index = 0
        self.image = self.animation_list[self.action][self.frame_index]
        self.rect = pygame.Rect((x, y, c["rect_size"][0], c["rect_size"][1]))
        self.update_time = temps_ms()
        # mouvement
        self.vel_y = 0; self.jump = False; self.running = False
        # combat
        self.attacking = False; self.attack_type = 0; self.attack_cd = 0
        self.hit = False; self.damage_dealt = False
        self.health = c["health"]; self.max_health = c["health"]; self.alive = True
        # combo (config["combo"]) : chaine d'attaques via une sequence de boutons + timing
        self.combo_step = 0        # 0 = pas dans une chaine ; 1,2,3 = profondeur
        self.combo_queued = None   # nom de l'action a enchainer (mise en file pendant un coup)
        self.t_combo = 0           # instant (horloge ACTIVE) du dernier appui de combo
        self.a1_prev = False; self.a2_prev = False
        # SPIN (config["spin"], ex Barrion) : toupie incontrolable dans le sens du mouvement,
        # multi-hit, renvoyable en parade frame-parfaite.
        self.spinning = False; self.spin_dir = 1; self._spin_hit_f = -99
        self._spin_loops = 0; self._spin_prev_fi = 0   # tours accomplis + detection de rebouclage
        self._spin_parried = False                     # spin renvoye (rebond sur bouclier) -> ne frappe plus
        self.spin_cd = 0                               # recharge du spin (frames) -> barre au-dessus de la tete
        self.jumping_attack = False; self.ja_dir = 1; self._ja_timer = 0   # saut-attaque (Haut, timer continu)
        self._ja_hit_f = -99; self.hit_down = False   # multi-hit du saut + slam vertical (Move2 en l'air)
        self._hd_timer = 0; self._hd_start_bottom = 0   # slam : timer + hauteur de depart des PIEDS
        self._hd_ax = 0; self._hd_impact_x = 0          # slam : ancre du corps au cast + point d'impact (sol)
        self._hd_blit_x = 0; self._hd_blit_y = 0        # slam : blit de la CELLULE (ancree au sol, fixe)
        self._hd_dx0 = 0; self._hd_dy0 = 0              # slam : ecart au cast, resorbe en ~6 frames (fluide)
        self._hd_rx0 = 0; self._hd_ry0 = 0              # slam : ecart du RECT au cast (resorbe pareil)
        self._hd_hammer_hit = False; self._hd_body_hit = False   # premier-contact : 1 coup par source
        self._hd_prev = None                            # rect au pas precedent (balayage anti-tunneling)
        self.teleporting = False; self._tp_timer = 0; self.tp_dir = 1   # teleport (Oswald, Haut)
        self._tp_fait = False                           # le saut instantane n'a lieu qu'UNE fois
        self.tp_cd = 0                                  # recharge du TELEPORT (separee du cd d'attaque)
        self._l_prev = False; self._r_prev = False     # fronts montants gauche/droite (double-tap -> spin)
        self._l_tap_ms = -999; self._r_tap_ms = -999   # instant du dernier tap gauche / droite
        self.last_move_dir = 1     # dernier sens de deplacement (-1 gauche / +1 droite)
        self.up_prev = False       # front montant du saut (jump-attack a venir)
        # blocage
        self.block = False; self.blocked_hit = False; self.block_health = 0; self.block_cd = 0
        # bouclier + titre (comme Fighter)
        for i in range(3):
            sh = pygame.image.load('assets/fighting assets/block_shield%d.png' % i).convert_alpha()
            setattr(self, "shield_sprite%d" % i,
                    pygame.transform.scale(sh, (self.rect.width * 1.5, self.rect.height * 1.5)))
        if c.get("title"):
            title = pygame.image.load(c["title"]).convert_alpha()
            self.title = pygame.transform.scale(title, (c["title_size"][0], c["title_size"][1]))
        else:
            self.title = pygame.Surface((1, 1), pygame.SRCALPHA)   # ex: mannequin -> pas de nom

    def _charger_sheets(self, sheets, echelle):
        """Decoupe chaque sheet (frames fw x fh) ligne par ligne -> animation_list a plat.
        Calcule aussi, par animation : la largeur de frame scalee (_fw_anim) et le PIVOT
        des pieds scale (_piv = (centre_x_pieds, bas)) -> sert a aligner les sheets entre
        elles (une sheet d'attaque plus large ne decale plus le perso)."""
        anims = []; self._fw_anim = []; self._piv = []; self._piv_frames = []
        for sh in sheets:
            img = pygame.image.load(sh["path"]).convert_alpha()
            fh = sh["fh"]
            # "fw" peut etre un entier (uniforme) OU une liste (largeur PAR LIGNE) : certaines
            # sheets melangent des largeurs (ex King : attack1/2 en 160, attack3 en 128).
            fws = sh["fw"] if isinstance(sh["fw"], (list, tuple)) else [sh["fw"]] * len(sh["steps"])
            for r, n in enumerate(sh["steps"]):
                fw = fws[r]
                tw, th = int(fw * echelle), int(fh * echelle)
                frames, cxs, bys, pf = [], [], [], []
                for cidx in range(n):
                    sub = img.subsurface((cidx * fw, r * fh, fw, fh))
                    cx, by = _pivot_pieds(sub)          # sur la frame NON scalee (rapide)
                    if cx is not None:
                        cxs.append(cx); bys.append(by)
                    pf.append((cx * echelle, by * echelle) if cx is not None else None)
                    frames.append(pygame.transform.scale(sub, (tw, th)))
                anims.append(frames); self._fw_anim.append(tw)
                if cxs:
                    cxs.sort(); bys.sort()
                    med = (cxs[len(cxs) // 2] * echelle, bys[-1] * echelle)
                else:
                    med = (tw / 2.0, float(th))
                self._piv.append(med)
                self._piv_frames.append([p if p is not None else med for p in pf])   # None -> mediane
        return anims

    def _calc_offsets(self, rw, rh):
        """Offset par action (gauche/droite/vertical) qui CENTRE les pieds sur le rect et
        pose le bas au sol, pour CHAQUE animation (peu importe la sheet)."""
        # La MORT s'arrete sur sa DERNIERE frame (cadavre) : on l'ancre sur CETTE frame,
        # sinon le cadavre flotte (l'ancrage "point le plus bas de toute l'anim" prend la
        # chute qui descend plus bas que la pose finale de repos).
        di = self.actions.get("death")
        if di is not None and self.animation_list[di]:
            cx, by = _pivot_pieds(self.animation_list[di][-1])
            if cx is not None:
                self._piv[di] = (cx, by)
        # Correction HORIZONTALE (px echelle-sprite) du centre des pieds, quand une arme
        # (ex: epee/marteau de Barrion) fausse la mediane -> recentre le CORPS. pivot_dx s'applique
        # aux DEUX sens ; mais comme il se FLIP, on peut regler chaque sens SEPAREMENT avec
        # pivot_dx_R (regard a droite) et pivot_dx_L (regard a gauche) si le sprite est asymetrique.
        pdx = self.config.get("pivot_dx", 0)
        pdxr = self.config.get("pivot_dx_R", pdx)
        pdxl = self.config.get("pivot_dx_L", pdx)
        # Anims de MOUVEMENT (spin/saut/tp) : le perso est dessine DECALE dans le cadre pour appuyer
        # le mouvement. On les ancre PAR FRAME sur les pieds -> le perso reste toujours sur la hitbox
        # (elle "suit" le sprite), sinon "tp" visuel a la fin de l'anim. config["anims_par_frame"].
        par_frame = {self.actions[n] for n in self.config.get("anims_par_frame", []) if n in self.actions}
        self._off = {}
        self._off_frames = {}
        for idx in range(len(self.animation_list)):
            cx, by = self._piv[idx]
            fw = self._fw_anim[idx]
            self._off[idx] = ((cx + pdxr) - rw / 2.0, fw - (cx + pdxl) - rw / 2.0, by - rh)
            if idx in par_frame:
                self._off_frames[idx] = [((c + pdxr) - rw / 2.0, fw - (c + pdxl) - rw / 2.0, b - rh)
                                         for (c, b) in self._piv_frames[idx]]
        # Anims AERIENNES (salto) : ancrage sur le CENTROIDE (stable en rotation, contrairement aux
        # pieds qui partent en l'air) + arc de LIFT (px echelle-sprite) qui pilotera rect.bottom pour
        # que la boite suive l'arc du sprite (anim-driven vertical). config["anims_arc"].
        self._off_arc = {}; self._foot_off = {}
        arc_actions = {self.actions[n] for n in self.config.get("anims_arc", []) if n in self.actions}
        for idx in arc_actions:
            cents = [pygame.mask.from_surface(fr).centroid() for fr in self.animation_list[idx]]
            bots = [fr.get_bounding_rect().bottom for fr in self.animation_list[idx]]   # bas (pieds)
            fw = self._fw_anim[idx]
            self._off_arc[idx] = [(cxc - rw / 2.0, fw - cxc - rw / 2.0, cyc - rh / 2.0)
                                  for (cxc, cyc) in cents]
            # distance PIEDS(bas)-CENTROIDE par frame -> pose les pieds au sol pour les frames AU SOL
            # (gere accroupi vs debout), le centroide anime le salto (stable en rotation).
            self._foot_off[idx] = [bot - cyc for (bot, (cxc, cyc)) in zip(bots, cents)]
        # LIFT du centroide au-dessus du sol (px echelle-sprite) PAR FRAME, precalcule UNE fois :
        #   au sol -> foot_off (pieds au sol) ; en l'air (launch..land) -> parabole (cloche).
        # En jeu on l'INTERPOLE en continu (sous-frame) -> mouvement fluide (pas de saut par frame).
        self._ja_lift = {}
        ja = self.config.get("jump_attack")
        jidx = self.actions.get("jump_attack")
        if ja and jidx in arc_actions:
            fo = self._foot_off[jidx]
            lc, ld, peak = ja["launch"], ja["land"], ja["peak"]
            ref = fo[lc] if lc < len(fo) else 0
            lift = []
            for fi, v in enumerate(fo):
                if lc <= fi <= ld and ld > lc:
                    lift.append(ref + peak * math.sin(math.pi * (fi - lc) / float(ld - lc)))
                else:
                    lift.append(v)
            self._ja_lift[jidx] = lift
        # SLAM (jump_attack_hit_down) : precalculs pour l'ancrage "cellule au sol" + le SUIVI DU
        # CORPS. (1) _hd_cell = point d'impact du marteau (mediane des pivots des frames d'onde,
        # stables) + calibration fin (derniere frame : corps debout au sol pres du marteau).
        # (2) _hd_body = position du CORPS par frame, par TRACKING de la composante connexe la plus
        # proche de la position precedente (le marteau lance / l'onde de choc sont des composantes
        # SEPAREES ; on ignore les petites, <30% de la plus grosse) -> le rect peut suivre le corps
        # meme quand pivots/centroides sont pollues par les VFX. Lissage mediane-3 (bruit VFX).
        hidx = self.actions.get("jump_attack_hit_down")
        if ja and hidx is not None:
            piv = self._piv_frames[hidx]
            imp = piv[ja.get("hammer_from", 19):]
            sc = sorted(c for c, _ in imp); sb = sorted(bb for _, bb in imp)
            cxi, byi = sc[len(sc) // 2], sb[len(sb) // 2]
            body = []; hammer = []; prev = None; prevh = None
            for fr in self.animation_list[hidx]:
                # tracking sur une frame REDUITE (1/4) : 16x moins de pixels, precision +-4px
                # echelle-sprite (~2px ecran) -> chargement rapide (sinon ~2s rien que pour ca).
                pt = pygame.transform.scale(fr, (max(1, fr.get_width() // 4),
                                                 max(1, fr.get_height() // 4)))
                infos = [((m.centroid()[0] * 4, m.centroid()[1] * 4), m.count())
                         for m in pygame.mask.from_surface(pt).connected_components(minimum=25)]
                gros = max((n for _, n in infos), default=0)
                cands = [c for c, n in infos if n >= gros * 0.3]
                if not cands:
                    body.append(prev or (cxi, byi)); hammer.append(prevh or prev or (cxi, byi))
                    continue
                if prev is None:
                    prev = max(infos, key=lambda cn: cn[1])[0]    # 1re frame : la plus grosse masse
                else:
                    prev = min(cands, key=lambda c: (c[0] - prev[0]) ** 2 + (c[1] - prev[1]) ** 2)
                body.append(prev)
                # le MARTEAU lance = l'autre grosse composante (celle qui n'est pas le corps) ;
                # tant qu'il est en main (une seule masse), sa position = le corps.
                autres = [(c, n) for c, n in infos if n >= gros * 0.3 and c != prev]
                if autres:
                    if prevh is None:
                        prevh = max(autres, key=lambda cn: cn[1])[0]
                    else:
                        prevh = min((c for c, _ in autres),
                                    key=lambda c: (c[0] - prevh[0]) ** 2 + (c[1] - prevh[1]) ** 2)
                hammer.append(prevh if prevh else prev)
            liss = []
            for k in range(len(body)):
                v0, v1, v2 = body[max(0, k - 1)], body[k], body[min(len(body) - 1, k + 1)]
                liss.append((sorted((v0[0], v1[0], v2[0]))[1], sorted((v0[1], v1[1], v2[1]))[1]))
            self._hd_body = liss
            # marteau : positions de VOL jusqu'a hammer_from, puis fige au point d'impact
            # (apres l'impact le tracking capterait des morceaux d'onde de choc).
            hf = ja.get("hammer_from", 20)
            self._hd_hammer = [((cxi, byi) if k >= hf else hammer[k]) for k in range(len(hammer))]
            self._hd_cell = (cxi, byi, cxi - liss[-1][0], byi - liss[-1][1])   # (+ ofx/ofy calibration)

    def draw(self, surface):
        """Comme Fighter.draw mais avec l'offset PAR ANIMATION (aligne aux pieds) ; pour les anims
        de MOUVEMENT (spin/saut/tp), offset PAR FRAME -> le perso reste sur la hitbox (pas de tp)."""
        debug_box(self.rect, "hitbox")
        debug_cds(surface, self)
        if getattr(self, "hit_down", False):
            # SLAM : la CELLULE est ancree AU SOL (posee au cast : _hd_blit_x/_hd_blit_y, le point
            # d'impact du marteau = SOL). Le corps/le marteau/l'onde bougent DANS la cellule ; le
            # rect ne pilote pas ce dessin (il suit le corps pour les collisions). L'ecart au cast
            # (dx0/dy0) se resorbe en ~6 frames -> transition fluide depuis le salto.
            fi = min(self.frame_index, len(self.animation_list[self.action]) - 1)
            src = self.animation_list[self.action][fi]
            e = getattr(self, "echelle", 1.0)
            t0 = min(getattr(self, "_hd_timer", 99) / 6.0, 1.0)
            img = pygame.transform.flip(sprite_nuit(src), self.flip, False)
            surface.blit(img, (self._hd_blit_x + self._hd_dx0 * (1.0 - t0),
                               self._hd_blit_y + self._hd_dy0 * (1.0 - t0)))
            self._draw_hud(surface)
            return
        arc = getattr(self, "_off_arc", {}).get(self.action)
        per = self._off_frames.get(self.action)
        if arc is not None:                          # anim aerienne : ancrage CENTROIDE (salto)
            fi = min(self.frame_index, len(arc) - 1)
            oxr, oxl, oy = arc[fi]
            src = self.animation_list[self.action][fi]
        elif per is not None:
            # IMPORTANT : l'image doit venir de la MEME frame que l'offset. self.image est fixee
            # dans update() AVANT que _spin_update ne fasse sauter frame_index (rebouclage 23->9)
            # -> l'utiliser dessinerait la frame 22 avec l'offset de la frame 9 (ecart ~340px) =
            # "tp" d'une frame au rebouclage. On reprend donc l'image sur frame_index courant.
            fi = min(self.frame_index, len(per) - 1)
            oxr, oxl, oy = per[fi]
            src = self.animation_list[self.action][fi]
        else:
            oxr, oxl, oy = self._off.get(self.action, (self.offset[0], self.offset[0], self.offset[1]))
            src = self.image
        ox = oxl if self.flip else oxr
        e = getattr(self, "echelle", 1.0)
        img = pygame.transform.flip(sprite_nuit(src), self.flip, False)
        if grace_active(self):
            img.set_alpha(130 if (temps_ms() // 66) % 2 else 220)   # grace : le sprite clignote
        surface.blit(img, (self.rect.x - ox * e, self.rect.y - oy * e))
        self._draw_hud(surface)

    def _draw_hud(self, surface):
        """Titre + triangle indicateur + barre de recharge du spin + bouclier (commun aux deux
        chemins de draw : normal et slam ancre au sol)."""
        if not getattr(self, "title_hidden", False):
            surface.blit(self.title, self.title_dest())
        col = (220, 40, 40) if self.side == "Left" else (40, 90, 220)
        cx, top = self.rect.centerx, self.rect.top
        tri = [(cx - 18, top - 40), (cx + 18, top - 40), (cx, top - 14)]
        pygame.draw.polygon(surface, col, tri)
        pygame.draw.polygon(surface, (255, 255, 255), tri, 3)
        # (recharge du spin visible via le panneau debug_cds, menu Display > Debug)
        # bouclier affiche si on bloque, OU maintenu qq frames apres un coup pris de dos en
        # gardant (garde_hold) -> le bouclier ne se retracte pas pendant l'anim de get-hit.
        montre = self.block or temps_ms() < getattr(self, "_garde_hold", 0)
        shield = None
        if montre and self.block_health >= BOUCLIER_MAX * 0.66:
            shield = self.shield_sprite2
        elif montre and self.block_health >= BOUCLIER_MAX * 0.33:
            shield = self.shield_sprite1
        elif montre:
            shield = self.shield_sprite0
        if shield is not None:
            si = pygame.transform.flip(sprite_nuit(shield), self.flip, False)
            surface.blit(si, (self.rect.centerx - si.get_width() // 2,
                              self.rect.centery - si.get_height() // 2))

    def title_dest(self):
        if self.side == "Left":
            return (30, 50)
        return (1600 - 30 - self.title.get_width(), 50)

    # ------------------------------------------------------------------ combat + combo
    _NOM_ATK = {"attack1": 1, "attack2": 2, "attack3": 3}

    def _nom_action(self, idx):
        for nom, i in self.actions.items():
            if i == idx and nom in self._NOM_ATK:
                return nom
        return None

    def _demarrer_attaque(self, nom):
        self.attacking = True
        self.damage_dealt = False
        self.action = self.actions[nom]
        self.frame_index = 0
        self.update_time = temps_ms()
        self.attack_type = self._NOM_ATK.get(nom, 1)

    def combo_timing(self):
        """SOURCE UNIQUE pour le Combo Trainer : la fenetre d'enchainement REELLE du coup en
        cours, calculee avec les MEMES valeurs que la logique de mvmt (config["combo"]) ->
        tout reequilibrage de la fenetre se reflete automatiquement dans le trainer.
        Renvoie (total, debut, fin, position) -- fin EXCLUSIVE, memes unites que position --
        ou None (pas d'attaque / rien a enchainer apres ce coup / enchainement deja en file)."""
        combo = self.config.get("combo")
        if not (self.attacking and combo) or self.combo_queued:
            return None
        nom = self._nom_action(self.action)
        if not nom or nom not in combo.get("chaines", {}):
            return None                        # derniere attaque de la chaine : rien a enchainer
        ff = combo.get("fenetre_frames")
        if ff is not None:                     # FRAME-PERFECT : appui sur [impact, impact+ff]
            total = len(self.animation_list[self.action])
            imp = self.config.get("attacks", {}).get(self.attack_type, {}).get("frame", 0)
            return (total, imp, min(imp + ff + 1, total), self.frame_index)
        mn, mx = combo.get("fenetre", (0.25, 0.6))   # fenetre en TEMPS depuis le debut du coup
        return (mx * 1.25, mn, mx, (temps_actif_ms() - self.t_combo) / 1000.0)

    def mvmt(self, surface, target):
        """Deplacement + gravite + COMBO (chaine d'attaques via config["combo"])."""
        c = self.config
        e = getattr(self, "echelle", 1.0)
        speed_base = c["speed_base"] * e
        gravit = c["gravit"] * e
        jump = c["jump"] * e
        dx = dy = 0
        self.running = False
        self.block = False
        bouclier_tick(self)      # regen de l'usure hors blocage + garde BRISEE = sonne
        if self.attack_cd > 0:
            self.attack_cd -= 1
        if self.spin_cd > 0:
            self.spin_cd -= 1
        if self.tp_cd > 0:
            self.tp_cd -= 1
        speed = speed_base / 1.5 if self.jump else speed_base
        if self.block_cd > 0:
            self.block_cd -= 1
            speed = speed_base / 2
            jump = jump / 1.5
        inp = getattr(self, "inputs", None) or Inputs()
        BLOCK, L, R, U, A1, A2 = inp.block, inp.left, inp.right, inp.up, inp.move1, inp.move2
        if not getattr(self, "controls_enabled", True):
            BLOCK = L = R = U = A1 = A2 = False
        now = temps_actif_ms()                        # horloge ACTIVE (figee en hitstop) -> combo
        presse = {"move1": A1 and not self.a1_prev, "move2": A2 and not self.a2_prev}
        self.a1_prev, self.a2_prev = A1, A2
        peut_agir = self.alive and not self.hit

        # --- SPIN (config["spin"]) : DOUBLE-TAP d'une direction (gauche-gauche ou droite-droite
        # dans double_window_ms) -> toupie dans ce sens. Remplace l'ancien Move1+Move2 (trop
        # bugge : ghosting clavier a 3 touches, conflits avec le combo). ---
        spin = c.get("spin")
        if spin:
            now_ms = temps_ms()
            pl = L and not self._l_prev            # front montant gauche
            pr = R and not self._r_prev            # front montant droite
            self._l_prev, self._r_prev = L, R
            dtap = 0
            win = spin.get("double_window_ms", 300)
            if pl:
                if 0 <= now_ms - self._l_tap_ms <= win: dtap = -1   # 2e tap gauche dans la fenetre
                self._l_tap_ms = now_ms
            if pr:
                if 0 <= now_ms - self._r_tap_ms <= win: dtap = 1    # 2e tap droite dans la fenetre
                self._r_tap_ms = now_ms
            if (dtap != 0 and not self.spinning and peut_agir and not self.attacking
                    and not self.jump and self.attack_cd == 0 and self.spin_cd == 0):
                self.spinning = True
                self.spin_dir = dtap
                self.flip = self.spin_dir < 0
                self.action = self.actions["spin_attack"]; self.frame_index = 0
                self.damage_dealt = False; self._spin_hit_f = -99; self._spin_parried = False
                self._spin_loops = 0; self._spin_prev_fi = 0
                self.update_time = temps_ms()
                self._l_tap_ms = self._r_tap_ms = -999   # evite un re-trigger sur un 3e tap

        if self.spinning:
            # file tout droit UNIQUEMENT pendant la rotation (milieu) : il prepare et s'arrete
            # SUR PLACE (intro/outro immobiles), sinon il "glisse" pendant la prep -> moche.
            ls = spin.get("loop_start", spin["first"]); le = spin.get("loop_end", spin["last"])
            dx = self.spin_dir * spin["speed"] * e if ls <= self.frame_index <= le else 0
        elif self.jumping_attack:
            ja = c["jump_attack"]
            self._ja_timer += 1
            # Move2 EN L'AIR (pendant le vol) -> declenche le SLAM vertical (une seule fois).
            if (not self.hit_down and presse.get("move2") and self.jump
                    and "jump_attack_hit_down" in self.actions):
                # pieds ecran actuels : le SAUT ancre le CENTROIDE (feet = centroide + foot_off) ->
                # on part de la meme hauteur de pieds pour la descente (pas de tp a la bascule).
                foj = getattr(self, "_foot_off", {}).get(self.action)
                self._hd_start_bottom = self.rect.centery + (foj[min(self.frame_index, len(foj) - 1)]
                                                             if foj else 0) * e
                # ANCRAGE "CELLULE AU SOL" (precalcule aux pivots) : on cale la cellule pour que le
                # POINT D'IMPACT du marteau (mediane des pivots apres l'impact, stable) soit AU SOL,
                # et le CORPS (pivot a slam_start) sur la position actuelle de Barrion.
                idx = self.actions["jump_attack_hit_down"]
                piv = self._piv_frames[idx]
                ss = ja["slam_start"]
                cx0, by0 = piv[ss]
                cxi, byi, ofx, ofy = self._hd_cell              # point d'impact + calibration (precalc)
                W = self._fw_anim[idx]
                dirn = -1 if self.flip else 1
                self._hd_ax = self.rect.centerx
                self._hd_impact_x = self._hd_ax + dirn * (cxi - cx0) * e
                # tout doit rester A L'ECRAN : si le marteau tomberait hors champ, on decale TOUT
                # (le decalage passe dans dx0/rx0 et se resorbe fluidement en ~6 frames).
                marge = 90
                shift = 0
                if self._hd_impact_x > 1600 - marge: shift = (1600 - marge) - self._hd_impact_x
                elif self._hd_impact_x < marge:      shift = marge - self._hd_impact_x
                self._hd_ax += shift; self._hd_impact_x += shift
                self._hd_blit_x = self._hd_ax - ((W - cx0) if self.flip else cx0) * e
                self._hd_blit_y = SOL - byi * e                 # bas d'impact de la cellule = SOL
                self._hd_dx0 = self.rect.centerx - self._hd_ax  # = -shift (0 si tout tient a l'ecran)
                self._hd_dy0 = self._hd_start_bottom - (self._hd_blit_y + by0 * e)
                # ecart du RECT au cast vs le CORPS suivi (_hd_body), resorbe comme le sprite ->
                # la boite part de la position actuelle et rejoint le corps mesure sans saut.
                bx0, byc0 = self._hd_body[ss]
                sx0 = (W - (bx0 + ofx)) if self.flip else (bx0 + ofx)
                self._hd_rx0 = self.rect.centerx - (self._hd_blit_x + sx0 * e)
                self._hd_ry0 = self.rect.bottom - (self._hd_blit_y + (byc0 + ofy) * e)
                self._hd_hammer_hit = False; self._hd_body_hit = False
                self.hit_down = True
                self.action = self.actions["jump_attack_hit_down"]
                self._hd_timer = 0; self._ja_hit_f = -99
            if self.hit_down:
                # SLAM : la CELLULE est ancree au sol (draw) -> le marteau frappe LE SOL. Le RECT
                # SUIT LE CORPS mesure par frame (_hd_body, tracking par composantes precalcule),
                # interpole en continu -> la hitbox reste calee sur Barrion pendant TOUTE l'anim
                # (flottement, ruee vers le marteau, atterrissage). Timer continu -> fluide.
                self._hd_timer += 1
                self._hd_prev = self.rect.copy()               # position avant le pas (balayage degats)
                m = len(self.animation_list[self.action])
                ss, hdur = ja["slam_start"], ja["hd_duration"]
                fp = ss + min(self._hd_timer / float(hdur), 1.0) * (m - 1 - ss)
                self.frame_index = min(int(fp), m - 1)
                t0 = min(self._hd_timer / 6.0, 1.0)            # resorbe l'ecart du cast (~6 frames)
                cxi, byi, ofx, ofy = self._hd_cell
                body = self._hd_body
                i0 = min(int(fp), m - 1); i1 = min(i0 + 1, m - 1); frac = fp - i0
                bx = body[i0][0] + (body[i1][0] - body[i0][0]) * frac
                byc = body[i0][1] + (body[i1][1] - body[i0][1]) * frac
                W = self._fw_anim[self.action]
                sx = (W - (bx + ofx)) if self.flip else (bx + ofx)
                cible_x = self._hd_blit_x + sx * e + self._hd_rx0 * (1.0 - t0)
                cible_bottom = min(SOL, self._hd_blit_y + (byc + ofy) * e + self._hd_ry0 * (1.0 - t0))
                dx = cible_x - self.rect.centerx
                dy = cible_bottom - self.rect.bottom
                self.jump = cible_bottom < SOL - 25            # airborne tant que le corps vole
                if self._hd_timer >= hdur:
                    self.jumping_attack = False; self.hit_down = False
                    self.attack_cd = ja.get("cd_down", ja["cd"]); self.rect.bottom = SOL; self.jump = False
            else:
                # SAUT normal (salto) : TIMER CONTINU -> sprite ET position, fluide. Lift interpole.
                lift = getattr(self, "_ja_lift", {}).get(self.action)
                n = len(lift) if lift else len(self.animation_list[self.action])
                dur = ja.get("duration", 50)
                fp = min(self._ja_timer / float(dur), 1.0) * (n - 1)
                self.frame_index = min(int(fp), n - 1)
                if lift:
                    lc, ld = ja.get("launch", 7), ja.get("land", 17)
                    self.jump = lc <= fp <= ld       # airborne seulement en vol (poussiere aux bons instants)
                    if lc <= fp <= ld:
                        i0 = min(int(fp), n - 1); i1 = min(i0 + 1, n - 1)
                        lv = lift[i0] + (lift[i1] - lift[i0]) * (fp - i0)   # parabole interpolee (fluide)
                        dx = self.ja_dir * ja.get("advance", 0) * e
                    else:
                        lv = lift[self.frame_index]; dx = 0    # AU SOL : lift de la frame dessinee (pieds au sol)
                    centery = SOL - lv * e
                    dy = (centery + self.rect.height / 2.0) - self.rect.bottom
                if self._ja_timer >= dur:
                    self.jumping_attack = False
                    self.attack_cd = ja["cd"]; self.rect.bottom = SOL; self.jump = False
            self.vel_y = 0
        elif self.teleporting:
            # TELEPORT : timer continu (sprite). Le deplacement est un VRAI TP : un saut
            # INSTANTANE de toute la distance quand l'anim atteint tp_frame (le coeur du blink),
            # pas un glissement. INVULNERABLE tout du long (voir esquive()).
            tp = c["teleport"]
            self._tp_timer += 1
            n = len(self.animation_list[self.action])
            dur = tp.get("duration", 24)
            fp = min(self._tp_timer / float(dur), 1.0) * (n - 1)
            self.frame_index = min(int(fp), n - 1)
            if not self._tp_fait and fp >= tp.get("tp_frame", 7):
                dx = self.tp_dir * tp["distance"] * e   # TP : tout le deplacement d'UN coup
                self._tp_fait = True
            self.vel_y = 0
            if self._tp_timer >= dur:                  # fin : re-TP en recharge, mais l'ATTAQUE
                self.teleporting = False               # est dispo presque tout de suite -> le tp
                self.tp_cd = tp["cd"]                  # est un OUTIL OFFENSIF (tp -> punir)
                self.attack_cd = tp.get("cd_attaque", 3)
        else:
            combo = c.get("combo")
            if peut_agir and not self.block and combo:
                if not self.attacking and self.attack_cd == 0:
                    for btn, act in combo["base"].items():          # appui standalone
                        if presse.get(btn):
                            self._demarrer_attaque(act)
                            self.t_combo = now
                            self.combo_step = 1 if act == combo.get("depart") else 0
                            self.combo_queued = None
                            break
                elif self.attacking and self.combo_step >= 1 and self.combo_queued is None:
                    ch = combo["chaines"].get(self._nom_action(self.action))   # enchainer ?
                    if ch:
                        btn, nxt = ch
                        ff = combo.get("fenetre_frames")
                        if ff is not None:
                            # FRAME-PERFECT : l'appui doit tomber sur [frame_impact, +ff] de
                            # l'attaque en cours (fenetre de quelques frames -> timing serre).
                            imp = self.config.get("attacks", {}).get(self.attack_type, {}).get("frame", 0)
                            ok = presse.get(btn) and imp <= self.frame_index <= imp + ff
                        else:
                            mn, mx = combo.get("fenetre", (0.25, 0.6))   # fenetre en temps (souple)
                            ok = presse.get(btn) and mn <= (now - self.t_combo) / 1000.0 <= mx
                        if ok:
                            self.combo_queued = nxt

            if peut_agir and not self.attacking and not self.block:
                if BLOCK and not self.jump and self.block_cd == 0:
                    self.block = True; speed = 0; jump = 0
                if L:
                    dx = -speed; self.flip = True; self.running = True; self.last_move_dir = -1
                if R:
                    dx = speed; self.flip = False; self.running = True; self.last_move_dir = 1
                if U and not self.jump:
                    ja = c.get("jump_attack")
                    tp = c.get("teleport")
                    if tp and self.attack_cd == 0 and self.tp_cd == 0:   # Haut -> TELEPORT (invuln)
                        self.teleporting = True; self._tp_timer = 0; self._tp_fait = False
                        self.tp_dir = -1 if L else (1 if R else (-1 if self.flip else 1))
                        self.flip = self.tp_dir < 0
                        self.action = self.actions["teleport"]; self.frame_index = 0
                        self.update_time = temps_ms()
                    elif ja and self.attack_cd == 0:              # Haut -> saut-attaque (salto marteau)
                        self.jumping_attack = True; self._ja_timer = 0; self._ja_hit_f = -99
                        self.hit_down = False
                        self.ja_dir = -1 if self.flip else 1
                        self.action = self.actions["jump_attack"]; self.frame_index = 0
                        self.damage_dealt = False; self.update_time = temps_ms()
                    elif jump:
                        self.vel_y = -jump; self.jump = True
        if not self.jumping_attack:                 # le saut-attaque pilote dy via l'arc (pas de gravite)
            self.vel_y += gravit
            dy += self.vel_y

        if self.rect.left + dx < 0: dx = -self.rect.left
        if self.rect.right + dx > 1600: dx = 1600 - self.rect.right
        if not self.jumping_attack and self.rect.bottom + dy > SOL:
            dy = SOL - self.rect.bottom; self.jump = False
        self.rect.x += dx
        self.rect.y += dy

    def update(self, surface, target):
        A = self.actions
        if self.hit and (grace_active(self) or armure_lourde(self)):
            self.hit = False               # anti stun-lock / super armor : degats subis, pas de stun
            self._hit_amorti = True        # ... mais les SONS d'impact jouent quand meme (feedback)
        if self.health <= 0:
            self.health = 0; self.alive = False; self.spinning = False
            self.jumping_attack = False; self.hit_down = False; self.teleporting = False
            self.update_action(A["death"])
        elif self.hit:
            self.spinning = False; self.jumping_attack = False; self.hit_down = False
            self.teleporting = False                   # annule spin/saut/slam/teleport
            self.update_action(A["take_hit"])
        elif self.spinning:
            pass                                       # spin_attack deja pose (mvmt)
        elif self.jumping_attack:
            pass                                       # jump_attack deja pose (mvmt)
        elif self.teleporting:
            pass                                       # teleport deja pose (mvmt)
        elif self.block:
            self.update_action(A["idle"])
        elif self.attacking:
            pass                                       # action d'attaque deja posee
        elif self.jump:
            self.update_action(A.get("jump", A["idle"]))
        elif self.running:
            self.update_action(A["run"])
        else:
            self.update_action(A["idle"])

        animation_cd = self._cd_idx.get(self.action, self.config.get("anim_cd", 99))
        self.image = self.animation_list[self.action][self.frame_index]
        # saut-attaque et teleport pilotent frame_index via leur TIMER (mvmt) -> pas de cadence ici
        if not self.jumping_attack and not self.teleporting and temps_ms() - self.update_time > animation_cd:
            self.frame_index += 1
            self.update_time = temps_ms()

        if self.spinning:
            self._spin_update(target)
        if self.jumping_attack:
            self._jump_attack_update(target)

        if self.attacking:
            self.check_collision(surface, target)
            # ENCHAINEMENT : apres la frame d'impact, si un coup est en file -> on l'enchaine
            # tout de suite (annule la recovery -> combo nerveux).
            atk = self.config.get("attacks", {}).get(self.attack_type)
            seuil = atk["frame"] if atk else int(len(self.animation_list[self.action]) * 0.6)
            if self.combo_queued and self.frame_index > seuil:
                nxt = self.combo_queued
                self.combo_queued = None
                self.combo_step += 1
                self._demarrer_attaque(nxt)
                self.t_combo = temps_actif_ms()
                return

        if self.frame_index >= len(self.animation_list[self.action]):
            if not self.alive:
                self.frame_index = len(self.animation_list[self.action]) - 1
                return
            self.frame_index = 0
            if self.jumping_attack:                    # fin du saut-attaque -> recharge + retour sol
                self.jumping_attack = False
                self.attack_cd = self.config["jump_attack"]["cd"]
                if self.rect.bottom < SOL:             # anim finie avant d'atterrir -> pose au sol
                    self.rect.bottom = SOL
                self.jump = False; self.vel_y = 0
            nom = self._nom_action(self.action)
            if self.attacking and nom:                 # fin d'une attaque non enchainee
                cd = self.config.get(nom + "_cd", 15)
                if self.blocked_hit:
                    cd = int(cd * 1.25); self.blocked_hit = False
                self.attacking = False
                self.attack_cd = cd
                self.combo_step = 0
                self.combo_queued = None
            if self.action == A["take_hit"]:
                self.hit = False
                self._grace = temps_actif_ms() + GRACE_MS   # anti stun-lock
                self.attacking = False
                self.attack_cd = 15

    def _spin_update(self, target):
        """Toupie : INTRO (preparation) + MILIEU qui BOUCLE 'loops' fois + OUTRO (arret), l'intro
        et l'outro ne sont joues QU'UNE fois (sinon ca fait bizarre : il n'arrete pas de se
        preparer/s'arreter). MULTI-HIT pendant la boucle (un coup toutes les 'interval' frames,
        box centree car le marteau tourne autour). INVULNERABLE (voir check_collision : toucher un
        perso en spin = effet bouclier pour l'attaquant). RENVOYABLE : un block FRAIS DE FACE
        (block_age <= parry_window) le renvoie DANS L'AUTRE SENS (il repart en toupie a l'oppose,
        ne frappe plus). Bloque de face tenu = chip ; dos au spin = plein degat."""
        spin = self.config["spin"]
        e = getattr(self, "echelle", 1.0)
        n = len(self.animation_list[self.action])
        ls, le = spin.get("loop_start", spin["first"]), spin.get("loop_end", spin["last"])
        loops = spin.get("loops", 1)
        fi = self.frame_index
        if fi > le and self._spin_loops < loops - 1:   # fin d'un tour -> on reboucle le MILIEU
            self._spin_loops += 1
            self.frame_index = ls; fi = ls
            self._spin_hit_f = -99                      # nouveau tour : hits reautorises
        if spin["first"] <= fi <= spin["last"] and not self._spin_parried:   # renvoye : ne frappe plus
            rects = [pygame.Rect(self.rect.centerx + hx * e, self.rect.y + hy * e, int(w * e), int(h * e))
                     for (hx, hy, w, h) in spin["hitboxes"]]
            for r in rects:
                debug_box(r, "damage box")
            if ((fi - self._spin_hit_f) >= spin["interval"]
                    and any(r.colliderect(target.rect) for r in rects)
                    and not esquive(target)):           # teleport : la toupie le traverse
                self._spin_hit_f = fi
                a_gauche = self.rect.centerx < target.rect.centerx
                # le bouclier ne protege QUE de face (comme les attaques / la lance d'Arinya) :
                # dos au spin -> pas de blocage, on encaisse.
                de_face = getattr(target, "block", False) and (a_gauche == target.flip)
                if de_face and getattr(target, "block_age", 999) <= spin.get("parry_window", 0):
                    # PARADE (block FRAIS, DE FACE) -> le spin est RENVOYE : Barrion rebondit sur
                    # le bouclier et repart en toupie DANS L'AUTRE SENS (et ne frappe plus).
                    self.spin_dir = -self.spin_dir
                    self.flip = self.spin_dir < 0
                    self._spin_parried = True
                    self.frame_index = ls                # repart au debut de la rotation -> recul net
                elif de_face:
                    target.health -= spin["damage"] * BLOC_CHIP  # garde tenue de face : chip + usure
                    target.block_health += spin["block_dmg"] * 0.9
                else:
                    target.health -= spin["damage"]           # dos au spin / pas de garde : plein
                    target.hit = True                         # joue quand meme l'anim de get-hit
                    poussee(target, self.rect.centerx, spin["damage"])   # recul d'impact
                    if getattr(target, "block", False):
                        # bloc leve mais de DOS : on encaisse ET on joue le hit, MAIS le bouclier
                        # NE se baisse pas -> on le maintient affiche qq frames (sinon il clignote
                        # en multi-hit a chaque passage devant/derriere).
                        target._garde_hold = temps_ms() + spin.get("garde_hold_ms", 300)
        if fi >= n - 1:                                 # outro terminee -> fin du spin + recharge
            self.spinning = False
            self.frame_index = n - 1
            self.attack_cd = spin["cd"]
            self.spin_cd = spin["cd"]                   # recharge dediee (pour la barre)

    def _infliger(self, target, dmg, bdmg):
        """Resolution d'un coup (commune saut/slam) : invuln-toupie, block de face (chip + usure),
        sinon plein degat + get-hit (bouclier maintenu si la cible gardait de dos)."""
        if esquive(target):                             # teleport : le coup RATE (esquive totale)
            return
        a_gauche = self.rect.centerx < target.rect.centerx
        de_face = getattr(target, "block", False) and (a_gauche == target.flip)
        if getattr(target, "spinning", False):          # cible en toupie = invuln (effet bouclier)
            self.blocked_hit = True
        elif de_face and parade_parfaite(target):
            riposte_parade(self, target)                # PARADE PARFAITE : rien pour la cible
        elif de_face:
            target.health -= dmg * BLOC_CHIP
            target.block_health += bdmg * 0.9
            self.blocked_hit = True
        else:
            target.health -= dmg
            target.hit = True
            poussee(target, self.rect.centerx, dmg)     # recul d'impact
            if getattr(target, "block", False):         # coup de dos en garde -> bouclier maintenu
                target._garde_hold = temps_ms() + 300

    def _jump_attack_update(self, target):
        """Degats du SAUT-ATTAQUE. Saut normal : MULTI-HIT (un coup tous les 'interval' frames
        pendant first..last, le marteau balaie). SLAM : DEUX sources INDEPENDANTES, chacune frappe
        UNE fois DES LE PREMIER CONTACT (pas de fenetre d'impact unique) -- le MARTEAU (box au
        point d'impact au sol, active hammer_from..hammer_to) et le CORPS de Barrion qui percute
        en volant (son rect, jusqu'a body_to)."""
        ja = self.config["jump_attack"]
        e = getattr(self, "echelle", 1.0)
        fi = self.frame_index
        if self.hit_down:
            m = len(self.animation_list[self.action])
            ss, hdur = ja["slam_start"], ja["hd_duration"]
            fp = ss + min(self._hd_timer / float(hdur), 1.0) * (m - 1 - ss)   # position continue
            if fp <= ja["hammer_to"]:
                if fp < ja["hammer_from"]:
                    # MARTEAU EN VOL : box centree sur le marteau lance (position precalculee
                    # _hd_hammer, interpolee en continu -> pas de trou entre deux frames).
                    hm = self._hd_hammer
                    i0 = min(int(fp), m - 1); i1 = min(i0 + 1, m - 1); fr2 = fp - i0
                    hxc = hm[i0][0] + (hm[i1][0] - hm[i0][0]) * fr2
                    hyc = hm[i0][1] + (hm[i1][1] - hm[i0][1]) * fr2
                    W = self._fw_anim[self.action]
                    sxh = (W - hxc) if self.flip else hxc
                    cxh = self._hd_blit_x + sxh * e
                    cyh = self._hd_blit_y + hyc * e
                    rects = [pygame.Rect(cxh + hx * e, cyh + hy * e, int(w * e), int(h * e))
                             for (hx, hy, w, h) in ja["hitboxes_hammer_fly"]]
                else:
                    # MARTEAU A L'IMPACT : box fixe au point de chute (au sol).
                    rects = [pygame.Rect(self._hd_impact_x + hx * e, SOL + hy * e, int(w * e), int(h * e))
                             for (hx, hy, w, h) in ja["hitboxes_down"]]
                for r in rects:
                    debug_box(r, "damage box")
                if not self._hd_hammer_hit and any(r.colliderect(target.rect) for r in rects):
                    self._hd_hammer_hit = True
                    self._infliger(target, ja["damage_down"], ja["block_dmg_down"])
            if not self._hd_body_hit and fi <= ja["body_to"]:
                # BALAYAGE : Barrion traverse tres vite en fonçant vers son marteau (jusqu'a
                # ~900px/frame) -> on teste la ZONE parcourue depuis la frame precedente (union),
                # sinon le rect "saute par-dessus" la cible entre deux frames (tunneling).
                zone = self.rect.union(self._hd_prev) if self._hd_prev else self.rect
                debug_box(zone, "damage box")
                if zone.colliderect(target.rect):
                    self._hd_body_hit = True
                    self._infliger(target, ja["damage_fly"], ja["block_dmg_fly"])
            return
        if not (ja["first"] <= fi <= ja["last"]):
            return
        specs = ja["hitboxes_right"] if not self.flip else ja["hitboxes_left"]
        rects = [pygame.Rect(self.rect.centerx + hx * e, self.rect.y + hy * e, int(w * e), int(h * e))
                 for (hx, hy, w, h) in specs]
        for r in rects:
            debug_box(r, "damage box")
        if ((fi - self._ja_hit_f) >= ja.get("interval", 2)
                and any(r.colliderect(target.rect) for r in rects)):
            self._ja_hit_f = fi
            self._infliger(target, ja["damage"], ja["block_dmg"])

    def check_collision(self, surface, target):
        """Comme Fighter.check_collision mais generalise a TOUTES les attaques du config
        (attack1/2/3), pas seulement 1 et 2."""
        A = self.actions
        e = getattr(self, "echelle", 1.0)
        for atk_type, atk in self.config.get("attacks", {}).items():
            nom = "attack%d" % atk_type
            if nom not in A or self.action != A[nom] or self.frame_index != atk["frame"]:
                continue
            specs = atk["hitboxes_right"] if not self.flip else atk["hitboxes_left"]
            rects = []
            for (hx, hy, w, h) in specs:
                height = self.rect.height if h is None else int(h * e)
                rects.append(pygame.Rect(self.rect.centerx + hx * e, self.rect.y + hy * e,
                                         int(w * e), height))
            for r in rects:
                debug_box(r, "damage box")
            if any(r.colliderect(target.rect) for r in rects) and not self.damage_dealt:
                attaquant_a_gauche = self.rect.centerx < target.rect.centerx
                bloque_de_face = (attaquant_a_gauche == target.flip)
                if esquive(target):
                    self.damage_dealt = True            # teleport : le coup RATE (esquive totale)
                elif getattr(target, "spinning", False):
                    # cible en TOUPIE = INVULNERABLE ; l'attaquant prend l'effet "coup dans un
                    # bouclier" (blocked_hit -> son cd d'attaque rallonge), aucun degat/stun.
                    self.damage_dealt = True
                    self.blocked_hit = True
                elif target.block and bloque_de_face and parade_parfaite(target):
                    riposte_parade(self, target)        # PARADE PARFAITE : rien pour la cible
                elif target.block and bloque_de_face:
                    target.health -= atk["damage"] * BLOC_CHIP
                    target.block_health += atk["block_dmg"] * 0.9
                    self.damage_dealt = True
                    self.blocked_hit = True
                else:
                    target.health -= atk["damage"]
                    target.hit = True
                    self.damage_dealt = True
                    poussee(target, self.rect.centerx, atk["damage"])   # recul d'impact


class Oswald(Fighter2):
    """The King : roi de LUMIERE (glow a venir). Corps a corps ULTRA rapide,
    3 attaques enchainables, teleportation a la place du saut."""

    ICON = "assets/characters/The King/icon.png"

    _SP = "assets/characters/The King/Sprites/"
    CONFIG = {
        "sheets": [
            {"path": _SP + "The King (without attack 128x128).png", "fw": 128, "fh": 128,
             "steps": [18, 8, 14, 37, 4]},          # idle, run, teleport, death, take_hit
            {"path": _SP + "The King (Attacks 160x128).png", "fw": [160, 160, 128], "fh": 128,
             "steps": [27, 31, 24]},                # attack1(160), attack2(160), attack3(128) : largeurs mixtes!
        ],
        "actions": {"idle": 0, "run": 1, "teleport": 2, "death": 3, "take_hit": 4,
                    "attack1": 5, "attack2": 6, "attack3": 7, "jump": 0},   # jump=idle (placeholder)
        "echelle_sprite": 6.52,
        "pos_left": (330, 530), "pos_right": (1120, 530),
        "rect_size": (140, 262),   # cale sur le corps (couronne->pieds ~260px, avant : 320 trop haut)
        # offset_left[0] = largeur_frame_idle(128*6.52=834) - offset_right[0] - largeur_rect
        #   -> le corps reste centre sur le rect quand il regarde a gauche (flip).
        "offset_face_right": [349, 176], "offset_face_left": [335, 176],
        "health": 340, "speed_base": 16, "gravit": 2.0, "jump": 0,   # jump 0 : Haut = TELEPORT
        # TELEPORT (Haut) : VRAI TP INVULNERABLE (esquive totale : les coups RATENT, sans effet
        # bouclier). Il disparait et REAPPARAIT d'un coup 'distance' plus loin (saut INSTANTANE
        # a tp_frame, au coeur du blink) dans la direction tenue (sinon le regard).
        # cd = recharge du RE-teleport (tp_cd dedie) ; cd_attaque = mini-delai avant de pouvoir
        # attaquer en SORTIE de tp (petit -> tp offensif : tp dans le dos puis punir).
        "teleport": {"distance": 420, "duration": 24, "tp_frame": 7, "cd": 26, "cd_attaque": 3},
        # GLOW de LUMIERE (le pouvoir d'Oswald) : halo DORE additif autour de lui, pilote par
        # la QUANTITE de pixels tres clairs de la frame courante (PRECALCULEE au chargement ->
        # en jeu : 1 lookup + 1 blit). force = (pixels - plancher) / seuil, sur ces anims.
        "glow": {"anims": ["attack1", "attack2", "attack3", "teleport"],
                 "seuil": 60000, "plancher": 8000, "couleur": (255, 210, 110)},
        # SWORD OF JUSTICE : chatiment du FINISHER (attack3). A la fin de l'anim d'attack3,
        # l'epee apparait SUR l'ennemi -- position FIGEE au declenchement -> ESQUIVABLE en
        # bougeant pendant la charge au sol (frame_impact / vitesse ~= 10 gf). PAS bloquable,
        # gros degats, et elle GLOW (halo dore). hauteur/zone en px echelle-sprite.
        "sword": {"damage": 30, "frame_impact": 4, "vitesse": 0.4,
                  "hauteur": 420, "zone": 180},
        "armure": (3,),     # SUPER ARMOR : le finisher (eruption) n'est pas interrompu
        # teleport : le perso est dessine DECALE dans les cases (after-images) -> ancrage par
        # frame (le deplacement REEL est porte par le rect, pas par le dessin).
        "anims_par_frame": ["teleport"],
        "anim_cd": 55,   # cadence par defaut (ms/frame) pour les actions non listees
        # attack1/2/3 a PLEINE vitesse (28 < 33ms -> 1 frame d'anim par frame de jeu : le CAC
        # "ultra-rapide" d'Oswald ; a 55 c'etait 2x plus lent -> injouable). L'anim d'attack3
        # etant 2x plus courte, la SWORD OF JUSTICE (declenchee a la FIN de l'anim) sort 2x
        # plus tot -- l'epee elle-meme garde sa propre vitesse (config["sword"]).
        "cadences": {"idle": 100, "run": 48, "death": 30, "take_hit": 45,
                     "attack1": 28, "attack2": 28, "attack3": 28},   # death rapide : la montee
        # de lumiere n'est qu'un bref eclat -> il retombe vite en cendres AU SOL (pas de flottement).
        "frames_supprimees": {"attack2": [24]},   # 7e frame depuis la fin = parasite
        "attack1_cd": 12, "attack2_cd": 14, "attack3_cd": 20,
        # COMBO : Move1 -> Move2 -> Move2 = attack1 -> attack2 -> attack3 (sinon M1=atk1, M2=atk2).
        # fenetre_frames = 1 -> l'appui d'enchainement doit tomber pile sur la frame d'impact
        # (ou juste apres) : timing FRAME-PERFECT (comme Barrion).
        "combo": {
            "base": {"move1": "attack1", "move2": "attack2"},
            "depart": "attack1",
            "chaines": {"attack1": ("move2", "attack2"), "attack2": ("move2", "attack3")},
            # 3 frames d'anim a pleine vitesse (28ms) = ~4 gf (~130ms) : la MEME fenetre
            # REELLE qu'avant le speed-up des attaques (1 frame a 55ms) -> feel inchange.
            "fenetre_frames": 3,
        },
        "title": "assets/characters/The King/title.png", "title_size": (300, 45),
        "title_x_right": 1270,
        # NB: hy compenses de -58 (rect passe de 320 a 262 -> rect.y descend de 58) pour garder
        # les hitbox d'attaque a leur position VERTICALE d'origine (tunee au filmstrip).
        # Equilibrage 2026-07 : gros NERF de degats. Oswald a un TELEPORT (mobilite/esquive
        # totale) ET la Sword of Justice (30 en plus au finisher) -> son melee etait TROP haut
        # (atk2 dps 23.9, atk3 dps 35.5 + l'epee : combo ~113+30). Cible modele dps~16.5.
        # Le combo M1->M2->M2 passe de 26+35+52=113 a 24+24+30=78 (+30 epee = 108) : punchy
        # sans one-shot. block_dmg alignes (bouclier casse en ~3 coups avec BOUCLIER_MAX=55).
        "attacks": {
            # attack1 = explosion de lumiere AUTOUR de lui (radiale, ~f21). Box calee sur
            # l'explosion REELLE mesuree (hx -176..+274, hy -155..+262) : monte au-dessus
            # de la tete et deborde vers l'avant.
            1: {"frame": 21, "damage": 24, "block_dmg": 22,
                "hitboxes_right": [(-180, -160, 460, 425)], "hitboxes_left": [(-280, -160, 460, 425)]},
            # attack2 = pilier de lumiere DEVANT lui (~f11), tres haut.
            2: {"frame": 11, "damage": 24, "block_dmg": 22,
                "hitboxes_right": [(20, -98, 200, 380)], "hitboxes_left": [(-220, -98, 200, 380)]},
            # attack3 = eruption de lumiere verticale centree sur lui (~f20-22). FINISHER
            # (+ Sword of Justice 30 par-dessus si l'anim va au bout).
            3: {"frame": 20, "damage": 30, "block_dmg": 28,
                "hitboxes_right": [(-150, -78, 300, 360)], "hitboxes_left": [(-150, -78, 300, 360)]},
        },
    }

    def __init__(self, side, flip_state):
        super().__init__(side, flip_state)
        # GLOW : force lumineuse PAR FRAME (compte des pixels tres clairs, sur frame reduite
        # 1/4) PRECALCULEE au chargement -> rien a mesurer en jeu (regle perf : jamais d'array
        # par frame). Les anims hors config["glow"]["anims"] n'ont pas de glow.
        g = self.config["glow"]
        self._glow = {}
        if _np is None:
            return
        for nom in g["anims"]:
            idx = self.actions.get(nom)
            if idx is None:
                continue
            forces = []
            for fr in self.animation_list[idx]:
                pt = pygame.transform.scale(fr, (max(1, fr.get_width() // 4),
                                                 max(1, fr.get_height() // 4)))
                rgb = pygame.surfarray.array3d(pt)
                a = pygame.surfarray.array_alpha(pt)
                clair = (a > 50) & (rgb[..., 0] > 200) & (rgb[..., 1] > 180)
                n = int(clair.sum()) * 16              # remis a l'echelle pleine (subsample 1/4)
                forces.append(max(0.0, min(1.0, (n - g["plancher"]) / float(g["seuil"]))))
            self._glow[idx] = forces
        # SWORD OF JUSTICE : frames 64x128 de la bande + force de glow par frame (petites
        # frames -> precalcul trivial). Scale differee au draw avec cache (jamais par frame).
        sw = pygame.image.load("assets/particles/sword of justice.png").convert_alpha()
        self._sword_frames = [sw.subsurface((k * 64, 0, 64, 128)) for k in range(sw.get_width() // 64)]
        self._sword_glow = []
        for fr in self._sword_frames:
            rgb = pygame.surfarray.array3d(fr)
            a = pygame.surfarray.array_alpha(fr)
            clair = (a > 50) & (rgb[..., 0] > 200) & (rgb[..., 1] > 180)
            self._sword_glow.append(min(1.0, int(clair.sum()) / 1500.0))
        self._sword_cache = {}      # (idx, hauteur) -> frame scalee
        self.sword = None           # epee active : {"x" (fige au cast), "t", "dmg"}

    def update(self, surface, target):
        etait_a3 = self.attacking and self.action == self.actions["attack3"]
        super().update(surface, target)
        if etait_a3 and not self.attacking and self.alive:
            # fin du FINISHER -> la SWORD OF JUSTICE apparait sur l'ennemi. Position FIGEE
            # maintenant : s'il bouge pendant la charge au sol, il l'esquive.
            self.sword = {"x": target.rect.centerx, "t": 0.0, "dmg": False}
        self._maj_sword(target)

    def _maj_sword(self, target):
        """Avance l'epee de Justice : charge au sol -> IMPACT a frame_impact (degats UNE fois,
        PAS bloquable -- seulement esquivable/spin/teleport) -> dissolution."""
        s = self.sword
        if s is None:
            return
        sw = self.config["sword"]
        e = getattr(self, "echelle", 1.0)
        s["t"] += sw["vitesse"]
        zone = pygame.Rect(int(s["x"] - sw["zone"] * e / 2), int(SOL - sw["hauteur"] * e),
                           int(sw["zone"] * e), int(sw["hauteur"] * e))
        if s["t"] <= sw["frame_impact"] + 1:
            debug_box(zone, "damage box")              # la zone a esquiver (visible en debug)
        if not s["dmg"] and s["t"] >= sw["frame_impact"]:
            s["dmg"] = True
            if (zone.colliderect(target.rect) and not esquive(target)
                    and not getattr(target, "spinning", False)):
                target.health -= sw["damage"]
                target.hit = True
        if s["t"] >= len(self._sword_frames):
            self.sword = None

    def draw_static_overlay(self, surface):
        """Effets d'Oswald par-dessus la scene : (1) GLOW dore autour de lui (halo pilote par
        la force lumineuse precalculee de la frame courante), (2) SWORD OF JUSTICE sur
        l'ennemi (l'epee + son propre glow). Les halos respectent le reglage par map
        (HALO_STORMR) ; l'epee elle-meme se dessine toujours (c'est l'attaque)."""
        forces = self._glow.get(self.action)
        if forces and HALO_STORMR:
            f = forces[min(self.frame_index, len(forces) - 1)]
            if f > 0.03:
                halo = halo_variante(f, self.config["glow"]["couleur"])
                rect = halo.get_rect(center=self.rect.center)
                surface.blit(halo, rect.topleft, special_flags=pygame.BLEND_RGB_ADD)
        s = self.sword
        if s is not None:
            idx = min(int(s["t"]), len(self._sword_frames) - 1)
            e = getattr(self, "echelle", 1.0)
            H = max(2, int(self.config["sword"]["hauteur"] * e))
            W = max(1, H // 2)
            cle = (idx, H)
            img = self._sword_cache.get(cle)
            if img is None:                        # scale differee + cachee (pas de scale/frame)
                img = pygame.transform.scale(self._sword_frames[idx], (W, H))
                self._sword_cache[cle] = img
            surface.blit(img, (int(s["x"] - W / 2), SOL - H))   # PLEINE lumiere (elle eclaire)
            if HALO_STORMR:
                fg = self._sword_glow[idx]
                if fg > 0.03:
                    halo = halo_variante(fg, self.config["glow"]["couleur"])
                    rect = halo.get_rect(center=(int(s["x"]), SOL - H // 2))
                    surface.blit(halo, rect.topleft, special_flags=pygame.BLEND_RGB_ADD)


class Barrion(Fighter2):
    """Old Knight of the crown : chevalier lourd. Move1=epee legere, Move2=marteau
    lourd, spin attack, jump-attacks (a venir aux etapes 3-4)."""

    ICON = "assets/characters/Old Knight of the crown/icon.png"

    _SP = "assets/characters/Old Knight of the crown/Sprites/"
    CONFIG = {
        "sheets": [
            {"path": _SP + "Old knight of the Crown (170x96).png", "fw": 170, "fh": 96,
             "steps": [11, 9, 9, 40, 16, 8, 30, 4]},   # atk1, atk2, atk3, death, idle, run, spin, take_hit
            {"path": _SP + "jump attack (220x96).png", "fw": 220, "fh": 96, "steps": [25]},
            {"path": _SP + "jump attack hit down (260x128).png", "fw": 260, "fh": 128, "steps": [40]},
        ],
        "actions": {"attack1": 0, "attack2": 1, "attack3": 2, "death": 3, "idle": 4,
                    "run": 5, "spin_attack": 6, "take_hit": 7,
                    "jump_attack": 8, "jump_attack_hit_down": 9, "jump": 4},   # jump=idle (placeholder)
        "echelle_sprite": 8.11,
        "pos_left": (320, 530), "pos_right": (1110, 530),
        "rect_size": (165, 275),   # envergure de la CHAIR/corps (560->721, hors noir des manches) = 161
                                   #   -> les deux bras (peau) dedans ; manches noirs + marteau + epee dehors
        "pivot_dx": -41,           # centre sur la chair ~640 (mediane pieds 681 faussee par l'epee ; le
                                   #   noir du manche a gauche est exclu) ; symetrique -> pas de saut au demi-tour
        # offset_left[0] = largeur_frame(170*8.11=1378) - offset_right[0] - largeur_rect
        "offset_face_right": [572, 296], "offset_face_left": [636, 296],
        "health": 460, "speed_base": 11, "gravit": 2.0, "jump": 0,
        "anim_cd": 55,   # cadence par defaut (ms/frame) pour les actions non listees
        # Attaques LENTES et lourdes (marteau/epee) : cadence 120 = ~4 gameframes/frame.
        "cadences": {"idle": 100, "run": 78, "death": 50, "take_hit": 45, "spin_attack": 45,
                     "jump_attack": 50, "attack1": 120, "attack2": 120, "attack3": 120},
        "attack1_cd": 14, "attack2_cd": 22, "attack3_cd": 22,
        # COMBO : Move1 -> Move2 -> Move1 = attack1 -> attack2 -> attack3 (sinon M1=atk1, M2=atk2).
        # fenetre_frames = 1 -> l'appui d'enchainement doit tomber sur la frame d'impact
        # (ou juste apres) : timing FRAME-PERFECT, combo dur a placer.
        "combo": {
            "base": {"move1": "attack1", "move2": "attack2"},
            "depart": "attack1",
            "chaines": {"attack1": ("move2", "attack2"), "attack2": ("move1", "attack3")},
            "fenetre_frames": 1,
        },
        # SPIN : Move1+Move2 APRES/pendant un deplacement -> toupie qui file dans le sens du
        # mouvement, INCONTROLABLE, MULTI-HIT (le marteau tourne autour, box centree). Actif des
        # frames 'first' a 'last' de spin_attack, un coup toutes les 'interval' frames.
        # SPIN = mobilite PHARE de Barrion (tres lent au sol). loop_start/loop_end = le MILIEU qui
        # tourbillonne (f9->f22) ; l'intro (f0-8 prep) et l'outro (f23-29 arret) ne sont joues
        # qu'UNE fois. loops = nb de tours du milieu (allonge la duree). first/last = fenetre de
        # hit (calee sur la boucle). speed = deplacement (SEULEMENT pendant la rotation).
        # double_window_ms = fenetre (ms) du DOUBLE-TAP de direction qui declenche le spin.
        # parry_window = fenetre (game-frames) ou un block FRAIS DE FACE (block_age) renvoie le spin.
        # loop_start/loop_end = UNE rotation COMPLETE (fi10 up-right -> ... -> fi17 up ; fi18 ~= fi10)
        # -> le rebouclage 17->10 raccorde sans "tp" de pose (avant 9->22 sautait le bas de rotation).
        "armure": (2, 3),   # SUPER ARMOR : marteau (atk2) + overhead (atk3) non interrompus
        "spin": {"speed": 16, "loops": 2, "loop_start": 10, "loop_end": 17, "first": 10, "last": 17,
                 "interval": 3, "damage": 12, "block_dmg": 11, "cd": 40,
                 "hitboxes": [(-210, 20, 420, 200)],
                 "double_window_ms": 300, "parry_window": 0, "garde_hold_ms": 300},
        # JUMP-ATTACK (Haut) : accroupi (f0-6) -> salto marteau en l'air (f7-15) -> atterri/recup.
        # Frames AU SOL (avant launch / apres land) : pieds poses au sol. Frames EN L'AIR
        # (launch..land) : PARABOLE (cloche) de hauteur 'peak' (px echelle-sprite). 'duration' =
        # duree TOTALE du saut en game-frames (timer continu -> vitesse librement reglable, fluide).
        # advance = avance horizontale/game-frame en vol. first/last = fenetre de hit (la hitbox
        # DESCEND jusqu'au sol pour toucher un ennemi au sol quand le marteau retombe).
        # SLAM (jump_attack_hit_down) : Move2 EN L'AIR pendant le jump-attack -> le marteau est LANCE
        # et frappe LE SOL devant (onde de choc). ANCRAGE "CELLULE AU SOL" (pose au cast, voir mvmt/
        # draw) : la cellule d'anim est calee pour que le POINT D'IMPACT du marteau (mediane des
        # pivots, stable) soit AU SOL. Le RECT (hitbox corps) SUIT LE CORPS mesure par frame
        # (_hd_body, tracking par composantes connexes precalcule au chargement) -> la boite reste
        # calee sur Barrion pendant TOUTE l'anim (flottement, ruee vers le marteau, atterrissage).
        # DEGATS "PREMIER CONTACT", deux sources INDEPENDANTES (chacune frappe UNE fois des que sa
        # box touche la cible) : le MARTEAU (hitboxes_down ancree sur le point d'impact au sol,
        # active hammer_from..hammer_to) et le CORPS qui percute en volant (son rect, jusqu'a body_to).
        "jump_attack": {"advance": 11, "duration": 64, "launch": 7, "land": 17, "peak": 170,
                        "first": 9, "last": 19, "interval": 2, "damage": 10, "block_dmg": 9, "cd": 22,
                        "hitboxes_right": [(-70, 10, 230, 250)], "hitboxes_left": [(-160, 10, 230, 250)],
                        "slam_start": 16, "hd_duration": 52, "cd_down": 28,
                        # marteau : EN VOL (slam_start..hammer_from, box qui SUIT le marteau lance,
                        # position precalculee _hd_hammer) puis IMPACT (hammer_from..hammer_to, box
                        # fixe au point de chute). UNE seule frappe au total (premier contact).
                        "hammer_from": 20, "hammer_to": 29, "damage_down": 14, "block_dmg_down": 13,
                        "hitboxes_hammer_fly": [(-115, -115, 230, 230)],
                        "hitboxes_down": [(-150, -270, 300, 290)],
                        "body_to": 30, "damage_fly": 9, "block_dmg_fly": 8},
        # anims ou le perso est dessine DECALE dans le cadre -> ancrage PAR FRAME sur les PIEDS.
        "anims_par_frame": ["spin_attack"],
        # anim AERIENNE (salto) : ancrage CENTROIDE + rect qui suit l'arc (anim-driven).
        # (jump_attack_hit_down n'y est PAS : le slam a son propre ancrage "cellule au sol",
        #  pose au cast -- voir mvmt/draw.)
        "anims_arc": ["jump_attack"],
        "title": "assets/characters/Old Knight of the crown/title.png", "title_size": (300, 45),
        "title_x_right": 1270,
        # NB: hy compenses de -45 (rect 320 -> 275) pour garder les hitbox a leur hauteur d'origine.
        # attack2 a h=None (= hauteur du rect) -> couvre le corps, laisse tel quel.
        "attacks": {
            # attack1 = estoc a l'epee (thrust horizontal ~f5, portee longue, hauteur poitrine).
            1: {"frame": 5, "damage": 15, "block_dmg": 15,
                "hitboxes_right": [(0, 75, 380, 100)], "hitboxes_left": [(-380, 75, 380, 100)]},
            # impact a f2 : l'arc du marteau frappe le sol DES f2 (mesure ; f6 = bien trop tard,
            # degats/sons arrivaient ~0.5s apres l'impact visuel).
            2: {"frame": 2, "damage": 22, "block_dmg": 24,
                "hitboxes_right": [(0, 0, 260, None)], "hitboxes_left": [(-260, 0, 260, None)]},
            # attack3 = gros coup de marteau overhead qui tourne et s'abat devant (~f4,
            # arc haut + avant, portee ~340). Finisher lourd.
            3: {"frame": 4, "damage": 24, "block_dmg": 22,
                "hitboxes_right": [(0, -125, 340, 420)], "hitboxes_left": [(-340, -125, 340, 420)]},
        },
    }


class TrainingDummy(Fighter2):
    """Mannequin d'entrainement : FIXE, PASSIF, INVINCIBLE. Rejoue 'hit' quand on le
    frappe, et joue 'death' (puis reapparait) quand on appelle .tuer() (combo reussi).
    Sheet 2048x192, frames 64x64, 3 lignes : idle(2), hit(18), death(17)."""

    POUSSABLE = False   # FIXE par design : le recul d'impact (poussee) ne le deplace pas

    _SP = "assets/characters/Training Dummy/Training Dummy.png"
    # Sheet 2048x192 : frames de 128px de LARGE (le sujet ~64px est centre dans chaque cellule),
    # 3 lignes de 64px de haut -> idle(1), hit(9), death(16). (lire en 64px coupait chaque pose
    # en deux -> le mannequin clignotait.) Frame ou la partie haute brisee touche le sol = 2.
    FALL_FRAME = 2
    CONFIG = {
        "sheets": [{"path": _SP, "fw": 128, "fh": 64, "steps": [1, 9, 16]}],
        # run/jump/attack* mappes sur idle (jamais utilises : mannequin passif).
        "actions": {"idle": 0, "take_hit": 1, "death": 2,
                    "run": 0, "jump": 0, "attack1": 0, "attack2": 0},
        "echelle_sprite": 4.6,
        "pos_left": (1120, 560), "pos_right": (1120, 560),
        "rect_size": (120, 300),
        "offset_face_right": [0, 0], "offset_face_left": [0, 0],   # ignore (Fighter2 aligne auto)
        "health": 1_000_000, "speed_base": 0, "gravit": 0, "jump": 0,
        "anim_cd": 60,
        "cadences": {"idle": 130, "take_hit": 45, "death": 60},
        "attack1_cd": 1, "attack2_cd": 1,
        "title": None,                 # pas de banniere de nom
        "attacks": {},                 # ne frappe pas
    }

    def __init__(self, side, flip_state):
        super().__init__(side, flip_state)
        self.mourant = False
        self.respawn_timer = 0

    def tuer(self):
        """Declenche l'anim de mort (feedback de combo reussi) puis un respawn."""
        if not self.mourant:
            self.mourant = True
            self.hit = False
            self.frame_index = 0
            self.update_time = temps_ms()
            self.respawn_timer = 24     # ~0,8 s sur la derniere frame avant de reapparaitre

    def mvmt(self, surface, target):
        pass                            # fixe et passif

    def update(self, surface, target):
        A = self.actions
        if self.hit and grace_active(self):
            self.hit = False               # meme regle de grace que les persos (fidele au versus)
            self._hit_amorti = True        # sons d'impact joues quand meme
        if self.mourant:
            self.update_action(A["death"])
        elif self.hit:
            self.update_action(A["take_hit"])
        else:
            self.update_action(A["idle"])
        animation_cd = self._cd_idx.get(self.action, self.config.get("anim_cd", 99))
        self.image = self.animation_list[self.action][self.frame_index]
        if temps_ms() - self.update_time > animation_cd:
            self.frame_index += 1
            self.update_time = temps_ms()
        if self.frame_index >= len(self.animation_list[self.action]):
            if self.mourant:
                self.frame_index = len(self.animation_list[self.action]) - 1
                self.respawn_timer -= 1
                if self.respawn_timer <= 0:      # REAPPARAIT frais
                    self.mourant = False
                    self.frame_index = 0
                    self.update_action(A["idle"])
            else:
                self.frame_index = 0
                if self.action == A["take_hit"]:
                    self.hit = False             # fin du hit -> retour idle
                    self._grace = temps_actif_ms() + GRACE_MS   # meme grace que les persos
        self.health = self.max_health            # INVINCIBLE : PV toujours pleins
        self.alive = True

    def draw(self, surface):
        debug_box(self.rect, "hitbox")
        debug_cds(surface, self)
        oxr, oxl, oy = self._off.get(self.action, (0, 0, 0))
        ox = oxl if self.flip else oxr
        e = getattr(self, "echelle", 1.0)
        img = pygame.transform.flip(sprite_nuit(self.image), self.flip, False)
        if grace_active(self):
            img.set_alpha(130 if (temps_ms() // 66) % 2 else 220)   # grace : le sprite clignote
        # +5 px : la base du mannequin flottait un poil au-dessus de l'herbe (ajust visuel)
        surface.blit(img, (self.rect.x - ox * e, self.rect.y - oy * e + 5 * e))
        # Bouclier quand il bloque (mode Shield du Custom Dummy) : sprite selon l'usure, comme
        # les persos. Maintenu qq frames apres un coup pris de dos en gardant (garde_hold) pour
        # qu'il ne clignote pas pendant l'anim de get-hit.
        if self.block or temps_ms() < getattr(self, "_garde_hold", 0):
            shield = (self.shield_sprite2 if self.block_health >= BOUCLIER_MAX * 0.66
                      else self.shield_sprite1 if self.block_health >= BOUCLIER_MAX * 0.33
                      else self.shield_sprite0)
            si = pygame.transform.flip(sprite_nuit(shield), self.flip, False)
            surface.blit(si, (self.rect.centerx - si.get_width() // 2,
                              self.rect.centery - si.get_height() // 2))


# ======================================================================
#  MOVES GUIDE (mode Entrainement) -- les COUPS SIGNATURES de chaque perso.
#  Source unique pour le panneau "Moves Guide" du training (TSOG Game).
#  Chaque entree : nom affiche, sequence de badges (libelles libres),
#  note d'une ligne, et detect(joueur, degats) -> True a l'instant ou le
#  move vient d'etre REUSSI sur le mannequin. Les moves "a degats" testent
#  degats>0 + le flag du move ; les moves "action" (teleport, esquive,
#  changement d'arme...) se valident sur le FRONT MONTANT de leur etat
#  (via _front, qui stocke l'etat precedent SUR le fighter -> reset auto
#  a chaque changement de perso puisque l'instance est recreee).
# ======================================================================

def _front(f, attr, cle):
    """True uniquement a l'INSTANT ou f.<attr> passe a True (front montant)."""
    prev = getattr(f, cle, False)
    cur = bool(getattr(f, attr, False))
    setattr(f, cle, cur)
    return cur and not prev


def _d_changement_arme(f, degats):
    prev = getattr(f, "_gd_arme", None)
    cur = getattr(f, "current_weapon", None)
    f._gd_arme = cur
    return prev is not None and cur is not None and cur != prev


def _d_ramassage_lance(f, degats):
    prev = getattr(f, "_gd_lance", None)
    cur = getattr(f, "has_spear", None)
    f._gd_lance = cur
    return prev is False and cur is True


def _d_dodge_cancel(f, degats):
    prev = getattr(f, "_gd_cancel", 0)
    cur = getattr(f, "nb_cancels", 0)
    f._gd_cancel = cur
    return cur > prev


def _d_ancrage(f, degats):
    prev = getattr(f, "_gd_ancr", 0)
    cur = getattr(f, "_ancrage_nb", 0)
    f._gd_ancr = cur
    return cur > prev


_M_ESQUIVE = {"nom": "Dodge", "seq": ["UP"],
              "note": "Invulnerable hop, always away from the enemy",
              "detect": lambda f, d: _front(f, "dodging", "_gd_dodge"),
              "prog": lambda f: 1 if getattr(f, "dodging", False) else 0}

# "prog" (optionnel) : nb de badges ACCOMPLIS "en ce moment" (0..len(seq)), lu
# chaque frame par le panneau -> les badges passent en bleu au fil du move
# (comme la profondeur des combos). Sans prog : validation par le flash Success.
MOVES_GUIDE = {
    "Kenshi": [
        {"nom": "Blade Through", "seq": ["UP  (close)", "M1 / M2"],
         "note": "Up close your dodge cuts THROUGH the foe - strike his back for +50%",
         "detect": lambda f, d: d > 0 and getattr(f, "_lame_derniere", 0) == 2,
         "prog": lambda f: 1 if (f.lame_niveau() == 2 or getattr(f, "_lame_atk", 0) == 2) else 0},
        {"nom": "First Blood", "seq": ["UP", "M1 / M2"],
         "note": "Any clean dodge sharpens your next strike (+25%) for a moment",
         "detect": lambda f, d: d > 0 and getattr(f, "_lame_derniere", 0) >= 1,
         "prog": lambda f: 1 if (f.lame_niveau() >= 1 or getattr(f, "_lame_atk", 0) >= 1) else 0},
        {"nom": "Dodge Cancel", "seq": ["M1 / M2", "UP"],
         "note": "Cut your own attack short with a dodge - escape a bad call",
         "detect": _d_dodge_cancel,
         "prog": lambda f: 1 if getattr(f, "attacking", False) else 0},
        {"nom": "Flow", "seq": ["HIT", "HIT", "HIT"],
         "note": "Landed hits quicken Kenshi (max 3) - fades if idle, lost if he bleeds",
         "detect": lambda f, d: getattr(f, "flow", 0) >= 3,
         "prog": lambda f: min(3, getattr(f, "flow", 0))},
        {"nom": "Execution", "seq": ["FOE UNDER 25% HP"],
         "note": "+30% damage against a weakened foe - end the duel",
         "detect": lambda f, d: d > 0 and getattr(f, "_exec_active", False)},
    ],
    "Lysandra": [
        {"nom": "Seismic Blow", "seq": ["HOLD M2", "RELEASE"],
         "note": "Freeze the windup to charge (x2.2 max) - fully charged, NO guard or parry stops it",
         "detect": lambda f, d: d > 0 and getattr(f, "_seisme_conso", 1.0) > 1.05,
         "prog": lambda f: 1 if getattr(f, "_seisme_gel", False) else 0},
        {"nom": "Grinding March", "seq": ["WALK AT FOE", "HIT"],
         "note": "Marching at the foe loads her blow fast (up to +50%) - stopping bleeds it away",
         "detect": lambda f, d: d > 0 and getattr(f, "_marche_conso", 0) >= 0.5,
         "prog": lambda f: 1 if getattr(f, "poids", 0) >= f.POIDS_MAX_F * 0.5 else 0},
        {"nom": "Unbreakable", "seq": ["M1 / M2"],
         "note": "Her swings can NOT be interrupted - she trades pain for pain",
         "detect": lambda f, d: _front(f, "_armure_abs", "_gd_armure")},
        {"nom": "Anchored", "seq": ["BLOCK"],
         "note": "Blocking or charging, she cannot be pushed an inch",
         "detect": _d_ancrage},
        {"nom": "Slow Wrath", "seq": ["UNDER 60% HP"],
         "note": "The more she bleeds, the harder she hits (up to +35%)",
         "detect": lambda f, d: d > 0 and getattr(f, "_colere_active", False)},
    ],
    "Konrad": [
        {"nom": "Weapon Switch", "seq": ["M2"],
         "note": "Cycle rapier > spear > heavy sword > mace",
         "detect": _d_changement_arme},
        dict(_M_ESQUIVE),
    ],
    "Arinya": [
        {"nom": "Charged Spear Throw", "seq": ["HOLD M2", "RELEASE"],
         "note": "Longer charge = faster, deadlier spear",
         "detect": lambda f, d: d > 0 and getattr(f, "spear", None) is not None,
         "prog": lambda f: (2 if (getattr(f, "throwing", False)
                                  or getattr(f, "spear", None) is not None)
                            else 1 if getattr(f, "charging", False) else 0)},
        {"nom": "Spear Pickup", "seq": ["WALK ON IT"],
         "note": "Bare-handed after a throw - reclaim your spear on the ground",
         "detect": _d_ramassage_lance},
        {"nom": "Dash", "seq": ["<<  <<", ">>  >>"], "sep": "or",
         "note": "Double-tap a direction - quick burst to close in or retreat",
         "detect": lambda f, d: _front(f, "dashing", "_gd_dash"),
         "prog": lambda f: 2 if getattr(f, "dashing", False) else 0},
        dict(_M_ESQUIVE),
    ],
    "Stormr": [
        {"nom": "Static Charge", "seq": ["M1 / M2"],
         "note": "Every hit charges the enemy - fill the gauge to unlock the lightning",
         "detect": lambda f, d: d > 0 and getattr(f, "enemy_charge", 0) > 0,
         "prog": lambda f: 1 if getattr(f, "enemy_charge", 0) > 0 else 0},
        dict(_M_ESQUIVE),
    ],
    "Oswald": [
        {"nom": "Teleport", "seq": ["UP"],
         "note": "Blink through your foe - this IS your escape, no dodge",
         "detect": lambda f, d: _front(f, "teleporting", "_gd_tp"),
         "prog": lambda f: 1 if getattr(f, "teleporting", False) else 0},
    ],
    "Barrion": [
        {"nom": "Spin", "seq": ["<<  <<", ">>  >>"], "sep": "or",
         "note": "Double-tap a direction - beware, a FRESH shield reflects it",
         "detect": lambda f, d: d > 0 and getattr(f, "spinning", False),
         "prog": lambda f: 2 if getattr(f, "spinning", False) else 0},
        {"nom": "Hammer Leap", "seq": ["UP"],
         "note": "Leaping hammer strike - the old knight has no dodge",
         "detect": lambda f, d: (d > 0 and getattr(f, "jumping_attack", False)
                                 and not getattr(f, "hit_down", False)),
         "prog": lambda f: (1 if (getattr(f, "jumping_attack", False)
                                  and not getattr(f, "hit_down", False)) else 0)},
        {"nom": "Sky Slam", "seq": ["UP", "AIR M2"],
         "note": "Crash down mid-leap - the hammer shakes the ground",
         "detect": lambda f, d: d > 0 and getattr(f, "hit_down", False),
         "prog": lambda f: (2 if getattr(f, "hit_down", False)
                            else 1 if getattr(f, "jumping_attack", False) else 0)},
    ],
}

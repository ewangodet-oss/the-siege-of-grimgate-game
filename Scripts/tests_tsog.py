# ======================================================================
#  TESTS AUTOMATIQUES DE TSOG -- filet de securite anti-regression.
#
#  Lance le jeu SANS fenetre (headless), simule des appuis de touches et
#  verifie que les mecaniques cles marchent toujours. A lancer apres
#  CHAQUE modification (equilibrage, rework, fix) :
#
#      python tests_tsog.py          (ou F5 dans Spyder)
#
#  Sortie : OK / ECHEC par test + bilan final. Si un test echoue, c'est
#  tres probablement la DERNIERE modification qui a casse cette mecanique
#  -> on le sait tout de suite, pas trois semaines plus tard en jouant.
# ======================================================================
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
_ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ICI)
os.chdir(os.path.dirname(_ICI))            # racine du jeu (les assets sont relatifs)

import pygame                               # noqa: E402
pygame.init()
pygame.display.set_mode((1600, 900))
import classes                              # noqa: E402
classes.set_sol(850)

SURF = pygame.Surface((1600, 900), pygame.SRCALPHA)
TESTS = []


def test(nom):
    def deco(fn):
        TESTS.append((nom, fn))
        return fn
    return deco


# ---------------------------------------------------------------- outils
# Les persos sont LONGS a charger (sheets scalees, tracking du slam...) ->
# on garde UNE instance par classe (cache) et on la remet a neuf entre les
# essais. Un attribut oublie ici ferait ECHOUER un test (visible), pas un
# bug silencieux -> acceptable pour un outil de test.
_RESET = dict(attacking=False, attack_cd=0, hit=False, block=False, block_cd=0,
              block_health=0, jump=False, vel_y=0, running=False, damage_dealt=False,
              blocked_hit=False, combo_step=0, combo_queued=None, t_combo=0,
              a1_prev=False, a2_prev=False, up_prev=False,
              spinning=False, spin_cd=0, _spin_hit_f=-99, _spin_parried=False,
              _spin_loops=0, _l_tap_ms=-999, _r_tap_ms=-999, _l_prev=False, _r_prev=False,
              jumping_attack=False, _ja_timer=0, _ja_hit_f=-99,
              hit_down=False, _hd_timer=0, _hd_hammer_hit=False, _hd_body_hit=False,
              _hd_prev=None, teleporting=False, _tp_timer=0, tp_cd=0,
              frame_index=0, update_time=0, _garde_hold=0, block_age=999, _block_prev=False,
              # Stormr / Arinya / Konrad
              enemy_charge=0.0, lightning=None, t_atk1=0, t_atk2=0, attack_action=None,
              atk_prev=False, throw_prev=False, charging=False, throwing=False, charge=0,
              combo_first_time=0, spear=None, has_spear=True, attack_type=0,
              dash_cd=0, dashing=False, switch_cd=0, sword=None, _grace=0, _fx_parade=0,
              # esquive (Haut) : saut bref invulnerable (tap arriere / double-tap avant)
              dodging=False, dodge_cd=0, dodge_iframes=0, dodge_timer=0, _dodge_up_prev=False,
              _dodge_fwd=False, _dodge_lift=0.0, dodge_dir=-1,
              _dodge_dur=0, _dodge_speed=0, _dodge_hop=0,
              # kit Kenshi (passe-lame / flow / execution / dodge-cancel)
              flow=0, _lame_fin=0, _lame_niv=0, _lame_derniere=0, _lame_atk=0,
              _exec_active=False, cancel_cd=0, nb_cancels=0, _pl_depart=None,
              _pl_croise=False, _pl_dodge_prev=False, _flow_hp=None, _trail=None,
              _dodge_trav=False, _flow_t=0,
              # kit Lysandra (seisme / marche / ancrage / colere / armure)
              poids=0, _seisme_arme=False, _seisme_gel=False, _seisme_t0=0,
              _seisme_mult=1.0, _seisme_perce=False, _seisme_lache=False,
              _seisme_conso=1.0, _marche_conso=0.0, _colere_active=False,
              _armure_abs=False, _ancrage_nb=0, _gd_armure=False, _gd_ancr=0,
              # dummy
              mourant=False, respawn_timer=0)
_CACHE = {}


def perso(cls, x=500, flip=False):
    if cls not in _CACHE:
        p = cls("Left", False)
        p.echelle = 1.0
        _CACHE[cls] = p
    p = _CACHE[cls]
    for k, v in _RESET.items():
        if hasattr(p, k):
            setattr(p, k, v)
    p.health = p.max_health
    p.alive = True
    p.flip = flip
    p.rect.bottom = classes.SOL
    p.rect.centerx = x
    return p


def dummy(x=1400):
    d = perso(classes.TrainingDummy, x)
    d.flip = True
    return d


def simule(p, d, script, frames=120, chaque=None):
    """Simule un combat headless. script = {frame: {"up": True, "move1": True, ...}} ;
    chaque(f, p, d, deg) est appele apres chaque frame si fourni -- deg = degats infliges
    au dummy CETTE frame (mesures AVANT sa regeneration : il est invincible)."""
    classes.reset_horloge()
    classes.reset_horloge_active()
    for f in range(frames):
        classes.avancer_horloge()
        classes.avancer_horloge_active()
        i = classes.Inputs()
        for k, v in script.get(f, {}).items():
            setattr(i, k, v)
        p.inputs = i
        p.mvmt(SURF, d)
        hp_avant = d.health
        p.update(SURF, d)
        deg = hp_avant - d.health
        d.update(SURF, p)
        if chaque:
            chaque(f, p, d, deg)


# ---------------------------------------------------------------- tests
# memes classes que PERSONNAGES de "TSOG Game.py" (non importable ici : espace dans le nom)
_PERSOS = (classes.Kenshi, classes.Lysandra, classes.KonradForgeval, classes.Arinya,
           classes.Stormr, classes.Oswald, classes.Barrion)


@test("chargement de tous les persos jouables")
def t_chargement():
    for cls in _PERSOS:
        p = perso(cls)
        assert p.animation_list and p.max_health > 0, cls.__name__


@test("pieds au sol : chaque perso est dessine PILE sur le sol (offsets calibres)")
def t_pieds_au_sol():
    s = pygame.Surface((1600, 900), pygame.SRCALPHA)
    for cls in _PERSOS:
        p = perso(cls, 800)
        p.title_hidden = True
        s.fill((0, 0, 0, 0))
        p.draw(s)
        bb = s.get_bounding_rect()
        ecart = bb.bottom - classes.SOL
        assert abs(ecart) <= 2, "%s : pieds a %+d px du sol (offset vertical a recaler)" % (
            cls.__name__, ecart)


@test("PV : constante de classe == PV reels (source unique)")
def t_hp_source_unique():
    for cls in (classes.KonradForgeval, classes.Arinya, classes.Stormr):
        assert perso(cls).max_health == cls.HEALTH, cls.__name__
    for cls in (classes.Oswald, classes.Barrion):
        assert perso(cls).max_health == cls.CONFIG["health"], cls.__name__


def _croise_combo(cls, btn1, btn2, detecte, lo, hi):
    """FIDELITE du Combo Trainer : la barre doit etre VERTE exactement sur les frames
    d'appui qui enchainent REELLEMENT (combo_timing == logique de mvmt)."""
    for fp in range(lo, hi):
        p = perso(cls)
        d = dummy()
        etat = {"ench": False, "vert": None}
        classes.reset_horloge()
        classes.reset_horloge_active()
        for f in range(90):
            classes.avancer_horloge()
            classes.avancer_horloge_active()
            i = classes.Inputs()
            if f == 2:
                setattr(i, btn1, True)
            if f == fp:
                setattr(i, btn2, True)
                tim = p.combo_timing()
                etat["vert"] = tim is not None and tim[1] <= tim[3] < tim[2]
            p.inputs = i
            p.mvmt(SURF, d)
            p.update(SURF, d)
            d.update(SURF, p)
            if detecte(p):
                etat["ench"] = True
        assert etat["ench"] == bool(etat["vert"]), \
            "appui a f=%d : enchaine=%s mais barre verte=%s" % (fp, etat["ench"], etat["vert"])


@test("combo trainer fidele : Barrion (frame-perfect)")
def t_combo_barrion():
    _croise_combo(classes.Barrion, "move1", "move2",
                  lambda p: p.action == p.actions["attack2"] and p.attack_type == 2, 19, 34)


@test("combo trainer fidele : Oswald (frame-perfect)")
def t_combo_oswald():
    _croise_combo(classes.Oswald, "move1", "move2",
                  lambda p: p.action == p.actions["attack2"] and getattr(p, "combo_step", 0) >= 2, 16, 34)


@test("combo trainer fidele : Stormr (fenetre en temps)")
def t_combo_stormr():
    _croise_combo(classes.Stormr, "move1", "move2",
                  lambda p: getattr(p, "attack_action", None) == p.ATTACK2
                  and getattr(p, "combo_step", 0) == 2, 7, 18)


@test("combo trainer fidele : Arinya (fenetre en temps)")
def t_combo_arinya():
    _croise_combo(classes.Arinya, "move1", "move1",
                  lambda p: getattr(p, "attack_type", 0) == 2, 7, 17)


@test("spin Barrion : double-tap declenche dans les 2 sens + multi-hit + fin au sol")
def t_spin():
    for direction, dx_att in (("right", 1), ("left", -1)):
        b = perso(classes.Barrion, 800)
        d = dummy(800 + 220 * dx_att)
        etat = {"spin": False, "hits": 0}

        def chaque(f, p, dd, deg, etat=etat):
            if p.spinning:
                etat["spin"] = True
            if deg > 0:
                etat["hits"] += 1
        simule(b, d, {2: {direction: True}, 5: {direction: True}}, frames=100, chaque=chaque)
        assert etat["spin"], "spin pas declenche (%s)" % direction
        assert etat["hits"] >= 3, "multi-hit attendu, %d coups (%s)" % (etat["hits"], direction)
        assert not b.spinning and b.rect.bottom == classes.SOL, "fin de spin incorrecte"
        # tap simple = PAS de spin
    b = perso(classes.Barrion, 800)
    d = dummy()
    etat = {"spin": False}

    def chaque2(f, p, dd, deg):
        if p.spinning:
            etat["spin"] = True
    simule(b, d, {2: {"right": True}}, frames=30, chaque=chaque2)
    assert not etat["spin"], "un tap simple ne doit PAS declencher le spin"


@test("spin Barrion : renvoi FRAME PERFECT de face (age 0 renvoie, age 1 non)")
def t_spin_parade():
    b = perso(classes.Barrion, 700)
    d = dummy(900)
    b.spinning = True
    b.frame_index = 12
    b.spin_dir = 1
    d.block = True
    d.block_age = 0
    d.flip = True                       # face au spin
    b._spin_update(d)
    assert b._spin_parried and b.spin_dir == -1, "parade frame-perfect de face doit RENVOYER le spin"
    # FRAME PERFECT PUR (parry_window 0) : UNE frame de retard = garde TENUE -> chip
    # seulement, PAS de renvoi -> verrouille la fenetre contre tout relachement futur.
    b = perso(classes.Barrion, 700)
    d = dummy(900)
    b.spinning = True
    b.frame_index = 12
    b.spin_dir = 1
    d.block = True
    d.block_age = 1
    d.flip = True
    b._spin_update(d)
    assert not b._spin_parried and b.spin_dir == 1, "block_age 1 ne doit PLUS renvoyer le spin"


@test("spin Barrion : invulnerable (l'attaquant prend l'effet bouclier)")
def t_spin_invuln():
    b = perso(classes.Barrion, 800)
    b.spinning = True
    o = perso(classes.Oswald, 760)
    atk = o.config["attacks"][1]
    o.update_action(o.actions["attack1"])
    o.frame_index = atk["frame"]
    hp0 = b.health
    o.check_collision(SURF, b)
    assert b.health == hp0 and not b.hit, "cible en spin = 0 degat"
    assert o.blocked_hit, "toucher un spin = effet bouclier pour l'attaquant"


@test("jump-attack Barrion : touche, multi-hit, atterrit au sol")
def t_jump():
    b = perso(classes.Barrion, 500)
    d = dummy(700)
    etat = {"hits": 0}

    def chaque(f, p, dd, deg):
        if deg > 0:
            etat["hits"] += 1
    simule(b, d, {2: {"up": True}}, frames=100, chaque=chaque)
    assert etat["hits"] >= 2, "multi-hit du saut attendu, %d coups" % etat["hits"]
    assert not b.jumping_attack and b.rect.bottom == classes.SOL, "doit finir au sol"


@test("slam Barrion : marteau (impact au sol) + corps (balayage), fin a l'impact")
def t_slam():
    # dummy au POINT D'IMPACT -> marteau (damage_down) PUIS corps qui atterrit dessus
    b = perso(classes.Barrion, 300)
    d = dummy(1350)
    coups = []

    def chaque(f, p, dd, deg):
        if deg > 0:
            coups.append(int(deg))
    # declenche le slam en l'air : fronts de move2 espaces pendant le vol (gf ~21-48)
    script = {2: {"up": True}, 24: {"move2": True}, 30: {"move2": True}, 36: {"move2": True}}
    simule(b, d, script, frames=115, chaque=chaque)
    ja = b.config["jump_attack"]
    assert b._hd_hammer_hit, "le marteau doit toucher au point d'impact"
    assert ja["damage_down"] in coups, "degats du marteau attendus %s, coups=%s" % (ja["damage_down"], coups)
    assert b.rect.bottom == classes.SOL, "doit finir au sol"
    # dummy a MI-TRAJECTOIRE -> seul le CORPS le percute (balayage anti-tunneling)
    b = perso(classes.Barrion, 300)
    d = dummy(700)
    simule(b, d, script, frames=115)
    assert b._hd_body_hit and not b._hd_hammer_hit, "corps doit percuter, pas le marteau"


@test("teleport Oswald : saut INSTANTANE (pas un glissement), fin au sol, recharge")
def t_teleport():
    o = perso(classes.Oswald, 400)
    d = dummy()
    x0 = o.rect.centerx
    pas = []

    def chaque(f, p, dd, deg, prev=[x0]):
        pas.append(abs(p.rect.centerx - prev[0]))
        prev[0] = p.rect.centerx
    simule(o, d, {2: {"up": True}}, frames=40, chaque=chaque)
    dist = o.config["teleport"]["distance"]
    assert abs((o.rect.centerx - x0) - dist) < 30, "distance %d, attendu ~%d" % (o.rect.centerx - x0, dist)
    assert max(pas) >= dist * 0.9, "VRAI TP attendu : tout le deplacement en 1 frame (max pas=%d)" % max(pas)
    assert not o.teleporting and o.rect.bottom == classes.SOL, "doit finir au sol"
    # tp OFFENSIF : l'attaque revient presque tout de suite, c'est le RE-tp qui recharge
    assert o.attack_cd <= o.config["teleport"]["cd_attaque"], "attaque quasi immediate en sortie de tp"
    assert o.tp_cd > 0, "le re-teleport doit etre en recharge (tp_cd)"
    # vers la gauche avec la touche tenue
    o = perso(classes.Oswald, 1000)
    x0 = o.rect.centerx
    simule(o, d, {2: {"up": True, "left": True}}, frames=40)
    assert (o.rect.centerx - x0) < -dist * 0.8, "teleport vers la gauche attendu"


@test("teleport Oswald : INVULNERABLE (les coups ratent, sans effet bouclier)")
def t_teleport_invuln():
    o = perso(classes.Oswald, 600)
    o.teleporting = True
    b = perso(classes.Barrion, 760, flip=True)
    atk = b.config["attacks"][1]
    b.update_action(b.actions["attack1"])
    b.frame_index = atk["frame"]
    hp0 = o.health
    b.check_collision(SURF, o)
    assert o.health == hp0 and not o.hit, "cible en teleport = 0 degat"
    assert not b.blocked_hit, "esquive : PAS d'effet bouclier pour l'attaquant"


@test("grace post-stun : un 2e coup rapproche blesse mais ne RE-stun pas")
def t_grace():
    b = perso(classes.Barrion, 760, flip=True)
    o = perso(classes.Oswald, 600)
    atk = b.config["attacks"][1]

    def coup():
        b.update_action(b.actions["attack1"])
        b.attacking = True
        b.frame_index = atk["frame"]
        b.damage_dealt = False
        b.check_collision(SURF, o)
    classes.reset_horloge()
    classes.reset_horloge_active()
    classes.avancer_horloge()
    classes.avancer_horloge_active()
    coup()
    assert o.hit, "1er coup : stun normal"
    o.hit = False
    o._grace = classes.temps_actif_ms() + classes.GRACE_MS   # comme apres la fin du take_hit
    hp = o.health
    coup()
    o.update(SURF, b)      # le filtre anti stun-lock s'applique dans update
    assert o.health < hp, "les degats du 2e coup passent"
    assert not o.hit, "mais PAS de re-stun pendant la grace"


@test("parade parfaite : FRAME PERFECT (age 0 pare, age 1 non), attaquant repousse")
def t_parade():
    b = perso(classes.Barrion, 760, flip=False)      # attaquant a gauche
    o = perso(classes.Oswald, 900, flip=True)        # garde face a lui, PILE a l'impact
    o.block = True
    o.block_age = 0
    atk = b.config["attacks"][1]
    b.update_action(b.actions["attack1"])
    b.attacking = True
    b.frame_index = atk["frame"]
    b.damage_dealt = False
    x0 = b.rect.centerx
    hp = o.health
    b.check_collision(SURF, o)
    assert o.health == hp and o.block_health == 0, "parade parfaite : ni degat ni usure"
    assert b.blocked_hit and b.rect.centerx < x0, "attaquant penalise et REPOUSSE"
    assert o._fx_parade > 0, "flash de parade signale"
    # FRAME PERFECT PUR : UNE frame de retard (block_age 1) = blocage NORMAL (chip + usure),
    # PAS de parade -> verrouille PARADE_FENETRE = 0 contre tout relachement futur.
    b.damage_dealt = False
    o.block_age = 1
    b.check_collision(SURF, o)
    assert o.block_health > 0 and o.health < hp, "1 frame de retard ne doit PLUS parer"


@test("super armor : une attaque lourde n'est pas interrompue")
def t_armure():
    b = perso(classes.Barrion, 600)
    b.attacking = True
    b.attack_type = 2                                # marteau = attaque lourde (config armure)
    b.update_action(b.actions["attack2"])
    o = perso(classes.Oswald, 760, flip=True)
    atk = o.config["attacks"][1]
    o.update_action(o.actions["attack1"])
    o.attacking = True
    o.frame_index = atk["frame"]
    o.damage_dealt = False
    hp = b.health
    o.check_collision(SURF, b)
    b.update(SURF, o)
    assert b.health < hp, "les degats passent"
    assert not b.hit and b.attacking, "mais l'attaque lourde CONTINUE (pas de stun)"


@test("recul d'impact : un coup non bloque repousse la cible")
def t_poussee():
    b = perso(classes.Barrion, 700, flip=False)
    o = perso(classes.Oswald, 860)
    atk = b.config["attacks"][1]
    b.update_action(b.actions["attack1"])
    b.attacking = True
    b.frame_index = atk["frame"]
    b.damage_dealt = False
    x0 = o.rect.centerx
    b.check_collision(SURF, o)
    assert o.rect.centerx > x0, "la cible doit reculer sous l'impact"
    d = dummy(900)                                   # le mannequin, lui, reste FIXE
    b.damage_dealt = False
    xd = d.rect.centerx
    b.check_collision(SURF, d)
    assert d.rect.centerx == xd, "le dummy n'est pas poussable (fixe par design)"


@test("bouclier vivant : l'usure se resorbe hors blocage, garde brisee = sonne")
def t_bouclier():
    o = perso(classes.Oswald, 500)
    d = dummy()
    o.block_health = 30
    o._block_prev = False
    simule(o, d, {}, frames=60)
    assert o.block_health < 30, "l'usure doit se resorber hors blocage"
    o = perso(classes.Oswald, 500)
    o.block_health = classes.BOUCLIER_MAX + 1         # usure pleine -> bris au prochain tick
    simule(o, d, {}, frames=2)
    assert o.block_cd > 0, "garde brisee : recharge lancee"
    assert o.hit or o.action == o.actions["take_hit"], "garde brisee : le defenseur est SONNE"


@test("nerf bloc : un coup bloque laisse passer BLOC_CHIP des degats (pas 10%)")
def t_bloc_chip():
    # Bloc NORMAL (garde tenue, PAS frame-perfect) : block_age eleve -> chip + usure.
    b = perso(classes.Barrion, 760, flip=False)       # attaquant a gauche
    o = perso(classes.Oswald, 900, flip=True)         # garde TENUE (pas fraiche)
    o.block = True
    o.block_age = 30
    atk = b.config["attacks"][1]
    b.update_action(b.actions["attack1"])
    b.attacking = True
    b.frame_index = atk["frame"]
    b.damage_dealt = False
    hp = o.health
    b.check_collision(SURF, o)
    perte = hp - o.health
    attendu = atk["damage"] * classes.BLOC_CHIP
    assert abs(perte - attendu) < 0.5, f"chip = damage*BLOC_CHIP (attendu {attendu}, perte {perte})"
    assert not o.hit, "un coup bloque ne stun pas (le bouclier tient)"


@test("sword of justice : tombe apres attack3, touche l'immobile, esquivable en bougeant")
def t_sword():
    # cible IMMOBILE sous l'epee -> prend les degats (une fois)
    o = perso(classes.Oswald, 500)
    d = dummy(650)
    etat = {"deg": 0}

    def chaque(f, p, dd, deg):
        if deg > 0:
            etat["deg"] = max(etat["deg"], deg)
    classes.reset_horloge()
    classes.reset_horloge_active()
    o._demarrer_attaque("attack3")
    simule(o, d, {}, frames=140, chaque=chaque)
    assert etat["deg"] >= o.config["sword"]["damage"], \
        "l'epee doit frapper une cible immobile (max deg=%s)" % etat["deg"]
    assert o.sword is None, "l'epee doit s'eteindre a la fin"
    # cible qui S'ELOIGNE pendant la charge -> esquive (aucun degat de l'epee)
    o = perso(classes.Oswald, 500)
    d = dummy(650)
    etat2 = {"hits": 0}

    def chaque2(f, p, dd, deg):
        if p.sword is not None and not p.sword["dmg"]:
            dd.rect.centerx = 1300                     # il s'ecarte pendant la charge au sol
        if deg > 0:
            etat2["hits"] += 1
    o._demarrer_attaque("attack3")
    simule(o, d, {}, frames=140, chaque=chaque2)
    assert etat2["hits"] == 0, "une cible qui bouge doit ESQUIVER l'epee"


@test("glow Oswald : precalcule, pic sur les explosions, rien en idle")
def t_glow():
    o = perso(classes.Oswald)
    a1 = o.actions["attack1"]
    assert a1 in o._glow, "forces de glow precalculees attendues sur attack1"
    imp = o.config["attacks"][1]["frame"]
    assert max(o._glow[a1][imp:imp + 4]) >= 0.9, "pic de glow attendu autour de l'impact"
    assert o.actions["idle"] not in o._glow, "pas de glow en idle"
    assert o.actions["teleport"] in o._glow, "glow leger attendu sur le teleport"


@test("sequences du Combo Trainer derivables du config (Oswald/Barrion)")
def t_seq_combo():
    def seq(cls):
        combo = cls.CONFIG["combo"]
        bd = {a: b for b, a in combo["base"].items()}
        act = combo["depart"]
        lb = {"move1": "M1", "move2": "M2"}
        s = [lb[bd[act]]]
        vus = {act}
        while act in combo["chaines"]:
            btn, act = combo["chaines"][act]
            s.append(lb.get(btn, btn))
            if act in vus:
                break
            vus.add(act)
        return s
    assert seq(classes.Oswald) == ["M1", "M2", "M2"]
    assert seq(classes.Barrion) == ["M1", "M2", "M1"]


# ------------------------------------------------------------------- IA (solo)
def _sim_ia(cls, niveau, frames=120, px=500, dx=900):
    """Fait jouer l'IA (perso cls) contre un mannequin passif. Renvoie (p, d, infos)."""
    p = perso(cls, px)
    d = dummy(dx)
    ia = classes.IA(niveau, graine=1)
    classes.reset_horloge(); classes.reset_horloge_active()
    a3 = p.actions.get("attack3") if hasattr(p, "actions") else None
    infos = {"attaque": False, "bloc": False, "deg": 0.0, "x0": p.rect.centerx, "a3": False}
    for _ in range(frames):
        classes.avancer_horloge(); classes.avancer_horloge_active()
        inp = ia.decide(p, d)
        assert isinstance(inp, classes.Inputs), "l'IA doit renvoyer un Inputs"
        assert not (inp.block and (inp.move1 or inp.move2)), "jamais bloc ET attaque la meme frame"
        infos["attaque"] |= (inp.move1 or inp.move2)
        infos["bloc"] |= inp.block
        p.inputs = inp
        p.mvmt(SURF, d)
        hp = d.health
        p.update(SURF, d)
        infos["deg"] += max(0.0, hp - d.health)
        if a3 is not None and getattr(p, "action", None) == a3:
            infos["a3"] = True                          # le finisher (attack3) a ete atteint
        d.update(SURF, p)
    return p, d, infos


@test("IA : pilote les 7 persos aux 3 niveaux sans planter (inputs valides)")
def t_ia_robuste():
    for cls in _PERSOS:
        for niv in ("facile", "normal", "difficile"):
            p, d, infos = _sim_ia(cls, niv, frames=45)
            assert infos["attaque"] or p.rect.centerx != infos["x0"], \
                "%s/%s : l'IA doit au moins bouger ou attaquer" % (cls.__name__, niv)


@test("IA : approche l'adversaire quand il est loin")
def t_ia_approche():
    p, d, infos = _sim_ia(classes.Kenshi, "difficile", frames=40, px=400, dx=1100)
    assert p.rect.centerx > infos["x0"] + 60, "l'IA doit se rapprocher (marcher vers la cible)"


@test("IA : attaque et touche quand elle est en portee")
def t_ia_frappe():
    # cible proche -> l'IA doit frapper et infliger des degats au mannequin
    p, d, infos = _sim_ia(classes.Kenshi, "difficile", frames=90, px=600, dx=820)
    assert infos["attaque"], "l'IA doit lancer des attaques en portee"
    assert infos["deg"] > 0, "l'IA doit toucher (degats infliges au mannequin)"


@test("IA : bloque une attaque adverse proche (defense reactive)")
def t_ia_bloque():
    p = perso(classes.Oswald, 500)
    d = dummy(700)
    d.attacking = True                                  # menace : l'adversaire attaque, proche
    ia = classes.IA("difficile", graine=2)
    bloque = False
    for _ in range(14):                                 # reaction=1 -> doit bloquer dans la fenetre
        if ia.decide(p, d).block:
            bloque = True
            break
    assert bloque, "l'IA doit lever la garde face a une attaque proche"
    # ... mais PAS de garde si l'adversaire est LOIN (pas de turtle a distance)
    d2 = dummy(1500)                                    # hors de portee de menace
    d2.attacking = True
    ia2 = classes.IA("difficile", graine=2)
    assert not any(ia2.decide(p, d2).block for _ in range(14)), "pas de garde si la menace est loin"


@test("IA : portee_perso reflete l'allonge (longue portee > courte, jamais nulle)")
def t_ia_portee():
    assert classes.portee_perso(perso(classes.Kenshi)) > classes.portee_perso(perso(classes.Stormr)) > 0
    for cls in _PERSOS:
        assert classes.portee_perso(perso(cls)) > 0, cls.__name__


@test("IA difficile bien plus forte que facile (degats infliges)")
def t_ia_difficulte():
    _, _, fac = _sim_ia(classes.Oswald, "facile", frames=200, px=560, dx=860)
    _, _, dif = _sim_ia(classes.Oswald, "difficile", frames=200, px=560, dx=860)
    assert dif["deg"] > fac["deg"], "difficile doit surpasser facile (%.0f vs %.0f)" % (dif["deg"], fac["deg"])


@test("IA : enchaine un VRAI combo jusqu'au finisher (Oswald atteint attack3)")
def t_ia_combo():
    _, _, infos = _sim_ia(classes.Oswald, "difficile", frames=200, px=560, dx=860)
    assert infos["a3"], "l'IA doit atteindre le finisher via l'enchainement (pulse du bouton)"


def _feed(ia, m, adv_kwargs, frames=120):
    """Nourrit l'IA d'un adversaire au comportement FIXE (turtle/rush) et renvoie sa lecture."""
    for _ in range(frames):
        d = dummy(760)
        for k, v in adv_kwargs.items():
            setattr(d, k, v)
        ia.decide(m, d)
    return ia.lecture()


@test("IA : s'adapte a un joueur qui TURTLE (agression forcee, casse-garde)")
def t_ia_adapte_turtle():
    base = classes.IA.NIVEAUX["difficile"]
    m = perso(classes.Kenshi, 500)
    ia = classes.IA("difficile", graine=1)
    L = _feed(ia, m, {"block": True})
    assert L["casse_garde"], "un joueur qui bloque en continu doit declencher le mode casse-garde"
    assert ia._pe["agressif"] >= base["agressif"], "l'agression doit monter"
    assert ia._pe["espace"] < base["espace"], "le bait doit tomber (on colle pour chip la garde)"
    # FACILE n'adapte PAS (adapt=0) -> reste sur ses params de base
    iaf = classes.IA("facile", graine=1)
    Lf = _feed(iaf, m, {"block": True})
    assert not Lf["casse_garde"] and iaf._pe["agressif"] == classes.IA.NIVEAUX["facile"]["agressif"], \
        "l'IA facile ne lit pas le joueur"


@test("IA : s'adapte a un joueur qui RUSH (defense/bait renforces)")
def t_ia_adapte_rush():
    base = classes.IA.NIVEAUX["difficile"]
    m = perso(classes.Kenshi, 500)
    ia = classes.IA("difficile", graine=1)
    # adversaire qui attaque en fronts repetes -> rush eleve
    for k in range(120):
        d = dummy(760)
        d.attacking = (k % 3 != 0)
        ia.decide(m, d)
    L = ia.lecture()
    assert L["rush_joueur"] > 1.8, "l'IA doit mesurer un joueur agressif"
    assert L["espace_eff"] > base["espace"], "le bait/whiff-punish doit augmenter face a un rusheur"


@test("IA : punit un adversaire SONNE (garde brisee) par une attaque")
def t_ia_punit_stun():
    m = perso(classes.Kenshi, 500)
    ia = classes.IA("difficile", graine=3)
    d = dummy(760)
    d.hit = True                                        # adversaire sonne, a portee
    frappe = any((lambda inp: inp.move1 or inp.move2)(ia.decide(m, d)) for _ in range(6))
    assert frappe, "l'IA doit DEBALLER une attaque sur un adversaire sonne"


@test("IA : ne se sur-bloque pas (garde presque cassee -> ne bloque plus)")
def t_ia_garde_protegee():
    m = perso(classes.Oswald, 500)
    m.block_health = classes.BOUCLIER_MAX * 0.9         # garde au bord de la rupture
    ia = classes.IA("difficile", graine=1)
    bloque = False
    for _ in range(12):
        d = dummy(700)
        d.attacking = True                              # menace proche
        if ia.decide(m, d).block:
            bloque = True
    assert not bloque, "garde presque cassee : l'IA doit se degager, pas bloquer (auto-protection)"


def _hop(cls, dummy_x, flip):
    """Appui Haut (1 frame), adversaire a dummy_x, regard=flip. Renvoie (dx, hauteur, invuln, cd)."""
    p = perso(cls, 700)
    p.flip = flip
    d = dummy(dummy_x)
    x0 = p.rect.centerx
    classes.reset_horloge(); classes.reset_horloge_active()
    hmax = 0.0; invuln = False
    for f in range(18):
        classes.avancer_horloge(); classes.avancer_horloge_active()
        p.inputs = classes.Inputs(up=(f == 0))
        p.mvmt(SURF, d)
        hmax = max(hmax, classes.SOL - p.rect.bottom)   # hauteur du saut
        if classes.esquive(p):
            invuln = True
        p.update(SURF, d); d.update(SURF, p)
    return p.rect.centerx - x0, hmax, invuln, p.dodge_cd


@test("esquive (Haut) : SAUT invulnerable, toujours A L'OPPOSE de l'ennemi (regard indifferent)")
def t_esquive():
    for cls in (classes.Lysandra, classes.KonradForgeval, classes.Arinya, classes.Stormr):
        # ennemi a DROITE (900) -> saut a GAUCHE (on s'eloigne), QUEL QUE SOIT le regard
        for flip in (False, True):
            dx, h, inv, cd = _hop(cls, 900, flip)
            assert dx < -100, "%s : ennemi a droite -> saut a GAUCHE (regard flip=%s, dx=%d)" % (cls.__name__, flip, dx)
            assert h > 80, "%s : le saut doit etre HAUT (hauteur=%d)" % (cls.__name__, h)
            assert inv and cd > 0, "%s : invulnerable + cooldown" % cls.__name__
        # ennemi a GAUCHE (500) -> saut a DROITE, QUEL QUE SOIT le regard
        for flip in (False, True):
            dx, _, _, _ = _hop(cls, 500, flip)
            assert dx > 100, "%s : ennemi a gauche -> saut a DROITE (regard flip=%s, dx=%d)" % (cls.__name__, flip, dx)
    # KENSHI (passe-lame) : EXCEPTION voulue -- de PRES son esquive est un DASH RASANT qui
    # TRAVERSE l'ennemi (le depose dans son dos) ; de LOIN elle fuit en SAUTANT normalement.
    for flip in (False, True):
        dx, h, inv, cd = _hop(classes.Kenshi, 900, flip)          # proche (200 px < seuil 300)
        assert dx > 100, "Kenshi proche : l'esquive doit TRAVERSER vers l'ennemi (dx=%d)" % dx
        assert h <= 40, "Kenshi proche : dash RASANT, pas un saut (hauteur=%d)" % h
        assert inv and cd > 0, "Kenshi : invulnerable + cooldown"
        dx, _, _, _ = _hop(classes.Kenshi, 500, flip)             # proche, ennemi a gauche
        assert dx < -100, "Kenshi proche (ennemi a gauche) : traverse vers la GAUCHE (dx=%d)" % dx
        dx, h, _, _ = _hop(classes.Kenshi, 1400, flip)            # LOIN (700 px > seuil)
        assert dx < -100, "Kenshi loin : l'esquive doit FUIR a l'oppose (dx=%d)" % dx
        assert h > 80, "Kenshi loin : fuite SAUTEE classique (hauteur=%d)" % h
    # AGILITE : on doit SENTIR la difference -- Kenshi (agile) esquive plus VITE (airtime court)
    # que Stormr (normal), lui-meme plus vif que Lysandra (tres lourde).
    def _airtime(cls):
        p = perso(cls, 700); p.flip = False; d = dummy(900)
        classes.reset_horloge(); classes.reset_horloge_active(); air = 0
        for f in range(30):
            classes.avancer_horloge(); classes.avancer_horloge_active()
            p.inputs = classes.Inputs(up=(f == 0))
            p.mvmt(SURF, d); p.update(SURF, d); d.update(SURF, p)
            if classes.SOL - p.rect.bottom > 1:
                air += 1
        return air
    assert _airtime(classes.Kenshi) < _airtime(classes.Stormr) < _airtime(classes.Lysandra), \
        "l'agilite doit se sentir (Kenshi vif < Stormr normal < Lysandra lourde)"
    # EQUILIBRAGE vs BOUCLIER : invuln COURTE au DEBUT seulement -> la MAJEURE PARTIE de l'esquive
    # est vulnerable (un adversaire qui time bien PEUT toucher l'esquiveur) + un coup l'ANNULE.
    def _fenetres(cls, ex=900):
        p = perso(cls, 700); p.flip = False; d = dummy(ex)
        classes.reset_horloge(); classes.reset_horloge_active()
        inv = vul = 0
        for f in range(30):
            classes.avancer_horloge(); classes.avancer_horloge_active()
            p.inputs = classes.Inputs(up=(f == 0)); p.mvmt(SURF, d)
            if getattr(p, "dodging", False):
                if classes.esquive(p):
                    inv += 1
                else:
                    vul += 1
            p.update(SURF, d); d.update(SURF, p)
        return inv, vul
    # NB : Kenshi est mesure avec l'ennemi LOIN (1400) -> esquive de FUITE classique
    # (sa traversee courte de pres est testee dans t_kenshi_kit).
    for cls, ex in ((classes.Kenshi, 1400), (classes.Arinya, 900), (classes.KonradForgeval, 900),
                    (classes.Stormr, 900), (classes.Lysandra, 900)):
        inv, vul = _fenetres(cls, ex)
        assert inv >= 2, "%s : l'esquive doit evader si BIEN timee (invuln au debut)" % cls.__name__
        assert vul >= 3 and inv <= 5, \
            "%s : invuln COURTE + gros creneau vulnerable (invuln=%d vuln=%d)" % (cls.__name__, inv, vul)
    p = perso(classes.Kenshi, 700); p.flip = False; d = dummy(900)
    classes.reset_horloge(); classes.reset_horloge_active()
    classes.avancer_horloge(); p.inputs = classes.Inputs(up=True); p.mvmt(SURF, d)
    assert p.dodging, "esquive lancee"
    p.hit = True
    classes.avancer_horloge(); p.inputs = classes.Inputs(); p.mvmt(SURF, d)
    assert not p.dodging, "un coup encaisse doit ANNULER l'esquive (pas invincible)"
    # Oswald garde son TELEPORT sur Haut (pas l'esquive)
    o = perso(classes.Oswald, 600)
    classes.reset_horloge(); classes.reset_horloge_active()
    classes.avancer_horloge()
    o.inputs = classes.Inputs(up=True)
    o.mvmt(SURF, dummy(760))
    assert getattr(o, "teleporting", False) and not getattr(o, "dodging", False), \
        "Oswald : Haut reste le teleport, pas l'esquive"


def _joue_ia(m, d, ia, first=None, frames=45, cible=None, maintien=None):
    """Deroule l'IA en AVANCANT L'HORLOGE de simulation (comme le vrai jeu -> les anims/combos
    progressent). Joue 'first' puis la file d'inputs speciaux, puis les decisions. True si 'cible'
    (attribut du perso) s'active. maintien(m) est appele chaque frame (ex: garder la charge)."""
    classes.reset_horloge(); classes.reset_horloge_active()
    ok = False
    for k in range(frames):
        classes.avancer_horloge(); classes.avancer_horloge_active()
        if maintien:
            maintien(m)
        if k == 0 and first is not None:
            inp = first
        elif ia._file:
            inp = classes.Inputs(**ia._file.pop(0))
        else:
            inp = ia.decide(m, d)
        m.inputs = inp
        m.mvmt(SURF, d); m.update(SURF, d); d.update(SURF, m); d.hit = False
        if cible and getattr(m, cible, None) not in (None, False, 0):
            ok = True
            break
    return ok


@test("IA : declenche les MOVES SPECIAUX du kit (tp / spin / saut / dash / lance)")
def t_ia_kits():
    # (classe, distance base, attribut a voir s'activer, setup optionnel)
    cas = [
        (classes.Oswald,  300, "teleporting",    None),
        (classes.Barrion, 300, "spinning",       None),
        (classes.Barrion, 350, "jumping_attack", lambda m: setattr(m, "spin_cd", 60)),
        (classes.Arinya,  300, "dashing",        None),
        (classes.Arinya,  500, "spear",          None),
        (classes.KonradForgeval, 300, None,      None),   # switch : verifie via current_weapon
    ]
    for cls, dd, cible, setup in cas:
        m = perso(cls, 400)
        d = dummy(400 + int(dd) + 40)
        if setup:
            setup(m)
        ia = classes.IA("difficile", graine=1)
        first = ia._tactique_perso(m, getattr(m, "config", None), dd, "right")
        if cls is classes.KonradForgeval:
            w0 = m.current_weapon
            _joue_ia(m, d, ia, first=first, frames=30)
            assert m.current_weapon != w0, "Konrad doit varier d'arme (move2 = switch)"
        else:
            assert _joue_ia(m, d, ia, first=first, cible=cible), \
                "%s : %s ne s'active jamais sous IA" % (cls.__name__, cible)


@test("IA Barrion : declenche le SLAM (ecrase en plein saut, move2 en l'air)")
def t_ia_slam():
    m = perso(classes.Barrion, 400)
    m.spin_cd = 60                                          # empeche le spin -> force le saut
    d = dummy(760)
    ia = classes.IA("difficile", graine=1)
    ok = _joue_ia(m, d, ia, first=classes.Inputs(up=True, right=True), frames=55, cible="hit_down")
    assert ok, "le slam (hit_down) doit se declencher pendant le saut-attaque"


@test("IA Stormr : sort la FOUDRE au finisher quand la cible est chargee (M1-M2-M1)")
def t_ia_foudre():
    m = perso(classes.Stormr, 400)
    d = dummy(600)
    ia = classes.IA("difficile", graine=1)
    ok = _joue_ia(m, d, ia, frames=150, cible="lightning",
                  maintien=lambda mm: setattr(mm, "enemy_charge", mm.CHARGE_MAX))
    assert ok, "la foudre doit sortir au 3e coup du combo quand l'ennemi est charge a fond"



@test("Moves Guide : donnees completes + detecteurs (spin/slam/tp/arme/lance/esquive)")
def t_moves_guide():
    G = classes.MOVES_GUIDE
    NOMS = ("Kenshi", "Lysandra", "Konrad", "Arinya", "Stormr", "Oswald", "Barrion")
    for nom in NOMS:
        assert nom in G and G[nom], "pas d'entree Moves Guide pour %s" % nom
        for mv in G[nom]:
            assert mv.get("nom") and mv.get("seq") and mv.get("note"),                 "entree incomplete chez %s" % nom
            assert callable(mv.get("detect")), "detect manquant : %s / %s" % (nom, mv.get("nom"))

    def m(nom_p, nom_m):
        return next(x for x in G[nom_p] if x["nom"] == nom_m)

    # A BLANC : un perso neutre qui ne fait rien ne valide AUCUN move (2 appels :
    # le 1er initialise les detecteurs a front montant, le 2e doit dire False).
    CLASSES = {"Kenshi": classes.Kenshi, "Lysandra": classes.Lysandra,
               "Konrad": classes.KonradForgeval, "Arinya": classes.Arinya,
               "Stormr": classes.Stormr, "Oswald": classes.Oswald, "Barrion": classes.Barrion}
    for nom_p, cls in CLASSES.items():
        p = perso(cls)
        for k in ("_gd_dodge", "_gd_tp", "_gd_arme", "_gd_lance"):
            if hasattr(p, k):
                delattr(p, k)
        for mv in G[nom_p]:
            mv["detect"](p, 0)
            assert not mv["detect"](p, 0), "%s / %s se valide sans rien faire" % (nom_p, mv["nom"])

    # CAS POSITIFS (flags du move + degats quand le move frappe)
    b = perso(classes.Barrion)
    b.spinning = True
    assert m("Barrion", "Spin")["detect"](b, 12), "spin non detecte"
    b.spinning = False; b.jumping_attack = True; b.hit_down = False
    assert m("Barrion", "Hammer Leap")["detect"](b, 9), "saut-marteau non detecte"
    b.hit_down = True
    assert m("Barrion", "Sky Slam")["detect"](b, 20), "slam non detecte"
    assert not m("Barrion", "Hammer Leap")["detect"](b, 9), "saut-marteau valide PENDANT le slam"

    o = perso(classes.Oswald)
    if hasattr(o, "_gd_tp"):
        delattr(o, "_gd_tp")
    o.teleporting = True
    assert m("Oswald", "Teleport")["detect"](o, 0), "teleport non detecte (front montant)"
    assert not m("Oswald", "Teleport")["detect"](o, 0), "teleport re-valide sans nouveau front"

    k = perso(classes.KonradForgeval)
    if hasattr(k, "_gd_arme"):
        delattr(k, "_gd_arme")
    k.current_weapon = 1
    m("Konrad", "Weapon Switch")["detect"](k, 0)          # init du front
    k.current_weapon = 3
    assert m("Konrad", "Weapon Switch")["detect"](k, 0), "changement d'arme non detecte"

    a = perso(classes.Arinya)
    if hasattr(a, "_gd_lance"):
        delattr(a, "_gd_lance")
    a.has_spear = False
    m("Arinya", "Spear Pickup")["detect"](a, 0)           # init (sans lance)
    a.has_spear = True
    assert m("Arinya", "Spear Pickup")["detect"](a, 0), "ramassage de lance non detecte"
    a.spear = {"x": 0}
    assert m("Arinya", "Charged Spear Throw")["detect"](a, 8), "lance qui touche non detectee"

    s = perso(classes.Stormr)
    s.enemy_charge = 12.0
    assert m("Stormr", "Static Charge")["detect"](s, 5), "charge statique non detectee"

    kn = perso(classes.Kenshi)
    kn.flow = 3
    assert m("Kenshi", "Flow")["detect"](kn, 0), "flow max non detecte"
    kn._lame_derniere = 2
    assert m("Kenshi", "Blade Through")["detect"](kn, 7), "coup de passe-lame non detecte"
    assert m("Kenshi", "First Blood")["detect"](kn, 7), "premier sang non detecte"
    kn._exec_active = True
    assert m("Kenshi", "Execution")["detect"](kn, 7), "execution non detectee"

    # PROGRESSION "prog" (badges bleus au fil du move dans le panneau)
    b.spinning = False; b.jumping_attack = True; b.hit_down = False
    assert m("Barrion", "Sky Slam")["prog"](b) == 1, "prog slam : etape 1 (saut) attendue"
    b.hit_down = True
    assert m("Barrion", "Sky Slam")["prog"](b) == 2, "prog slam : etape 2 (ecrasement) attendue"
    kn.flow = 2
    assert m("Kenshi", "Flow")["prog"](kn) == 2, "prog flow : 2 stacks attendus"
    a.charging = True; a.throwing = False; a.spear = None
    assert m("Arinya", "Charged Spear Throw")["prog"](a) == 1, "prog lancer : charge attendue"


@test("Kit Kenshi : glass cannon (traversee, lame, flow, execution, dodge-cancel)")
def t_kenshi_kit():
    k = perso(classes.Kenshi)
    d = dummy(900)
    e = 1.0
    assert k.max_health == 280, "Kenshi doit etre le glass cannon (280 PV), a %s" % k.max_health

    # 1) ESQUIVE TRAVERSANTE : de pres, un DASH RASANT VERS l'ennemi ; de loin, fuite sautee.
    k.rect.centerx = 700; d.rect.centerx = 900          # proche (200 < seuil 300)
    k._dodge_up_prev = False
    classes.demarrer_esquive(k, True, e, d)
    assert k.dodging and k.dodge_dir == 1, "de pres l'esquive doit TRAVERSER (vers l'ennemi)"
    assert k._dodge_trav and k._dodge_hop == 0, "la traversee est un DASH rasant (pas de saut)"
    k.dodging = False; k.dodge_cd = 0; k._pl_depart = None; k._pl_dodge_prev = False
    classes.demarrer_esquive(k, False, e, d)            # frame suivante : fin de dash
    assert not getattr(k, "dashing", False) and not k._dodge_trav, "poussiere de dash coupee a la fin"
    k.rect.centerx = 200                                # loin (700 > seuil)
    k._dodge_up_prev = False
    classes.demarrer_esquive(k, True, e, d)
    assert k.dodging and k.dodge_dir == -1, "de loin l'esquive doit FUIR (a l'oppose)"
    assert not k._dodge_trav and k._dodge_hop > 0, "de loin : saut classique (pas un dash)"

    # 2) FENETRE DE LAME : traversee -> retombee FACE a l'ennemi -> fenetre transferee a
    #    l'attaque lancee pendant la fenetre, puis consommee par le coup qui connecte.
    k.dodging = False; k.dodge_cd = 0
    k.rect.centerx = 1000                               # retombe a DROITE de l'ennemi (900)
    k.flip = False                                      # regard a droite (dos a l'ennemi)
    k._pl_depart = -1; k._pl_croise = True; k._pl_dodge_prev = True
    classes.demarrer_esquive(k, False, e, d)            # frame suivante : retombee detectee
    assert k.lame_niveau() == 2, "traversee retombee -> fenetre passe-lame (niv 2)"
    assert k.flip == True, "il doit atterrir RETOURNE face a l'ennemi traverse"
    k.attacking = False; k.attack_cd = 0
    k.attack(SURF, d)                                   # attaque lancee PENDANT la fenetre
    assert k._lame_atk == 2 and k.lame_niveau() == 0, "fenetre transferee a l'attaque lancee"
    d.health = d.max_health
    assert abs(k.mult_degats(d) - 1.5) < 1e-6, "passe-lame = degats x1.5"
    k.coup_touche(d, False)
    assert k._lame_atk == 0, "le buff doit etre CONSOMME par le coup"
    assert k._lame_derniere == 2 and k.flow == 1, "coup buffe memorise + 1 stack de flow"
    k.attacking = False

    # 3) FLOW : stacks (max 3), esquive qui recharge plus vite, reset si on perd de la vie.
    k.coup_touche(d, False); k.coup_touche(d, False); k.coup_touche(d, False)
    assert k.flow == 3, "flow doit plafonner a 3"
    k.dodging = False; k.dodge_cd = 0; k._dodge_up_prev = False; k._pl_depart = None
    classes.demarrer_esquive(k, True, e, d)
    assert k.dodge_cd == 36 - 18, "flow 3 -> cd d'esquive 36-18=18 (recu %s)" % k.dodge_cd
    k._flow_hp = k.health; k.health -= 5
    k.update(SURF, d)
    assert k.flow == 0, "perdre de la vie doit remettre le flow a zero"
    k.health = k.max_health; k._flow_hp = k.health

    # 3ter) FLOW qui s'EVENTE : 4,5 s sans attaquer -> -1 stack, puis -1 par seconde.
    k.flow = 3; k._flow_t = classes.temps_actif_ms() - 4600
    k.update(SURF, d)
    assert k.flow == 2, "4,5 s sans attaque -> le flow doit perdre 1 stack"
    k.update(SURF, d)
    assert k.flow == 2, "le stack suivant ne part qu'UNE SECONDE plus tard"
    k._flow_t -= 1100
    k.update(SURF, d)
    assert k.flow == 1, "une seconde plus tard -> -1 stack de plus"
    k.attacking = False; k.attack_cd = 0
    k.attack(SURF, d)                                   # attaquer RELANCE le chrono
    k.attacking = False
    k.update(SURF, d)
    assert k.flow == 1, "attaquer doit entretenir le flow (pas de perte)"
    k.flow = 0

    # 4) EXECUTION : cible sous 25% -> x1.3 (et x1.3*1.5 si le coup porte la lame niv 2).
    d.health = d.max_health * 0.2
    assert abs(k.mult_degats(d) - 1.3) < 1e-6, "execution = x1.3 sous 25%% PV"
    k._lame_atk = 2
    assert abs(k.mult_degats(d) - 1.95) < 1e-6, "lame x1.5 * execution x1.3 = x1.95"
    k._lame_atk = 0

    # 5) DODGE-CANCEL : Haut pendant une attaque coupe l'attaque en esquive (cd dedie).
    k.dodging = False; k.dodge_cd = 0; k.cancel_cd = 0; k._dodge_up_prev = False
    k.attacking = True; k.attack_type = 1
    classes.demarrer_esquive(k, True, e, d)
    assert not k.attacking and k.dodging, "le cancel doit couper l'attaque en esquive"
    assert k.cancel_cd == 90 and k.nb_cancels >= 1, "cd de cancel arme + compteur pour le guide"
    # pendant le cd de cancel : l'esquive en pleine attaque est REFUSEE
    k.dodging = False; k.dodge_cd = 0; k._dodge_up_prev = False
    k.attacking = True
    classes.demarrer_esquive(k, True, e, d)
    assert k.attacking and not k.dodging, "cancel en cooldown -> pas de 2e cancel"
    k.attacking = False

    # 6) Les AUTRES persos ne traversent jamais (pas de cle 'traverse').
    l = perso(classes.Lysandra)
    l.rect.centerx = 700; l._dodge_up_prev = False; l.dodging = False; l.dodge_cd = 0
    classes.demarrer_esquive(l, True, e, d)
    assert l.dodge_dir == -1, "Lysandra proche doit toujours fuir a l'oppose"


@test("Kit Lysandra : juggernaut (armure totale, seisme, marche, ancrage, colere)")
def t_lysandra_kit():
    l = perso(classes.Lysandra, 700)
    d = dummy(900)
    loin = dummy(1400)

    # 1) INEBRANLABLE : super-armure sur TOUTES ses attaques (config armure = (1, 2))
    l.attacking = True; l.attack_type = 1; l.hit = True
    assert classes.armure_lourde(l), "attack1 doit etre sous super-armure"
    l.update(SURF, d)
    assert not l.hit, "un coup encaisse pendant l'attaque ne doit PAS l'interrompre"
    l.attack_type = 2
    assert classes.armure_lourde(l), "attack2 aussi (tous ses coups sont inarretables)"
    l.attacking = False; l.hit = False; l.attack_type = 0

    # 2) ANCRAGE : en garde, la poussee ne la deplace pas d'un pixel
    l.block = True; x0 = l.rect.x
    classes.poussee(l, l.rect.centerx - 100, 30)
    assert l.rect.x == x0 and l._ancrage_nb >= 1, "en garde : ZERO recul (ancrage)"
    l.block = False; x0 = l.rect.x
    classes.poussee(l, l.rect.centerx - 100, 30)
    assert l.rect.x != x0, "hors garde/charge : la poussee la deplace normalement"
    l.rect.centerx = 700; l.rect.bottom = classes.SOL

    # 3) MARCHE INARRETABLE : avancer vers l'ennemi charge le poids ; un coup le consomme
    l.poids = 0
    for _ in range(30):
        l.inputs = classes.Inputs(right=True)     # vers le dummy (900, a droite)
        l.mvmt(SURF, d)
    assert l.poids >= 25, "30 frames de marche vers l'ennemi -> du poids (recu %s)" % l.poids
    l.poids = classes.Lysandra.POIDS_MAX_F
    l.attack_type = 1; d.health = d.max_health; l.health = l.max_health
    m = l.mult_degats(d)
    assert abs(m - 1.35) < 0.02, "poids plein = +35%% (recu %.2f)" % m
    l.coup_touche(d, False)
    assert l.poids == 0, "le coup consomme le poids"

    # 4) SEISME : M2 TENUE -> montee figee ; relache -> mult ; PLEINE charge -> perce la garde
    l.rect.centerx = 700
    classes.reset_horloge(); classes.reset_horloge_active()
    l.attacking = True; l.attack_type = 2; l._seisme_arme = False; l._seisme_lache = False
    l.frame_index = 2; l.inputs = classes.Inputs(move2=True)
    l.update(SURF, loin)
    assert l._seisme_gel, "M2 tenue a la frame de gel -> charge sismique"
    for _ in range(20):                            # ~0,66 s de charge
        classes.avancer_horloge(); classes.avancer_horloge_active()
        l.frame_index = 2; l.inputs = classes.Inputs(move2=True)
        l.update(SURF, loin)
        assert l.frame_index <= 2 or l._seisme_gel is False, "la montee doit rester FIGEE"
    l.inputs = classes.Inputs(); l.frame_index = 2
    l.update(SURF, loin)                           # RELACHE (charge partielle)
    assert not l._seisme_gel and 1.2 < l._seisme_mult < 2.15, \
        "charge partielle -> mult intermediaire (recu %.2f)" % l._seisme_mult
    assert not l.perce_garde(loin), "charge partielle : ne perce PAS la garde"
    # pleine charge (>= charge_ms) -> x2.2 et PERCE
    l._seisme_arme = False; l._seisme_lache = False
    l.frame_index = 2; l.inputs = classes.Inputs(move2=True); l.update(SURF, loin)
    for _ in range(46):                            # > 1400 ms
        classes.avancer_horloge(); classes.avancer_horloge_active()
        l.frame_index = 2; l.inputs = classes.Inputs(move2=True)
        l.update(SURF, loin)
    l.inputs = classes.Inputs(); l.frame_index = 2
    l.update(SURF, loin)
    assert abs(l._seisme_mult - 2.2) < 0.06 and l.perce_garde(loin), \
        "pleine charge : x2.2 + PERCE la garde (mult %.2f)" % l._seisme_mult
    l.attacking = False; l._seisme_mult = 1.0; l._seisme_perce = False

    # 5) COLERE LENTE : x1 a pleine vie, +35% au plancher (20% PV)
    l.poids = 0; l.attack_type = 1
    l.health = l.max_health
    assert abs(l.mult_degats(d) - 1.0) < 1e-6, "pleine vie : pas de colere"
    l.health = l.max_health * 0.2
    assert abs(l.mult_degats(d) - 1.35) < 0.02, "20%% PV : +35%%"

    # 6) GARDE-FOU : le cumul poids * colere * seisme plafonne a MULT_CAP
    l.attacking = True; l.attack_type = 2          # en pleine attaque 2 (gate _en_attack2)
    l._seisme_mult = 2.2; l._seisme_perce = True
    l.poids = classes.Lysandra.POIDS_MAX_F
    assert abs(l.mult_degats(d) - classes.Lysandra.MULT_CAP) < 1e-6, "cumul plafonne"
    l.attacking = False; l.health = l.max_health; l.attack_type = 0
    l._seisme_mult = 1.0; l._seisme_perce = False; l.poids = 0


# ---------------------------------------------------------------- runner
def main():
    import time
    t0 = time.time()
    ok = echecs = 0
    for nom, fn in TESTS:
        try:
            fn()
            print("  OK     %s" % nom)
            ok += 1
        except AssertionError as e:
            print("  ECHEC  %s\n         -> %s" % (nom, e))
            echecs += 1
        except Exception as e:
            print("  ERREUR %s\n         -> %r" % (nom, e))
            echecs += 1
    print("-" * 64)
    verdict = "TOUT PASSE" if echecs == 0 else "!!! %d TEST(S) EN ECHEC" % echecs
    print("%d/%d tests OK en %.1fs -- %s" % (ok, ok + echecs, time.time() - t0, verdict))
    return 0 if echecs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

====================================================================
             THE SIEGE OF GRIMGATE  -  Comment jouer
====================================================================

>>> POUR JOUER : double-clique sur  "Jouer TSOG.bat"
    (ou sur le raccourci "The Siege of Grimgate" avec l'icone).

C'est tout. Le jeu s'occupe du reste tout seul.


--------------------------------------------------------------------
 AU TOUT PREMIER LANCEMENT (une seule fois)
--------------------------------------------------------------------
La premiere fois, une fenetre noire (invite de commande) s'ouvre
quelques secondes pour preparer le jeu. C'EST NORMAL. Elle :

  1) installe les composants du jeu (necessite internet 1 seule
     fois - patiente, ca peut prendre une minute) ;

  2) ouvre les ports du multijoueur dans le pare-feu Windows.

Les fois suivantes, le jeu demarre directement, sans rien de tout ca.


--------------------------------------------------------------------
 LES FENETRES / AVERTISSEMENTS WINDOWS  (tout est NORMAL)
--------------------------------------------------------------------

* "Voulez-vous autoriser cette application a apporter des
   modifications a votre appareil ?" (fenetre bleue de Windows, dite
   UAC), ou "l'invite de commandes va apporter des modifications"
   --> Reponds OUI.
   C'est UNIQUEMENT pour autoriser le jeu dans le pare-feu (pour que
   le multijoueur fonctionne). Le jeu ne touche a RIEN d'autre sur
   ton PC. Ca n'est demande qu'une seule fois.

* "Windows a protege votre ordinateur" (ecran bleu SmartScreen)
   --> Clique sur "Informations complementaires" puis "Executer
   quand meme". Ca apparait parce que le lanceur n'est pas signe
   (c'est un jeu perso, pas un logiciel commercial), pas parce qu'il
   est dangereux.

* Ton antivirus rale sur le .bat ou "python"
   --> C'est un faux positif classique pour ce genre de lanceur.
   Autorise-le / mets-le en exception. Aucun code malveillant ici.

* Le jeu s'appelle "pythonw" dans le mixeur de volume Windows
   --> Normal, c'est Python qui fait tourner le jeu.


--------------------------------------------------------------------
 MULTIJOUEUR (2 PC sur le meme Wi-Fi)
--------------------------------------------------------------------
- Un joueur clique "Multiplayer" > "Host a game", donne un nom.
- L'autre clique "Multiplayer" > "Join a game" : la partie apparait
  toute seule dans la liste, il clique dessus. C'est parti.
- Si "impossible de rejoindre" alors que la partie apparait : c'est
  le pare-feu. Lance une fois "Scripts\Autoriser le multijoueur.bat"
  (clic droit > Executer, reponds Oui) sur le PC qui heberge.
- Hors du meme Wi-Fi (ex: Hamachi) : le bouton "Enter IP / Code"
  permet de se connecter avec le code (ou l'IP) affiche cote hote.
- Ethernet d'un cote et Wi-Fi de l'autre = AUCUN probleme, tant que
  les deux PC sont sur la MEME box/routeur.


--------------------------------------------------------------------
 ERREUR "service Pare-feu Windows Defender" / pare-feu impossible
 a activer
--------------------------------------------------------------------
Symptomes : au lancement (ou via "Autoriser le multijoueur.bat") un
message dit qu'il est impossible de contacter le service Pare-feu
Windows Defender ; et dans les Parametres Windows, les pare-feu sont
tous "desactives" et tu ne peux pas les reactiver.

CAUSE : le service pare-feu (MpsSvc) est arrete. Il depend du service
"Moteur de filtrage de base" (BFE) : si BFE est desactive, le pare-feu
ne demarre plus. (Souvent casse par un outil de "nettoyage"/privacy
ou un antivirus tiers.)

REPARER : clic droit sur "Scripts\Autoriser le multijoueur.bat" >
Executer en tant qu'administrateur : il tente de redemarrer BFE puis
le pare-feu, puis d'ouvrir les ports. S'il n'y arrive pas, ouvre une
invite de commandes EN ADMIN et tape :
    sc config bfe start= auto
    net start bfe
    net start mpssvc
Puis relance le jeu. Si BFE refuse de demarrer : "sfc /scannow" (admin)
ou verifie qu'un antivirus tiers ne bloque pas le pare-feu Windows.

NB : si ton pare-feu est de toute facon DESACTIVE, l'hebergement multi
peut fonctionner malgre ce message (rien ne bloque les connexions).
Le jeu SOLO n'est jamais affecte.


--------------------------------------------------------------------
 CA NE SE LANCE PAS ? / J'AI DEPLACE LE DOSSIER ?
--------------------------------------------------------------------
- Lance TOUJOURS via  "Jouer TSOG.bat"  (il marche depuis n'importe
  quel emplacement). Il repare aussi le raccourci automatiquement.

- Si tu as recu le jeu en .zip : EXTRAIS d'abord tout le dossier
  quelque part (Bureau, Documents...) AVANT de lancer. Ne lance pas
  le .bat directement depuis l'interieur du .zip.

- Le raccourci ".lnk" retient un chemin fixe : si tu DEPLACES le
  dossier (ou l'envoies a un ami), le raccourci copie peut ne plus
  marcher. Solution : au 1er lancement dans le nouvel emplacement,
  passe par "Jouer TSOG.bat" -> il remet le raccourci a jour.

- Garde le dossier ENTIER ensemble : le lanceur a besoin des dossiers
  "Scripts", "assets" et "python_portable" a cote de lui.


--------------------------------------------------------------------
 CONTENU DU DOSSIER (pour info)
--------------------------------------------------------------------
  Jouer TSOG.bat ....... le lanceur (c'est ce que tu cliques)
  Scripts/ ............. le code du jeu + outils
  assets/ .............. images, sons, polices
  python_portable/ ..... Python embarque (pas besoin de l'installer)

Amuse-toi bien !  -  The Siege of Grimgate

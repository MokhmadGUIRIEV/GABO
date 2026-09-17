# GABO

## Aperçu

GABO est un jeu de cartes de mémoire et de bluff, pour **2 à 6 joueurs**, avec un jeu de 52 cartes classique (As à Roi, dans les 4 couleurs : cœur, carreau, pique, trèfle).

**Objectif** : avoir, une fois les cartes révélées, la plus petite somme de valeurs parmi tous les joueurs — et rester en dessous de 100 points de score cumulé au fil des manches.

## Mise en place d'une manche

1. Chaque joueur reçoit **4 cartes**, distribuées face cachée. Il ne peut pas les regarder à ce stade.
2. Le reste des cartes forme la **pioche**.
3. **Observation initiale** : tous les joueurs regardent en même temps **2 de leurs 4 cartes pendant 5 secondes**, puis elles se retournent. Il faut désormais s'appuyer sur sa mémoire.
4. Le premier joueur de la toute première manche de la partie est choisi au hasard. Le tour de jeu se poursuit ensuite dans le **sens horaire**.

## Déroulement d'un tour

À son tour, un joueur pioche une carte : lui seul peut la regarder. Il doit alors choisir entre deux actions :

- **Défausser** : poser la carte piochée, face visible, sur la pile de défausse au centre de la table.
- **Échanger** : remplacer une des cartes de sa main par la carte piochée. La carte remplacée est alors posée, face visible, sur la pile de défausse.

Le tour passe ensuite au joueur suivant, qui fait le même choix, et ainsi de suite.

## Le snap : jeter une carte identique

À tout moment — même si ce n'est pas son tour — un joueur qui pense avoir une carte de la **même valeur** que celle actuellement au sommet de la pile de défausse peut la jeter par-dessus :

- **S'il a raison** : sa carte rejoint la défausse et il se retrouve avec une carte de moins dans son jeu (bon pour lui).
- **S'il se trompe** : il reçoit une carte de pénalité, piochée sans la regarder et ajoutée à sa main.

> **Exemple** — Parmi les 2 cartes observées au début, je me souviens avoir un 6 et un 8. Le joueur avant moi pioche et défausse un 8. Ce n'est pas mon tour, mais tant que ce 8 reste au sommet de la défausse, je peux y déposer mon propre 8.

## Valeur des cartes

| Carte | Valeur |
|---|---|
| As | 1 |
| 2 à 10 | valeur numérique de la carte |
| Valet | 11 |
| Dame | 12 |
| Roi ♥ cœur ou ♦ carreau (rouge) | 35 |
| Roi ♠ pique ou ♣ trèfle (noir) | 0 |

## Les pouvoirs des cartes piochées

Certaines cartes déclenchent un pouvoir **si et seulement si elles sont défaussées directement** (et non échangées contre une carte de sa main) :

| Carte piochée | Pouvoir (si défaussée) |
|---|---|
| 7 ou 8 | Regarder une de ses **propres** cartes pendant 5 secondes |
| 9 ou 10 | Regarder une carte d'un **adversaire** pendant 5 secondes |
| Valet ou Dame | **Échanger** une de ses cartes avec celle d'un adversaire, et regarder la carte reçue pendant 5 secondes |

Utiliser un pouvoir est **optionnel** : un joueur peut toujours défausser sa carte sans rien faire d'autre.

⚠️ **Important** : si la carte piochée (7, 8, 9, 10, Valet ou Dame) est échangée contre une carte de sa main plutôt que défaussée directement, le pouvoir est annulé.

## Annoncer GABO et fin de manche

Un joueur qui pense avoir la plus petite somme de cartes parmi tous les joueurs peut annoncer **GABO** — mais uniquement **au début de son propre tour**, avant de piocher. Toutes les cartes sont alors immédiatement révélées.

Deux cas possibles :

- **Le joueur qui a dit GABO a strictement la plus petite somme** : il marque 0 point, et tous les autres joueurs ajoutent à leur score total la somme des valeurs de leurs cartes.
  > **Exemple** — Je dis GABO avec un As et un 2 (total 3). Le premier adversaire a un 4 et un As (total 5), le second a un 7 et un Valet (total 18). J'ai gagné : le premier ajoute 5 à son score, le second ajoute 18. Moi, j'ajoute 0.
- **Un autre joueur a une somme inférieure ou égale à la sienne** : seul le joueur ayant raté son GABO ajoute **35 points** à son score. Les autres n'ajoutent rien ce tour-ci.

Après un GABO, toutes les cartes sont ramassées et mélangées, et une nouvelle manche commence (redistribution de 4 cartes chacun, nouvelle observation initiale, etc.).

## Élimination et fin de partie

Un joueur atteignant **100 points ou plus** est éliminé. La partie continue avec les joueurs restants (les scores déjà acquis sont conservés), jusqu'à ce qu'il n'en reste plus qu'un seul : il remporte la partie.

---

## Implémentation

Ce dépôt contient une implémentation jouable du GABO.

### Stack technique

- **Backend** : Python (FastAPI + WebSockets), moteur de jeu pur (aucune dépendance web) testé unitairement avec pytest.
- **Frontend** : HTML/CSS/JS vanilla, sans framework, responsive.
- **Base de données** : SQLite (comptes joueurs, historique des parties et des scores).
- **Authentification** : email + mot de passe (hash bcrypt), session par cookie signé.

### Architecture

```
backend/
  app/
    game/
      cards.py      -> cartes, valeurs, deck
      engine.py      -> moteur de jeu pur (règles, tours, pouvoirs, scoring)
    main.py           -> application FastAPI (routes + WebSocket + sert le frontend)
    db.py, models.py   -> SQLite / SQLAlchemy (comptes, historique)
    routes/            -> auth (inscription/connexion) et salles de jeu
    ws.py              -> WebSocket temps réel reliant le moteur de jeu au client
    rooms.py           -> gestion des salles en mémoire
  tests/
    test_engine.py     -> 20 tests unitaires du moteur de jeu
frontend/
  index.html, lobby.html, game.html
  static/css, static/js
```

Le moteur de jeu (`engine.py`) ne connaît rien du transport (HTTP/WebSocket) : il expose une méthode `public_state(viewer_id)` qui ne révèle que ce que ce joueur a le droit de voir à cet instant. Cette séparation permet de réutiliser le moteur tel quel :
- aujourd'hui en **mode local "pass & play"** : un seul navigateur pilote toute la table, et l'interface demande de faire circuler l'appareil entre les joueurs (avec des écrans de transition qui cachent les cartes tant que le bon joueur n'a pas confirmé être devant l'écran) ;
- demain en **mode en ligne** : il suffira de connecter chaque joueur avec son propre WebSocket et son propre `viewer_id`, sans toucher au moteur de jeu.

### Choix d'implémentation et hypothèses

Les règles du jeu ne précisent pas tout ; voici les choix faits pour lever les ambiguïtés :
- **Snap** : la comparaison se fait sur la valeur/rang de la carte (un 8 quelle que soit sa couleur), pas sur la couleur exacte.
- **Appel de GABO** : dès qu'il est annoncé, toutes les cartes sont immédiatement révélées et la manche se termine (les autres joueurs ne rejouent pas de dernier tour).
- **Premier joueur de chaque nouvelle manche** : après un GABO, c'est le joueur suivant (sens horaire) après celui qui avait commencé la manche précédente qui commence, en sautant les joueurs éliminés.
- **Carte de départ de la défausse** : une fois que tout le monde a observé ses 2 cartes de départ, une carte est automatiquement retournée de la pioche vers la défausse avant même que le premier tour soit joué. Cela permet de tenter un snap dès le début de la manche, sans attendre qu'un joueur pioche et défausse.
- **Observation initiale** : les 2 cartes regardées au début sont toujours les 2 mêmes (les 2 premières de la main), pas un choix libre du joueur — cela reste fidèle à l'esprit "on observe une partie fixe de son jeu et on retient". En mode local (un seul écran partagé), cette observation se fait joueur par joueur (chacun avec ses 5 secondes), plutôt que simultanément comme ce serait le cas avec un appareil par joueur.

### Installation

Prérequis : Python 3.11+.

```bash
cd backend
pip install -r requirements.txt
```

### Lancer le jeu en local

```bash
cd backend
python -m uvicorn app.main:app --reload
```

Puis ouvrez `http://127.0.0.1:8000` dans votre navigateur : créez un compte, créez une partie en indiquant les noms des 2 à 6 joueurs qui vont se partager l'appareil, et jouez en suivant les instructions à l'écran (l'interface indique à qui de jouer et demande de faire circuler l'appareil au bon moment).

### Lancer les tests

```bash
cd backend
python -m pytest
```


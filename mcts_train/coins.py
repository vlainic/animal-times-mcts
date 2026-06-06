"""
Deck construction and coin tokens aligned with Godot ``deck_manager.gd``.

**Purpose**

The shipped game builds a 39-card deck: 37 territory cards (each with a unit “coin” type:
pirate / mount / cannon in paths) plus 2 wild ``treasure`` cards. This module reproduces that
**structure and shuffle policy** so the Python simulator can award draws on capture and
build the ``coins (P, T)`` observation channel (see ``features.py``).

**Type encoding (Python / observation)**

Godot paths use ``pirate``, ``mount``, ``cannon``. For neural tensors we map to integers::

    1 = saber   (was ``pirate`` in paths)
    2 = gun     (was ``mount``)
    3 = cannon  (was ``cannon``)

Wild cards: ``CoinToken.is_wild == True``; they do **not** occupy a territory column in
``(P, T)`` — use ``wild_per_player`` in ``build_observation``.

**Note**

Logical card paths use the ``res://Cards/...`` prefix to stay comparable to Godot; only
``path_to_token`` / ``_parse_card_path`` interpret them — no ``.tscn`` files are required
on disk for training.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .map_data import get_map_data, repo_root

# --- Path segment → observation coin kind (see module docstring) ---
_TYPE_TO_COIN = {"pirate": 1, "mount": 2, "cannon": 3}


@dataclass
class CoinToken:
    """
    One card/coin in a player's hand.

    Attributes:
        territory_idx: Tile index ``0..T-1`` for territory cards; ``-1`` for wild / unknown.
        coin_kind: For territory cards: ``1..3`` (saber/gun/cannon). Wilds use ``0`` here.
        is_wild: ``True`` for the two treasure cards from the deck.
    """

    territory_idx: int
    coin_kind: int
    is_wild: bool


def _parse_card_path(path: str) -> Tuple[int, int, bool]:
    """
    Parse a logical ``res://Cards/{continent}/{territory}/{type}.tscn`` path.

    Returns:
        Tuple ``(territory_idx, coin_kind_1_to_3, is_wild)``. On parse failure, returns a
        wild-like sentinel ``(-1, 0, True)`` so the deck never crashes training.
    """
    m = get_map_data()
    if "treasure" in path.lower():
        return -1, 0, True
    parts = path.replace("\\", "/").split("/")
    if len(parts) < 6:
        return -1, 0, True
    territory_name = parts[-2]
    type_file = parts[-1].replace(".tscn", "")
    kind = _TYPE_TO_COIN.get(type_file, 1)
    idx = m.name_to_idx.get(territory_name)
    if idx is None:
        return -1, 0, True
    return idx, kind, False


def create_balanced_deck(rng) -> List[str]:
    """
    Build the same 39-card deck as ``DeckManager.create_balanced_deck`` (Godot).

    Steps (mirrors GDScript):

    1. Append two treasure paths.
    2. Build a length-37 queue of unit types cycling pirate/mount/cannon.
    3. Shuffle continent order, then shuffle territories inside each continent.
    4. Emit ``res://Cards/{continent}/{territory}/{type}.tscn`` in that nested order.
    5. Shuffle the full deck with ``rng``.

    Args:
        rng: ``numpy.random.Generator`` (or any object with ``shuffle``).

    Returns:
        List of logical path strings (deck front = end of list for ``pop`` if you push
        left; ``draw_from_deck`` uses ``pop(0)`` from the front of a list treated as queue).
    """
    root = repo_root()
    territory_data = {
        "Mudflats": ["Frozen Mud", "Mud Hills", "Inner Mud", "Muddy Island", "Muddy Coast", "Dark Mud"],
        "Bamboovia": [
            "Bamboo Ridge",
            "Bamboo Forest",
            "Bamboo Mist",
            "Outer Bamboo",
            "Bamboo Valley",
            "Bamboo Beach",
            "Sacred Bamboo",
            "Wild Bamboo",
        ],
        "Riverside": ["Cold River", "Clear River", "Main River", "River Falls", "The Delta", "Stone Bridge", "Beaver Dam"],
        "Peaks": ["Stone Peak", "High Peak", "High Valley", "Central Peak", "Wind Ridge"],
        "Bushlands": ["Thorn Bush", "Sun Hollow", "Dry Basin", "Inner Bush", "The Lookout", "Wind Plains", "Bush Island"],
        "Eucalypta": ["Tree Island", "Deep Forest", "Crown Forest", "Old Trees"],
    }
    deck: List[str] = []
    deck.append(str(root / "Cards" / "treasure.tscn"))
    deck.append(str(root / "Cards" / "treasure.tscn"))

    unit_types = ["pirate", "mount", "cannon"]
    unit_type_queue = [unit_types[i % 3] for i in range(37)]

    continent_order = list(territory_data.keys())
    rng.shuffle(continent_order)

    for continent in continent_order:
        territories = list(territory_data[continent])
        rng.shuffle(territories)
        for territory in territories:
            ut = unit_type_queue.pop(0)
            path = f"res://Cards/{continent}/{territory}/{ut}.tscn"
            deck.append(path)

    rng.shuffle(deck)
    return deck


def path_to_token(path: str) -> CoinToken:
    """
    Convert a deck path string into a :class:`CoinToken`.

    Args:
        path: Entry from ``create_balanced_deck`` or discard pile.

    Returns:
        Token suitable to append to ``GameState.hands[p]``.
    """
    ti, k, wild = _parse_card_path(path)
    return CoinToken(territory_idx=ti, coin_kind=k if not wild else 0, is_wild=wild)


def draw_from_deck(deck: List[str], depot: List[str], rng) -> Optional[str]:
    """
    Pop one card from ``deck``; if empty, merge ``depot`` back in (shuffled) like Godot depot.

    Args:
        deck: Mutable draw pile (front = index 0).
        depot: Discards waiting to be reshuffled in.
        rng: Generator used to shuffle when refilling from depot.

    Returns:
        Path string, or ``None`` if both deck and depot are empty.
    """
    if not deck:
        if depot:
            deck.extend(depot)
            depot.clear()
            rng.shuffle(deck)
        else:
            return None
    return deck.pop(0)

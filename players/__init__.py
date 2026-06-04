"""
Player policies for ``mcts_train`` (Rookie baseline, future MCTS bot, …).

Import :class:`RookieBotPlayer` from ``mcts_train.players`` or
``mcts_train.players.rookie_bot_player``.
"""

from .mctsland_bot_player import MctslandBotPlayer, load_history_from_json
from .rookie_bot_player import RookieBotPlayer

__all__ = ["MctslandBotPlayer", "RookieBotPlayer", "load_history_from_json"]

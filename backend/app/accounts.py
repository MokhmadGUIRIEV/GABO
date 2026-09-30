from __future__ import annotations

import os

from sqlalchemy.orm import Session

from .models import GameRecord, PlayerResult, User


def admin_emails() -> set[str]:
    raw = os.environ.get("GABO_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(user: User) -> bool:
    return user.email.lower() in admin_emails()


def delete_account(db: Session, user: User) -> None:
    """Delete an account without erasing other players' history.

    - The player's name stays in the results of games they played (others
      still see those games), but the results are detached from the account,
      so the deleted player disappears from the leaderboard.
    - Games that only concern this account (local games, unfinished games)
      are deleted. Finished online games other accounts played in are handed
      over to one of those players, so they stay in everyone's history.
    """
    db.query(PlayerResult).filter(PlayerResult.user_id == user.id).update(
        {PlayerResult.user_id: None}, synchronize_session=False
    )

    for game in db.query(GameRecord).filter(GameRecord.created_by_user_id == user.id).all():
        other = (
            db.query(PlayerResult.user_id)
            .filter(PlayerResult.game_id == game.id, PlayerResult.user_id.is_not(None))
            .first()
        )
        if other is not None:
            game.created_by_user_id = other.user_id
        else:
            db.delete(game)

    db.flush()
    # The relationship cascade would otherwise delete games already handed over.
    db.expire(user, ["games"])
    db.delete(user)
    db.commit()

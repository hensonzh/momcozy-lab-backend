"""Drain encrypted auth mail after commit. Run as a supervised process next to API."""
import argparse
import asyncio

from app.core.settings import Settings
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.modules.auth.email import SmtpAuthEmailSender, deliver_next_email


async def run(once: bool) -> None:
    settings = Settings.from_env()
    settings.validate_for_startup()
    engine = create_db_engine(settings)
    sessions = create_session_factory(engine)
    sender = SmtpAuthEmailSender(settings)
    try:
        while True:
            async with sessions.begin() as session:
                outcome = await deliver_next_email(session, settings, sender)
            if once:
                return
            if outcome is None:
                await asyncio.sleep(2)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    asyncio.run(run(parser.parse_args().once))

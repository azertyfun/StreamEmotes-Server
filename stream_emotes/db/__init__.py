import os

from tortoise import Tortoise

async def init():
    await Tortoise.init(
        db_url=os.environ['POSTGRES_DSN'],
        modules={
            'models': [
                'stream_emotes.db.models.user',
                'stream_emotes.db.models.oauth',
                'stream_emotes.db.models.emote',
            ]
        },
    )

    await Tortoise.generate_schemas()

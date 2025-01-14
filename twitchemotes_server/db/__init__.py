import os

from tortoise import Tortoise

async def init():
    await Tortoise.init(
        db_url=os.environ['POSTGRES_DSN'],
        modules={
            'models': [
                'twitchemotes_server.db.models.user',
                'twitchemotes_server.db.models.oauth',
                'twitchemotes_server.db.models.emote',
            ]
        },
    )

    await Tortoise.generate_schemas()

from tortoise.models import Model
from tortoise import fields

class User(Model):
    id = fields.IntField(primary_key=True)
    temp_token = fields.CharField(max_length=256)
    temp_token_expires_at = fields.DatetimeField()
    minecraft_uuid = fields.UUIDField(null=True)
    twitch_id = fields.CharField(max_length=256, unique=True)
    oauth_bearer = fields.ForeignKeyField('models.OAuthBearer')
    last_emote_fetch = fields.DatetimeField(null=True)

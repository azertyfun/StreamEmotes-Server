from tortoise.models import Model
from tortoise import fields

class OAuthState(Model):
    id = fields.IntField(primary_key=True)
    state = fields.CharField(max_length=64)
    expires_at = fields.DatetimeField()

class OAuthBearer(Model):
    id = fields.IntField(primary_key=True)
    twitch_id = fields.CharField(max_length=256, unique=True)
    login = fields.CharField(max_length=256)
    display_name = fields.CharField(max_length=256)
    access_token = fields.TextField()
    refresh_token = fields.TextField()
    expires_at = fields.DatetimeField()

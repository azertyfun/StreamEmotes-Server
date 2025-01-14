from tortoise.models import Model
from tortoise import fields

class UserEmote(Model):
    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField('models.User')
    emote = fields.ForeignKeyField('models.Emote')

    class Meta:
        unique_together = (
            ('user', 'emote'),
        )

class Emote(Model):
    id = fields.CharField(max_length=64, primary_key=True)
    name = fields.CharField(max_length=32, unique=True)
    animated = fields.BooleanField()
    url = fields.TextField()

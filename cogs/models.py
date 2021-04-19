from tortoise import fields
from tortoise.models import Model


class HomeChannels(Model):
    guild_id = fields.BigIntField()
    channel_id = fields.BigIntField(null=True)

from tortoise import Tortoise, fields, run_async
from tortoise.models import Model


class Role(Model):
    id = fields.IntField(pk=True)
    name = fields.TextField()
    color = fields.SmallIntField()
    to_remove = fields.BooleanField(default=False)

    def __str__(self):
        return self.name


async def run():
    await Tortoise.init(db_url="sqlite://test.db", modules={"models": ["__main__"]})
    await Tortoise.generate_schemas()

    event = await Event.create(name="Test")
    await Event.filter(id=event.id).update(name="Updated name")

    print(await Event.filter(name="Updated name").first())
    # >>> Updated name

    await Event(name="Test 2").save()
    print(await Event.all().values_list("id", flat=True))
    # >>> [1, 2]
    print(await Event.all().values("id", "name"))
    # >>> [{'id': 1, 'name': 'Updated name'}, {'id': 2, 'name': 'Test 2'}]


if __name__ == "__main__":
    run_async(run())
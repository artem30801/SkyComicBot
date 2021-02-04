from tortoise.transactions import atomic


@atomic()
async def reshuffle(db_class, instance, number):
    changing_objs = await db_class.filter(number__gte=number).exclude(id=instance.id)
    for db_obj in changing_objs:
        number += 1
        db_obj.number = number
        await db_obj.save()


import asyncio

from tortoise.exceptions import DoesNotExist

import nest_asyncio_apply  # We want to apply nested asyncio as early as we can, so we do it in this import
import json

from tortoise import Tortoise, connections

class Router:
    def db_for_read(self, _):
        return "mysql"

    def db_for_write(self, _):
        return "sqlite"

async def migrate_item(item, to_con):
    try:
        migrated_item = await item.get(pk=item.pk, using_db=to_con)
        return migrated_item
    except DoesNotExist:
        pass
    model_desc = item.describe(serializable=False)
    fk_fields = [field['name'] for field in model_desc['fk_fields']]

    # Not sure if following code would handle these correctly, so making sure they are not used
    assert (len(model_desc['unique_together']) == 0)
    assert (len(model_desc['indexes']) == 0)
    assert (len(model_desc['o2o_fields']) == 0)
    assert (len(model_desc['m2m_fields']) == 0)

    # first make sure that all items this item relies on are migrated
    for fk_field in fk_fields:
        await item.fetch_related(fk_field)
        fk_item = getattr(item, fk_field)
        fk_item = await migrate_item(fk_item, to_con)
        setattr(item, fk_field, fk_item)

    await item.save(
        using_db=to_con,
        force_create=True # otherwise might skip saving since there might've been no changes
    )
    await item.refresh_from_db(using_db=to_con)
    return item

async def run_migration(from_con, to_con):
    for model_name, model in Tortoise.apps.get('models').items():
        print(f"Synchronizing {model_name}")

        items = model.all().using_db(from_con)
        async for item in items:
            await migrate_item(item, to_con)


async def main():
    config = {}
    with open("config.json", "r") as config_file:
        config = json.load(config_file)
    config = config["migration"]

    models = ["cogs.permissions", "cogs.roles", "cogs.timezones", "cogs.channels", "cogs.automod", "cogs.greetings", ]

    # generate tables for sqlite
    print("Generating tables for SQLite")
    await Tortoise.init(db_url=config["sqlite"], modules={"models": models})
    await Tortoise.generate_schemas()
    await Tortoise.close_connections()

    # Connect to both databases
    await Tortoise.init({
        'connections': {
            "mysql": config["mysql"],
            "sqlite": config["sqlite"]
        },
        "apps": {
            "models": {
                "models": models,
                "default_connection": "mysql"
            }
        },
        "routers": ["__main__.Router"]
    })
    await Tortoise.generate_schemas()
    print("Connected to both databases, starting a migration")

    # run migration
    await run_migration(connections.get("mysql"), connections.get("sqlite"))
    await Tortoise.close_connections()
    print("done")


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
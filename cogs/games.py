import logging

import asyncio
import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext, ComponentContext
from discord_slash.utils.manage_commands import create_option, create_choice
from discord_slash.utils.manage_components import create_actionrow, create_button, ButtonStyle, wait_for_any_component

from datetime import datetime, timedelta

import cogs.cog_utils as utils
from cogs.cog_utils import guild_ids

logger = logging.getLogger(__name__)

rsp_emojis = {"rock": "🪨", "paper": "🧻", "scissors": "✂️"}


class Games(utils.AutoLogCog, utils.StartupCog):
    """Fun games!"""

    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot
        self.timeout = 15 * 60

    @staticmethod
    def rsp_winner(moves):
        moves = sorted(moves)
        if moves[0] == moves[1]:
            return None, "Nobody lose, nobody wins! Rematch?"
        if moves[0] == "paper":
            if moves[1] == "rock":
                return "paper", "{} totally wiped out {}!"
            if moves[1] == "scissors":
                return "scissors", "{} cut {} in half!"
        else:
            return "rock", "{} rocks! And {} is crushed!"

    def make_rsp_embed(self, players, moves, started, is_ended=False):
        embed = utils.bot_embed(self.bot)
        embed.title = "Rock 🪨! Paper 🧻! Scissors ✂️!"
        ends_at = started + timedelta(seconds=self.timeout)
        now = datetime.utcnow()
        both_moves = moves[0] and moves[1]

        winner_index = None
        if both_moves:
            winner, win_text = self.rsp_winner(moves)
            if winner:
                winner_index = moves.index(winner)
                embed.add_field(name=f"🎉 {players[winner_index].name} won! 🎉",
                                value=win_text.format(players[winner_index].display_name,
                                                      players[winner_index - 1].display_name),
                                inline=False)
            else:
                embed.add_field(name="Draw!", value=win_text, inline=False)
            embed.set_footer(text="Use `/play rock_paper_scissors` for another game!",
                             icon_url=self.bot.user.avatar_url)
        elif is_ended or now > ends_at:
            embed.set_footer(text="Game ended at", icon_url=self.bot.user.avatar_url)
            embed.timestamp = now
        else:
            embed.description = "Join the game! Press button to play!"
            embed.set_footer(text="Game ends at", icon_url=self.bot.user.avatar_url)
            embed.timestamp = ends_at

        for i, player in enumerate(players):
            if player:
                text = f"{player.mention}\n{player}"
                if move := moves[i]:
                    if not both_moves:
                        text += "\n Already made a move!"
                    else:
                        text += f"\n Made a move: {rsp_emojis[move]}"
                else:
                    text += f"\n Haven't made a move yet!"
            else:
                text = "Nobody wanted to play :(" if is_ended else "Free spot!\nMake a move to join the game!"

            title = f"Player {i + 1}"
            if winner_index == i:
                title += "- winner!"

            embed.insert_field_at(i, name=title, value=text)

        return embed

    @cog_ext.cog_subcommand(base="play", name="rock_paper_scissors",
                            options=[
                                create_option(
                                    name="player1",
                                    description="Specify first player",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                                create_option(
                                    name="player2",
                                    description="Specify first player",
                                    option_type=discord.Member,
                                    required=False,
                                ),

                            ],
                            guild_ids=guild_ids)
    async def rock_paper_scissors(self, ctx: SlashContext, player1=None, player2=None):
        players = [player1, player2]
        if player1 == player2 and player1 and player2:
            raise commands.BadArgument("You can't set one member as two players at once!")
        if (player1 and player1.bot) or (player2 and player2.bot):
            raise commands.BadArgument("We bots are smart but we cant play rock paper scissors yet ;)")

        players_moves = [None, None]
        components = [create_actionrow(
            create_button(style=ButtonStyle.gray, label="Rock", emoji="🪨"),
            create_button(style=ButtonStyle.gray, label="Paper", emoji="🧻"),
            create_button(style=ButtonStyle.gray, label="Scissors", emoji="✂️"),
        )]
        started = datetime.utcnow()
        embed = self.make_rsp_embed(players, players_moves, started)
        msg = await ctx.send(embed=embed, components=components)  # Let's play rock paper scissors!"
        components_moves = {button["custom_id"]: button["label"].lower() for button in components[0]["components"]}

        async def move(button_ctx: ComponentContext):
            try:
                player_index = players.index(button_ctx.author)
            except ValueError:  # sender is not in players list
                try:
                    player_index = players.index(None)
                except ValueError:  # no free places
                    await button_ctx.send("Sorry, but all player spots are already taken!\n"
                                          "You can start a new game with `/game rock_paper_scissors` command!",
                                          hidden=True)
                    return
                else:
                    players[player_index] = button_ctx.author

            if players_moves[player_index]:
                await button_ctx.send("You already made your move! Wait for your opponent.", hidden=True)
                return

            move_str = components_moves[button_ctx.custom_id]
            players_moves[player_index] = move_str
            logger.debug(f"Player {player_index} ({button_ctx.author}) made a move: {move_str} ")
            embed = self.make_rsp_embed(players, players_moves, started)
            await button_ctx.edit_origin(embed=embed)

        async def wait_moves():
            while None in players_moves:
                button_ctx = await wait_for_any_component(self.bot, msg)
                await move(button_ctx)

        to_edit = {}
        try:
            await asyncio.wait_for(wait_moves(), timeout=self.timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            embed = self.make_rsp_embed(players, players_moves, started, is_ended=True)
            to_edit["embed"] = embed

        for component in components[0]["components"]:
            component["disabled"] = True
        to_edit["components"] = components

        await msg.edit(**to_edit)


def setup(bot):
    bot.add_cog(Games(bot))

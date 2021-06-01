import logging

import asyncio
import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext, ComponentContext
from discord_slash.utils.manage_commands import create_option, create_choice
from discord_slash.utils.manage_components import create_actionrow, create_button, ButtonStyle, wait_for_any_component

import random
import enum
from datetime import datetime, timedelta

import cogs.cog_utils as utils
from cogs.cog_utils import guild_ids

import sys as sus

logger = logging.getLogger(__name__)

rsp_emojis = {"rock": "🪨", "paper": "🧻", "scissors": "✂️"}


class GameStates(enum.Enum):
    waiting_players = 0
    waiting_move = 1
    game_timeout = 2
    has_winner = 3


class UnoCard:
    special_types = ["+2", "reverse", "block", "wild", "+4"]
    colors = [ButtonStyle.red, ButtonStyle.blue, ButtonStyle.green, ButtonStyle.gray]

    def __init__(self, card_number, card_color):
        self.number = card_number
        self.color = card_color

        self.button_id = None

    # @classmethod
    # def get_random(cls):
    #     numbers = list(range(0, 10)) + cls.special_types
    #     number = range()
    #     color = random.choice(cls.colors)
    #     return cls(card_number=number, card_color=color)
    #

    def is_valid_move(self, other):
        return self.number == other.number or self.color == other.color

    def is_same_card(self, other):
        return self.number == other.number and self.color == other.color

    def generate_button(self):
        label = self.number
        emoji = None
        button = create_button(style=self.color, label=label, emoji=emoji, custom_id=self.button_id)
        self.button_id = button["custom_id"]
        return button


class UnoGame:
    def __init__(self, cog):
        self.cog = cog

        self.max_players = 0
        self.players = []
        self.decks = []
        self.decks_valid = []

        self.direction_down = True
        self.top_card = None

        state = 0

    def main_menu_embed(self):
        embed = utils.bot_embed(self.cog.bot)
        embed.title = "Uno!"

    async def play(self):
        pass

    async def main_menu(self):
        pass

    async def deck(self):
        pass


class RSPGame:
    def __init__(self, ctx, cog, players=None):
        self.ctx = ctx
        self.cog = cog

        self.state = GameStates.waiting_move

        self.started_at = datetime.utcnow()
        self.game_ended = False

        self.players = players or [None, None]
        self.moves = [None, None]

        self.buttons = [create_actionrow(
            create_button(style=ButtonStyle.gray, label="Rock", emoji="🪨"),
            create_button(style=ButtonStyle.gray, label="Paper", emoji="🧻"),
            create_button(style=ButtonStyle.gray, label="Scissors", emoji="✂️"),
        )]

        self.moves_binding = {button["custom_id"]: button["label"].lower()
                              for button in self.buttons[0]["components"]}

    async def play(self):
        embed = self.make_rsp_embed()
        message = await self.ctx.send(embed=embed, components=self.buttons)

        to_edit = {}
        try:
            await asyncio.wait_for(self.wait_moves(message), timeout=self.cog.global_timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            self.state = GameStates.game_timeout
            to_edit["embed"] = self.make_rsp_embed()

        for button in self.buttons[0]["components"]:
            button["disabled"] = True
        to_edit["components"] = self.buttons

        await message.edit(**to_edit)

    async def wait_moves(self, message):
        while self.state is GameStates.waiting_move:
            button_ctx = await wait_for_any_component(self.cog.bot, message)
            await self.player_move(button_ctx)

    async def player_move(self, button_ctx: ComponentContext):
        try:
            player_index = self.players.index(button_ctx.author)
        except ValueError:  # sender is not in players list
            try:
                player_index = self.players.index(None)
            except ValueError:  # no free places
                await button_ctx.send("Sorry, but all player spots are already taken!\n"
                                      "You can start a new game with `/game rock_paper_scissors` command!",
                                      hidden=True)
                return
            else:
                self.players[player_index] = button_ctx.author  # new player joined

        if self.moves[player_index]:
            await button_ctx.send("You already made your move! Wait for your opponent.", hidden=True)
            return

        move_str = self.moves_binding[button_ctx.custom_id]
        self.moves[player_index] = move_str
        logger.debug(f"Player {player_index} ({button_ctx.author}) made a move: {move_str} ")

        if all(self.moves):
            self.state = GameStates.has_winner

        embed = self.make_rsp_embed()
        await button_ctx.edit_origin(embed=embed)

    def get_winner(self):
        moves = sorted(self.moves)
        if moves[0] == moves[1]:
            return None, "Nobody lose, nobody wins! Rematch?"
        if moves[0] == "paper":
            if moves[1] == "rock":
                return "paper", "{} totally wiped out {}!"
            if moves[1] == "scissors":
                return "scissors", "{} cut {} in half!"
        else:
            return "rock", "{} rocks! And {} is crushed!"

    def make_rsp_embed(self):
        embed = utils.bot_embed(self.cog.bot)
        embed.title = "Rock 🪨! Paper 🧻! Scissors ✂️!"

        winner_index = None
        if self.state is GameStates.has_winner:
            winner, win_text = self.get_winner()
            if winner:
                winner_index = self.moves.index(winner)
                embed.add_field(name=f"🎉 {self.players[winner_index].name} won! 🎉",
                                value=win_text.format(self.players[winner_index].display_name,
                                                      self.players[winner_index - 1].display_name),
                                inline=False)
            else:
                embed.add_field(name="Draw!", value=win_text, inline=False)
            embed.set_footer(text="Use `/play rock_paper_scissors` for another game!",
                             icon_url=self.cog.bot.user.avatar_url)
        elif self.state is GameStates.game_timeout:
            embed.set_footer(text="Game ended at", icon_url=self.cog.bot.user.avatar_url)
            embed.timestamp = datetime.utcnow()
        else:
            embed.description = "Join the game! Press button to play!"
            embed.set_footer(text="Game ends at", icon_url=self.cog.bot.user.avatar_url)
            embed.timestamp = self.started_at + timedelta(seconds=self.cog.global_timeout)

        for i, player in enumerate(self.players):
            if player:
                text = f"{player.mention}\n{player}"
                if move := self.moves[i]:
                    if self.state is GameStates.waiting_move:
                        text += "\n Already made a move!"
                    else:
                        text += f"\n Made a move: {rsp_emojis[move]}"
                else:
                    text += f"\n Haven't made a move yet!"
            else:
                text = "Nobody wanted to play :(" if self.state is GameStates.game_timeout \
                    else "Free spot!\nMake a move to join the game!"

            title = f"Player {i + 1}"
            if winner_index == i:
                title += "- winner!"

            embed.insert_field_at(i, name=title, value=text)

        return embed


class Games(utils.AutoLogCog, utils.StartupCog):
    """Fun games!"""

    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot
        self.global_timeout = 15 * 60
        self.move_timeout = 1 * 60

    @cog_ext.cog_subcommand(base="play", name="rock_paper_scissors",
                            options=[
                                create_option(
                                    name="player1",
                                    description="Specify first player to invite",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                                create_option(
                                    name="player2",
                                    description="Specify second player to invite",
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

        game = RSPGame(ctx, self, players)
        await game.play()

    @cog_ext.cog_subcommand(base="play", name="tic_tac_toe",
                            options=[
                                create_option(
                                    name="player1",
                                    description="Specify first player",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                                create_option(
                                    name="player2",
                                    description="Specify second player",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                                create_option(
                                    name="size",
                                    description="Size of the field",
                                    option_type=int,
                                    required=False,
                                    choices=[create_choice(name="3/3", value=3),
                                             create_choice(name="4/4", value=4),
                                             create_choice(name="5/5", value=5),
                                             ]
                                ),

                            ],
                            guild_ids=guild_ids)
    async def tic_tac_toe(self, ctx, player1=None, player2=None, size=3):
        players = [player1, player2]
        if player1 == player2 and player1 and player2:
            raise commands.BadArgument("You can't set one member as two players at once!")
        if (player1 and player1.bot) or (player2 and player2.bot):
            raise commands.BadArgument("We bots are smart but we cant play rock paper scissors yet ;)")

        # components = [create_actionrow(
        #     create_button(style=ButtonStyle.gray, label="Rock", emoji="🪨"),
        #     create_button(style=ButtonStyle.gray, label="Paper", emoji="🧻"),
        #     create_button(style=ButtonStyle.gray, label="Scissors", emoji="✂️"),
        # )]
        components = []
        components_moves = {}
        for i in range(size):
            row = []
            for j in range(size):
                button = create_button(style=ButtonStyle.gray, label="")
                row.append(button)
                components_moves[button["custom_id"]] = (i, j)
            components.append(create_actionrow(*row))


def setup(bot):
    bot.add_cog(Games(bot))

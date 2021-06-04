import logging

import asyncio
import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext, ComponentContext
from discord_slash.utils.manage_commands import create_option, create_choice
from discord_slash.utils.manage_components import create_actionrow, create_button, emoji_to_dict, ButtonStyle, \
    wait_for_any_component

import random
import enum
import itertools
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


class PlayerStates(enum.Enum):
    no_player = -1
    waiting_first_move = 0
    waiting_move = 1
    skipped_move = 2
    made_move = 3


class NoFreePlayerSlots(discord.DiscordException):
    pass


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


class Player:
    def __init__(self, member=None):
        self.state = PlayerStates.waiting_move
        self.member = member
        self.new = True

        self._notify_task = None
        self._notify_message: discord.Message = None

    def check_new(self):
        if self.new:
            self.new = False
            return True
        return False

    def notify_start(self, message, delay, delete_after):
        self._notify_task = asyncio.create_task(self.notify(message, delay, delete_after))

    async def notify_cancel(self):
        if self._notify_task is not None:
            self._notify_task.cancel()

        if self._notify_message is not None:
            await self._notify_message.delete()
            self._notify_message = None

    async def notify(self, message, delay, delete_after):
        await asyncio.sleep(delay)
        self._notify_message = await message.channel.send(content=f"{self.member.mention}, it's your move!",
                                                          delete_after=delete_after)


class Game:
    command_name: str
    game_title: str

    def __init__(self, ctx, cog):
        self.ctx = ctx
        self.cog = cog

        self.state = GameStates.waiting_move
        self.started_at = datetime.utcnow()

        self.max_players = 0 or self.max_players
        self.players = [None] * self.max_players
        self._player_mapping = {}
        self._next_index = itertools.count()

        self.game_message = None

    def get_player_index(self, member: discord.Member):
        try:
            player_index = self._player_mapping[member.id]
        except KeyError:  # sender is not in players list
            if len(self._player_mapping) == self.max_players:
                raise NoFreePlayerSlots

            player_index = next(self._next_index)
            self.players[player_index] = Player(member)  # new player joined
            self._player_mapping[member.id] = player_index

        return player_index

    async def check_player_index(self, ctx):
        try:
            player_index = self.get_player_index(ctx.author)
        except NoFreePlayerSlots:
            await ctx.send(f"Sorry, but all player spots are already taken!\n"
                           f"You can start a new game with `/play {self.command_name}` command!",
                           hidden=True)
            return

        if self.players[player_index].state is not PlayerStates.waiting_move:
            await ctx.send("You already made your move! Wait for your opponent.", hidden=True)
            return

        return player_index

    def get_next_player(self, player_index):
        raise NotImplemented

    async def play(self):
        embed = self.make_embed()
        self.game_message = await self.ctx.send(embed=embed, components=self.buttons)

        to_edit = {}
        try:
            await asyncio.wait_for(self.wait_moves(self.game_message), timeout=self.cog.global_timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            self.state = GameStates.game_timeout
            to_edit["embed"] = self.make_embed()

        for row in self.buttons:
            for button in row["components"]:
                button["disabled"] = True
        to_edit["components"] = self.buttons

        await self.game_message.edit(**to_edit)

    async def wait_moves(self, message):
        while self.state is GameStates.waiting_move:
            button_ctx = await wait_for_any_component(self.cog.bot, message)
            await self.player_move(button_ctx)

    async def player_move(self, button_ctx: ComponentContext):
        raise NotImplementedError

    def make_embed(self):
        raise NotImplementedError


class TwoPlayerGame(Game):
    def __init__(self, ctx, cog, players=None):
        self.max_players = 2
        super().__init__(ctx, cog)
        self.players = players or self.players

        self.buttons = []

    def make_embed(self):
        embed = utils.bot_embed(self.cog.bot)
        embed.title = self.game_title

        winner_index = None
        if self.state is GameStates.has_winner:
            winner_index, win_text = self.get_winner()
            if winner_index is not None:
                embed.add_field(name=f"🎉 {self.players[winner_index].member.name} won! 🎉",
                                value=win_text.format(
                                    self.players[winner_index].member.display_name,
                                    self.players[self.get_next_player(winner_index)].member.display_name
                                ),
                                inline=False)
            else:
                embed.add_field(name="Draw!", value=win_text, inline=False)
            embed.set_footer(text=f"Use `/play {self.command_name}` for another game!",
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
                text = f"{player.member.mention}\n{player.member}"
                text += self.additional_player_text(i)
            else:
                text = "Nobody wanted to play :(" if self.state is GameStates.game_timeout \
                    else "Free spot!\nMake a move to join the game!"

            title = self.get_player_title(i)
            if winner_index == i:
                title += "- winner!"

            embed.insert_field_at(i, name=title, value=text)

        return embed

    def additional_player_text(self, player_index):
        return ""

    def get_player_title(self, player_index):
        return f"Player {player_index + 1}"

    def get_winner(self):
        raise NotImplementedError

    def get_next_player(self, player_index):
        return 0 if player_index == 1 else 1


class RPSGame(TwoPlayerGame):
    command_name = "rock_paper_scissors"
    game_title = "Rock 🪨! Paper 🧻! Scissors ✂️!"

    def __init__(self, ctx, cog, players=None):
        super().__init__(ctx, cog, players)

        self.moves = [None, None]

        self.buttons = [create_actionrow(
            create_button(style=ButtonStyle.gray, label="Rock", emoji="🪨"),
            create_button(style=ButtonStyle.gray, label="Paper", emoji="🧻"),
            create_button(style=ButtonStyle.gray, label="Scissors", emoji="✂️"),
        )]

        self.moves_binding = {button["custom_id"]: button["label"].lower()
                              for button in self.buttons[0]["components"]}

    async def player_move(self, button_ctx: ComponentContext):
        if (player_index := await self.check_player_index(button_ctx)) is None:
            return

        move_str = self.moves_binding[button_ctx.custom_id]
        self.moves[player_index] = move_str
        self.players[player_index].state = PlayerStates.made_move
        logger.debug(f"Player {player_index} ({button_ctx.author}) made a move: {move_str} ")

        if all(self.moves):
            self.state = GameStates.has_winner

        embed = self.make_embed()
        await button_ctx.edit_origin(embed=embed)

    def get_winner(self):
        winner, message = self._get_winner()
        winner_index = self.moves.index(winner) if winner is not None else None
        return winner_index, message

    def _get_winner(self):
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

    def additional_player_text(self, player_index):
        if self.state is GameStates.has_winner:
            return f"\n Made a move: {rsp_emojis[self.moves[player_index]]}"
        elif self.players[player_index].state is PlayerStates.made_move:
            return "\n Already made a move!"
        elif self.players[player_index].state is PlayerStates.waiting_move:
            return f"\n Haven't made a move yet!"


class TTTGame(TwoPlayerGame):
    command_name = "tic_tac_toe"
    game_title = "🟢 Tic tac toe! ❌"

    empty_tile = " "

    def __init__(self, ctx, cog, players=None, size=3, winning_row=3):
        super().__init__(ctx, cog, players)
        self.size = size

        winning_row = winning_row or (size - 1 if size > 3 else size)
        self.winning_row = min(winning_row, size)

        self.winner_index = None
        self.move_count = 0

        self.empty_tile = emoji_to_dict(discord.utils.get(self.cog.bot.emojis, name="blank"))
        self._o_emoji = emoji_to_dict(discord.utils.get(self.cog.bot.emojis, name="ttt_circle"))

        self.buttons = []
        self.moves_binding = {}
        for i in range(size):
            row = []
            for j in range(size):
                button = create_button(style=ButtonStyle.gray, emoji=self.empty_tile)
                row.append(button)
                self.moves_binding[button["custom_id"]] = (i, j)
            self.buttons.append(create_actionrow(*row))

    def player_place(self, player_index):
        placement = [(self._o_emoji, ButtonStyle.green), ("✖️", ButtonStyle.red)]
        return placement[player_index]

    def get_button(self, i, j):
        return self.buttons[i]["components"][j]

    async def player_move(self, button_ctx: ComponentContext):
        if (player_index := await self.check_player_index(button_ctx)) is None:
            return

        player = self.players[player_index]
        i, j = self.moves_binding[button_ctx.custom_id]
        button = self.get_button(i, j)
        if button["emoji"] != self.empty_tile:
            if player.check_new():
                await button_ctx.defer(edit_origin=True)
                embed = self.make_embed()
                await button_ctx.edit_origin(embed=embed)

            await button_ctx.send("Sorry, but this tile is already taken! Make another move!", hidden=True)
            return

        await button_ctx.defer(edit_origin=True)

        move_str, color = self.player_place(player_index)
        button["emoji"] = emoji_to_dict(move_str)
        button["style"] = color

        player.state = PlayerStates.made_move
        await player.notify_cancel()

        boy_next_door: Player = self.players[self.get_next_player(player_index)]
        if boy_next_door:
            boy_next_door.state = PlayerStates.waiting_move
            boy_next_door.notify_start(self.game_message, self.cog.notify_timeout, self.cog.move_timeout)

        self.move_count += 1

        logger.debug(f"Player {player_index} ({button_ctx.author}) made a move: "
                     f"{('O', 'X')[player_index]} ({i}, {j}) ")

        if self.check_winner(move_str, i, j):
            self.state = GameStates.has_winner
            self.winner_index = player_index
        elif self.move_count == self.size ** 2:
            self.state = GameStates.has_winner

        embed = self.make_embed()
        await button_ctx.edit_origin(embed=embed, components=self.buttons)

    def get_winner(self):
        text = "Ney, it's a tie! Wanna try again?" if self.winner_index is None else "{} won against {}!"
        return self.winner_index, text

    def check_winner(self, move_str, i, j):
        horizontal = self.check_line(move_str, i, j, 0, 1)
        vertical = self.check_line(move_str, i, j, 1, 0)
        diagonal1 = self.check_line(move_str, i, j, 1, 1)
        diagonal2 = self.check_line(move_str, i, j, 1, -1)
        return horizontal or vertical or diagonal1 or diagonal2

    def check_line(self, move_str, i, j, dx=0, dy=0):
        count = self.check_line_side(move_str, i, j, dx, dy) + \
                self.check_line_side(move_str, i, j, dx * -1, dy * -1) - 1
        return count >= self.winning_row

    def check_line_side(self, move_str, i, j, dx=0, dy=0):
        count = 0
        while 0 <= i < self.size and 0 <= j < self.size:
            if self.get_button(i, j)["emoji"]["name"] == move_str:
                count += 1
            else:
                return count

            i += dx
            j += dy

        return count

    def make_embed(self):
        embed = super().make_embed()
        embed.insert_field_at(0, name="Game rules",
                              value=f"Grid size: **{self.size}/{self.size}**\n"
                                    f"Winning row size: **{self.winning_row}**\n"
                                    f"The player who succeeds in placing **{self.winning_row}** of their marks "
                                    f"in a diagonal, horizontal, or vertical row is the winner.",
                              inline=False)
        return embed

    def additional_player_text(self, player_index):
        if self.state is GameStates.has_winner:
            if player_index == self.winner_index:
                return "\n Made winning move!"
            else:
                return "\n Didn't win this time"

        elif self.players[player_index].state is PlayerStates.made_move:
            return "\n Already made a move!"
        elif self.players[player_index].state is PlayerStates.waiting_move:
            return f"\n Make your move now!"

    def get_player_title(self, player_index):
        symbols = ["🟢", "❌"]  # ⭕
        return f"{symbols[player_index]} " + super().get_player_title(player_index)


class Games(utils.AutoLogCog, utils.StartupCog):
    """Fun games!"""

    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot
        self.global_timeout = 20 * 60
        self.notify_timeout = 1 * 60
        self.move_timeout = 5 * 60

    async def check_2_players(self, ctx, player1, player2):
        players = [player1, player2]

        if player1 == player2 and player1 and player2:
            raise commands.BadArgument("You can't set one member as two players at once!")
        if (player1 and player1.bot) or (player2 and player2.bot):
            raise commands.BadArgument("We bots are smart but we cant play games yet ;)")

        return players

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
        """Multiplayer game! Assert dominance in epic battle of luck and wits!"""
        players = await self.check_2_players(ctx, player1, player2)
        game = RPSGame(ctx, self, players)
        await game.play()

    @cog_ext.cog_subcommand(base="play", name="tic_tac_toe",
                            options=[
                                create_option(
                                    name="size",
                                    description="Size of the field",
                                    option_type=int,
                                    required=False,
                                    choices=[create_choice(name="tic-tac", value=2),
                                             create_choice(name="3/3", value=3),
                                             create_choice(name="4/4", value=4),
                                             create_choice(name="5/5", value=5),
                                             ]
                                ),
                                create_option(
                                    name="winning_row",
                                    description="Length of consecutive row to win",
                                    option_type=int,
                                    required=False,
                                    choices=[create_choice(name="3", value=3),
                                             create_choice(name="4", value=4),
                                             create_choice(name="5", value=5),
                                             ]
                                ),
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
                            ],
                            guild_ids=guild_ids)
    async def tic_tac_toe(self, ctx, size=3, winning_row=None, player1=None, player2=None, ):
        """Multiplayer game! Tic-tac-toe! Noughts and crosses! Xs and Os! Play with friends!"""
        players = await self.check_2_players(ctx, player1, player2)
        game = TTTGame(ctx, self, players, size, winning_row)
        await game.play()


def setup(bot):
    bot.add_cog(Games(bot))

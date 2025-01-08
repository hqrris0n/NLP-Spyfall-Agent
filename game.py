import asyncio
import random
from enum import Enum
from functools import lru_cache
from itertools import chain

import pandas as pd  # type: ignore
import pygame
import soundfile as sf  # type: ignore
from tqdm import tqdm  # type: ignore

from agent import AGENT_REGISTRY, Agent
from data import *
from nlp import *
from util import *


# Used to describe how the game ended or if it is ongoing
class GameState(Enum):
    RUNNING = "running"
    SPY_GUESSED_RIGHT = "spy guessed right"
    SPY_GUESSED_WRONG = "spy guessed wrong"
    SPY_INDICTED = "spy indicted"
    NON_SPY_INDICTED = "non-spy indicted"
    NO_ONE_INDICTED = "no one indicted"


class Game:
    """Used to run a game of Spyfall
    Consists of multiple rounds
    """

    n_players: int
    player_names: list[str]
    n_rounds: int

    location: Location
    spy: int
    questioner: int  # current questioner
    last_questioner: int = -1  # last questioner
    players: list[Agent]
    player_nlps: list[TokenCounterWrapper]  # each keeps track of tokens per round
    rounds: list["Round"]
    game_state: GameState

    # can be optionally set to visualize how many rounds have been played
    tqdm_bar: tqdm | None = None

    spy_scoring = {
        GameState.RUNNING: 0,
        GameState.SPY_GUESSED_RIGHT: 4,
        GameState.SPY_GUESSED_WRONG: 0,
        GameState.SPY_INDICTED: 0,
        GameState.NON_SPY_INDICTED: 4,
        GameState.NO_ONE_INDICTED: 2,
    }
    nonspy_scoring = {
        GameState.RUNNING: 0,
        GameState.SPY_GUESSED_RIGHT: 0,
        GameState.SPY_GUESSED_WRONG: 1,
        GameState.SPY_INDICTED: 1,
        GameState.NON_SPY_INDICTED: 0,
        GameState.NO_ONE_INDICTED: 0,
    }

    def __init__(
        self,
        nlp: NLP,
        player_names: list[str] | None = None,
        n_rounds: int = 20,
    ):
        # init game
        if player_names is None:
            player_names = list(AGENT_REGISTRY.keys())
        n_players = self.n_players = len(player_names)
        assert n_players >= 3, "need at least 3 players"
        self.player_names = player_names
        self.n_rounds = n_rounds

        self.location = random.choice(list(Location))
        self.spy = random.randint(0, n_players - 1)
        self.questioner = random.randint(0, n_players - 1)
        self.players = []
        self.player_nlps = []
        self.rounds: list[Round] = []
        self.game_state = GameState.RUNNING

        for i, player_class_name in enumerate(player_names):
            player_class = AGENT_REGISTRY[player_class_name]
            player_nlp = TokenCounterWrapper(nlp)
            given_location = self.location if i != self.spy else None
            player_instance = player_class(
                given_location, n_players, n_rounds, nlp=NLPProxy(player_nlp)
            )
            self.players.append(player_instance)
            self.player_nlps.append(player_nlp)

        # povs maps global player index to local player index
        self._povs = [list(range(1, n_players)) for _ in range(n_players)]
        for i, pov in enumerate(self._povs):
            random.shuffle(pov)
            pov.insert(i, 0)  # global index i always maps to local index 0

        # r_povs maps local player index to global player index
        self._r_povs = [[0] * (n_players) for _ in range(n_players)]
        for i in range(n_players):
            for player, player_w_pov in enumerate(self._povs[i]):
                self._r_povs[i][player_w_pov] = player

    def add_pov(self, player: int, pov: int):
        """adds a point of view to a player index"""
        return self._povs[pov][player]

    def reverse_pov(self, player: int, pov: int):
        """Remove a point of view from a player index"""
        return self._r_povs[pov][player]

    def play(self):
        """runs the game"""
        asyncio.get_event_loop().run_until_complete(self.play_())

    async def play_(self):
        tqdm_bar = (
            self.tqdm_bar
            if self.tqdm_bar
            else tqdm(total=self.n_rounds, desc="Running Game, Rounds", colour="green")
        )

        for _ in range(self.n_rounds):
            round = Round(self)
            await round.play()
            tqdm_bar.update(1)
            self.rounds.append(round)
            if self.game_state != GameState.RUNNING:
                return
        tqdm_bar.update(self.n_rounds - len(self.rounds))
        self.game_state = GameState.NO_ONE_INDICTED

    def get_scores(self) -> pd.Series:
        """Gets the scores of all players as a pandas series

        Returns:
            pd.Series: Pandas Series with the scores
                index: player names
                values: score
        """
        scores_list = [self.nonspy_scoring[self.game_state]] * self.n_players
        scores_list[self.spy] = self.spy_scoring[self.game_state]

        scores = pd.Series(data=scores_list, index=self.player_names)
        scores = scores.groupby(scores.index).mean()
        return scores

    def get_percent_right_votes(self) -> pd.Series:
        """Gets the percent of right votes for each player as a pandas series

        Returns:
            pd.Series: Pandas Series with the percent of right votes
                index: player names
                values: percent of right votes
        """
        votes = np.array(
            [
                round.player_votes
                for round in self.rounds
                if hasattr(round, "player_votes")
            ]
        )

        if len(votes) == 0:
            return pd.Series(index=list(set(self.player_names)))

        percent_right_votes = np.mean(votes == self.spy, axis=0)
        percent_right_votes[self.spy] = np.nan
        series = pd.Series(data=percent_right_votes, index=self.player_names)
        series = series.groupby(series.index).mean()
        return series

    @lru_cache
    def get_conversation(self) -> pd.DataFrame:
        """Gets the conversation as a pandas dataframe

        Returns:
            pd.DataFrame: Pandas DataFrame with the conversation
                columns: player id, message
        """
        conv_list = list(chain(*[round.get_conversation() for round in self.rounds]))
        df = pd.DataFrame(conv_list, columns=["player", "message"])
        return df

    def pregenerate_audio(self):
        """pre-generates audio for the game"""
        for round in tqdm(
            self.rounds, desc="Pregenerating Audio, Rounds", colour="green"
        ):
            round.pregenerate_audio()

    def render(self):
        """Visualizes the game and plays the audio"""
        # init pygame
        sr = self.rounds[0].audio[0][2]
        pygame.mixer.pre_init(frequency=sr, channels=1, allowedchanges=0)
        pygame.init()

        for round in self.rounds:
            round.render()
        # close pygame

    def save_audio(self, path: str):
        """saves the audio to a path"""
        self.rounds[0].audio, "need to pregenerate audio"
        sr = self.rounds[0].audio[0][2]
        audio_list = [a for round in self.rounds for _, a, _ in round.audio]
        audio_list = [np.pad(a, (0, sr // 2)) for a in audio_list]
        comb_audio = np.concatenate(audio_list)
        # save audio to path
        sf.write(path, comb_audio, sr)

    def __str__(self):
        return f"Location: {self.location}, Spy: {self.spy}, Ending: {self.game_state}"


class Round:
    """Used to run a round of Spyfall
    Uses a game object to track the current state
    Uses instance variables to log the round's events"""

    questioner: int
    question: str
    answerer: int
    answer: str

    spy_guess: Location | None

    player_votes: list[int | None]
    indicted: int | None

    def __init__(self, game: Game):
        self.game = game

    async def play(self):
        game = self.game
        questioner = self.questioner = game.questioner

        # reset token counter for each player
        for nlp in game.player_nlps:
            nlp.reset_token_counter()

        # ask question
        answerer, question = await game.players[questioner].ask_question()
        assert 1 <= answerer < game.n_players and isinstance(question, str)
        answerer = game.reverse_pov(answerer, pov=questioner)

        # if the questioner selected the last questioner, select a random player
        if game.last_questioner != -1 and answerer == game.last_questioner:
            print("questioner selected last questioner, selecting random player")
            answerer = random.choice(
                [
                    i
                    for i in range(game.n_players)
                    if i != questioner and i != game.last_questioner
                ]
            )

        # answer question
        answer = await game.players[answerer].answer_question(question)
        assert isinstance(answer, str)

        # send question and answer to all players
        futures = []
        for player in range(game.n_players):
            q = game.add_pov(questioner, pov=player)
            a = game.add_pov(answerer, pov=player)
            futures.append(
                game.players[player].analyze_response(q, question, a, answer)
            )
        await asyncio.gather(*futures)

        self.question = question
        self.answer = answer
        game.last_questioner = questioner
        game.questioner = self.answerer = answerer

        # spy voting
        guess = self.spy_guess = await game.players[game.spy].guess_location()
        assert guess is None or isinstance(guess, Location)
        if guess == game.location:
            game.game_state = GameState.SPY_GUESSED_RIGHT
            return
        elif guess != None:
            game.game_state = GameState.SPY_GUESSED_WRONG
            return

        # collect votes
        votes = await asyncio.gather(
            *[player.accuse_player() for player in game.players]
        )
        assert all(1 <= vote < game.n_players for vote in votes if vote is not None)
        for i, vote in enumerate(votes):
            if vote is not None:
                votes[i] = game.reverse_pov(vote, pov=i)

        self.player_votes = votes

        # count votes
        indicted = self.indicted = count_votes(votes, game.n_players)
        if indicted == game.spy:
            game.game_state = GameState.SPY_INDICTED
            return
        elif indicted is not None:
            game.game_state = GameState.NON_SPY_INDICTED
            return

        # send votes to players
        futures = []
        for i in range(game.n_players):
            votes_pov = [None] * game.n_players
            for voter, votee in enumerate(votes):
                if votee is None:
                    continue
                voter = game.add_pov(voter, pov=i)
                votee = game.add_pov(votee, pov=i)
                votes_pov[voter] = votee
            futures.append(game.players[i].analyze_voting(votes_pov))
        await asyncio.gather(*futures)

    @lru_cache
    def get_conversation(self) -> list[tuple[int, str]]:
        """returns the conversation as a list of tuples of player index and their message"""
        game = self.game
        output = []

        # question and answer
        output.append((self.questioner, f"Player {self.answerer + 1}, {self.question}"))
        output.append((self.answerer, self.answer))

        # spy guess
        if self.spy_guess is not None:
            # spy: I am the spy. Was it the {location}?
            msg = random.choice(SPY_REVEAL_AND_GUESS).format(
                location=self.spy_guess.value
            )
            output.append((game.spy, msg))
            responder = random.choice(list(set(range(game.n_players)) - {game.spy}))
            if game.game_state == GameState.SPY_GUESSED_RIGHT:
                # random nonspy: yes that is right
                msg = random.choice(SPY_GUESS_RIGHT_RESPONSE)
            else:
                # random nonspy: no, it was the {location}
                msg = random.choice(SPY_GUESS_WRONG_RESPONSE).format(
                    location=game.location.value
                )
            output.append((responder, msg))

        # indictment
        elif self.indicted is not None:
            # one of the accusers: "I think it's player {spy} are you the spy?"
            accuser = random.choice(
                [i for i, x in enumerate(self.player_votes) if x == self.indicted]
            )
            msg = random.choice(ACCUSATION).format(spy=self.indicted + 1)
            output.append((accuser, msg))
            if game.game_state == GameState.SPY_INDICTED:
                # spy: I am the spy
                msg = random.choice(SPY_INDICTED_RESPONSE)
                output.append((game.spy, msg))
            else:
                # indicted: No, I am not the spy
                msg = random.choice(NON_SPY_INDICTED_RESPONSE)
                output.append((self.indicted, msg))
                # spy: I am the spy
                msg = random.choice(SPY_REVEAL)
                output.append((game.spy, msg))

        return output

    def pregenerate_audio(self):
        """pre-generates audio for the game"""
        random.seed(42)
        voices = random.sample(VOICES, self.game.n_players)
        pitch_shifts = random.sample(PITCH_SHIFTS, self.game.n_players)
        # list of (player, audio, sr)
        self.audio: list[int, np.ndarray, int] = []
        for player, message in self.get_conversation():
            voice = voices[player]
            ps = pitch_shifts[player]
            audio, sr = text_to_speech(message, voice, ps)
            self.audio.append((player, audio, sr))

    def render(self):
        # render the round
        self.audio, "need to pregenerate audio"
        # game = self.game
        # print(game.window)
        for voice in self.audio:
            _, audio, _ = voice
            sound = pygame.sndarray.make_sound(audio)
            sound.play()
            pygame.time.wait(int(sound.get_length() * 1000))

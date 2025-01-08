<img src="media/readme_image.jpg" alt="spyfall" width="250" align="right" style="padding: 50px;"/>

# NLP Spyfall Agent

This is the repository for the 2025 UT Austin MLDS Long Competition MEH team.
Agents is used to compete in a game of spyfall, and it can use LLMs and embedding models to ask/answer questions and determine the spy/location.

## Game Rules

**<u>Setup</u>**: Each game is played with 4-12 players. All players are given the location except for the spy.

**<u>Spy Objective</u>**: The spy must figure out the location without revealing their identity.

**<u>Non-Spy Objective</u>**: Players must figure out who the spy is.

**<u>Gameplay</u>**: Each game consists of a fixed number of rounds. In each round, the following happens:

1. **<u>Questioning</u>**: A random player starts by asking another player a question about the location. The player who answers the question will be the one to ask the question in the next round. You can ask a question to any player except the player who asked you a question the previous round.

   Ex: A --> B --> A is not allowed.
2. **<u>Questioning Analysis</u>**: All players are given time to analyze the question/answer.
3. **<u>Guessing</u>**: The spy may guess the location. This will end the game.
4. **<u>Accusation</u>**: Players may accuse another player of being the spy. Successfully indicting a player will end the game. For a player to be indicted, the following conditions must be met:
   * A majority of the players must accuse *any* player.
   * One player must get a plurality of the votes. If a tie occurs, nothing happens.

    Ex: If 2 players accuse player A, 1 player accuses player B, 1 player accuses player C, and 3 players do not vote, player A is successfully indicted.

5. **<u>Voting Analysis</u>**: Players can see who voted for who and are given time to analyze the votes.

**<u>The game ends when</u>**:

* The spy guesses the location.
* A player is indicted of being the spy.
* All rounds are completed.

**<u>Scoring</u>**:

* **<u>Spy Victory</u>**: The spy earns 2 points if no one is indicted of being the spy, 4 points if a non-spy player is indicted of being the spy, and 4 points if the spy stops the game and successfully guesses the location.
* **<u>Non-Spy Victory</u>**: Each non-spy player earns 1 point.

## Getting Started

**<u>Setting up the Code</u>**:

To run this project locally with conda, run the following commands. This may take a while.

``` bash
conda create -n long_comp python==3.10.13
conda activate long_comp
pip install -r requirements.txt
```

If you do not have conda installed, you can install it [here](https://docs.anaconda.com/miniconda/). Additionally, if you use VSCode, the VSCode Extension `Python Environment Manager` by Don Jayamanne is nice for managing and selecting default conda environments.

If you use additional packages, please add them to `requirements.txt`.

*Note: this repository is thoroughly type-annotated. To catch type errors, you can install the VSCode Extension `Mypy Type Checker` by Microsoft.*

**<u>Setting up Your LLM API Key</u>**:

We will be using [together.ai](https://api.together.ai) which offers a $5 credit (~50M tokens) for new users, no credit card required.

To get your API key, click the link above to create an account. A pop-up will appear with your API key.

Next, create a file named `.env` in the root directory with the following text:

``` text
TOGETHER_API_KEY = <your_together_api_key>
```

**<u>Running Games/Simulations</u>**:

See `main.py` for an example of how to run games and simulations.

*Note: by default, a dummy llm and embedding model are selected. You can change this at the top of `main.py`.*

## Contributors

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/mpianrhko">
        <img src="https://github.com/mpianrhko.png" width="100px;" alt="mpianrhko"/>
        <br />
        <sub><b>mpianrhko</b></sub>
      </a>
    </td>
    <td align="center">
      <a href="https://github.com/hqrris0n">
        <img src="https://github.com/hqrris0n.png" width="100px;" alt="hqrris0n"/>
        <br />
        <sub><b>hqrris0n</b></sub>
      </a>
    </td>
    <td align="center">
      <a href="https://github.com/123jklas">
        <img src="https://github.com/123jklas.png" width="100px;" alt="123jklas"/>
        <br />
        <sub><b>123jklas</b></sub>
      </a>
    </td>
  </tr>
</table>

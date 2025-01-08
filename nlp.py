import asyncio
import math
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import torch
from dotenv import load_dotenv
from together import AsyncTogether  # type: ignore
from transformers import (  # type: ignore
    AutoModelForSequenceClassification,
    AutoTokenizer,
)

from util import rate_limit

os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Please set your API key in the .env file: "TOGETHER_API_KEY=<your-api-key>"
load_dotenv()

if "TOGETHER_API_KEY" in os.environ:
    try:
        client = AsyncTogether()
    except Exception as e:
        client = e
else:
    client = Exception("Please set your API key in the .env file")


class LLMRole(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


# Interfaces ####################################################################


class LLMTokenizer(ABC):
    @abstractmethod
    def count_tokens(self, text_or_prompt: str | list[tuple[LLMRole, str]]) -> int:
        pass


class LLM(ABC):
    @abstractmethod
    async def prompt(
        self,
        prompt: list[tuple[LLMRole, str]],
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> str:
        pass


class EmbeddingTokenizer(ABC):
    @abstractmethod
    def count_tokens(self, text: str | list[str]) -> int:
        pass


class Embedding(ABC):
    @abstractmethod
    async def get_embeddings(
        self, text: str | list[str]
    ) -> np.ndarray | list[np.ndarray]:
        pass


# Implementations ####################################################################


class LlamaTokenizer(LLMTokenizer):
    def __init__(self):
        # Use NousResearch bc it doesn't have retricted access
        self.tokenizer = AutoTokenizer.from_pretrained(
            "NousResearch/Meta-Llama-3-70B-Instruct"
        )

    def count_tokens(self, text_or_prompt: str | list[tuple[LLMRole, str]]) -> int:
        if isinstance(text_or_prompt, str):
            tokens = self.tokenizer(text_or_prompt).encodings[0].tokens
            return len(tokens)
        else:
            tokens = [
                self.tokenizer(content).encodings[0].tokens
                for role, content in text_or_prompt
            ]
            return sum(len(token) for token in tokens)


class Llama(LLM):
    def __init__(self):
        super().__init__()
        if isinstance(client, Exception):
            raise client

    @rate_limit(requests_per_second=1)
    async def prompt(
        self,
        prompt: list[tuple[LLMRole, str]],
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> str:
        messages = [
            {"role": role.value, "content": content} for role, content in prompt
        ]
        response = await client.chat.completions.create(
            model="meta-llama/Meta-Llama-3-70B-Instruct-Lite",
            messages=messages,
            max_tokens=max_output_tokens,
            temperature=temperature,
            top_p=0.7,
            top_k=50,
            repetition_penalty=1,
            stop=["<|eot_id|>"],
            stream=False,
        )
        return response.choices[0].message.content


class DummyLLM(LLM):
    """A dummy LLM for testing"""

    async def prompt(
        self,
        prompt: list[tuple[LLMRole, str]],
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> str:
        return "Output from the LLM will be here"


class BERTTokenizer(EmbeddingTokenizer):
    def __init__(self):
        self.tokenizer = AutoTokenizer.from_pretrained(
            "bert-base-uncased", clean_up_tokenization_spaces=True
        )

    def count_tokens(self, text: str | list[str]) -> int:
        text = [text] if isinstance(text, str) else text
        token_list = [self.tokenizer(t).encodings[0].tokens for t in text]
        return sum(len(tokens) for tokens in token_list)


class BERTTogether(Embedding):
    def __init__(self):
        super().__init__()
        if isinstance(client, Exception):
            raise client

    @rate_limit(requests_per_second=50)
    async def get_embeddings(
        self, text: str | list[str]
    ) -> np.ndarray | list[np.ndarray]:
        """returns a 768-dimensional embedding"""
        response = await client.embeddings.create(
            model="togethercomputer/m2-bert-80M-2k-retrieval",
            input=text,
        )
        embeddings = [
            np.array(response.data[i].embedding) for i in range(len(response.data))
        ]
        if isinstance(text, str):
            return embeddings[0]
        return embeddings


class BERTLocal(Embedding):
    def __init__(
        self,
        device: str = "cpu",
        batch_size: int = 8,
    ):
        self.device = device
        self.batch_size = batch_size

        self.tokenizer = AutoTokenizer.from_pretrained(
            "bert-base-uncased",
            clean_up_tokenization_spaces=True,
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            "togethercomputer/m2-bert-80M-2k-retrieval", trust_remote_code=True
        ).to(device)
        self.model.eval()

    async def model_loop(self):
        """Use a loop to optimize with batching"""
        while True:
            # Once an item is added to the queue, build as large of a batch as possible (up to batch_size) and process it
            dequeued = [await self.queue.get()]
            for _ in range(self.batch_size - 1):
                try:
                    dequeued.append(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # print("batch size", len(dequeued))
            inputs, futures = zip(*dequeued)

            # tokenize and pad to the longest sequence in the batch
            input_ids = self.tokenizer(
                inputs,
                return_tensors="pt",
                padding="longest",
                return_token_type_ids=False,
            ).to(self.device)

            # run model
            with torch.no_grad():
                outputs = self.model(**input_ids)

            embeddings = outputs["sentence_embedding"].detach().cpu().numpy()
            for future, embedding in zip(futures, embeddings):
                future.set_result(embedding)

    async def get_embeddings(
        self, text: str | list[str]
    ) -> np.ndarray | list[np.ndarray]:
        """This adds the text to the queue which will be processed in the model_loop
        the model_loop will then set the future with the embedding"""
        if not hasattr(self, "model_loop_task") or self.model_loop_task.cancelled():  # type: ignore
            self.queue: asyncio.Queue[tuple[str, asyncio.Future]] = asyncio.Queue()
            self.model_loop_task = asyncio.create_task(self.model_loop())
        text_list = [text] if isinstance(text, str) else text
        futures = []
        for t in text_list:
            future = asyncio.get_event_loop().create_future()
            self.queue.put_nowait((t, future))
            futures.append(future)
        embeddings = await asyncio.gather(*futures)
        if isinstance(text, str):
            return embeddings[0]
        return embeddings


class DummyEmbedding(Embedding):
    async def get_embeddings(
        self, text: str | list[str]
    ) -> np.ndarray | list[np.ndarray]:
        if isinstance(text, str):
            return np.zeros(768)
        return [np.zeros(768) for _ in text]


@dataclass
class NLP:
    """Used for the single, main NLP instance in the runtime"""

    llm_tokenizer: LLMTokenizer = field(default_factory=LlamaTokenizer)
    llm: LLM = field(default_factory=DummyLLM)
    embedding_tokenizer: EmbeddingTokenizer = field(default_factory=BERTTokenizer)
    embedding: Embedding = field(default_factory=DummyEmbedding)

    async def prompt_llm(
        self,
        prompt: list[tuple[LLMRole, str]],
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> str:
        return await self.llm.prompt(prompt, max_output_tokens, temperature)

    async def get_embeddings(
        self, text: str | list[str]
    ) -> np.ndarray | list[np.ndarray]:
        return await self.embedding.get_embeddings(text)

    def count_llm_tokens(self, text_or_prompt: str | list[tuple[LLMRole, str]]) -> int:
        return self.llm_tokenizer.count_tokens(text_or_prompt)

    def count_embedding_tokens(self, text: str | list[str]) -> int:
        return self.embedding_tokenizer.count_tokens(text)


@dataclass
class TokenCounterWrapper:
    """Used for NLP instances that need to keep track of token usage"""

    nlp: NLP = field(default_factory=NLP)
    token_limit: int = 1000

    def __post_init__(self):
        self.reset_token_counter()

    def reset_token_counter(self):
        self.remaining_tokens = self.token_limit

    async def prompt_llm(
        self,
        prompt: list[tuple[LLMRole, str]],
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> str:
        if self.remaining_tokens is not None:
            self.remaining_tokens -= self.nlp.count_llm_tokens(prompt)
            if self.remaining_tokens <= 0:
                return ""

            if max_output_tokens is None:
                max_output_tokens = self.remaining_tokens
            else:
                max_output_tokens = min(max_output_tokens, self.remaining_tokens)

            output = await self.nlp.prompt_llm(prompt, max_output_tokens, temperature)
            self.remaining_tokens -= self.nlp.count_llm_tokens(output)
        return output

    async def get_embeddings(
        self, text: str | list[str]
    ) -> np.ndarray | list[np.ndarray]:
        self.remaining_tokens -= math.ceil(self.nlp.count_embedding_tokens(text) / 10)
        if self.remaining_tokens < 0:
            if isinstance(text, str):
                return np.zeros(768)
            return [np.zeros(768) for _ in text]

        return await self.nlp.get_embeddings(text)

    def count_llm_tokens(self, text_or_prompt: str | list[tuple[LLMRole, str]]) -> int:
        return self.nlp.count_llm_tokens(text_or_prompt)

    def count_embedding_tokens(self, text: str | list[str]) -> int:
        return self.nlp.count_embedding_tokens(text)


class NLPProxy:
    """The wrapper that agents will use to interact with the LLM"""

    def __init__(self, token_counter: TokenCounterWrapper | None = None):
        if token_counter is None:
            token_counter = TokenCounterWrapper()
        self.__token_counter = token_counter

    async def prompt_llm(
        self,
        prompt: list[tuple[LLMRole, str]],
        max_output_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> str:
        """prompts the LLM with a given prompt and returns the output

        Args:
            prompt (list[tuple[LLMRole, str]]): List of tuples containing the role and text.
                Use LLMRole.SYSTEM to give the llm background information and LLMRole.USER for user input.
                LLMRole.ASSISTANT can be used to give the llm an example of how to respond.

            max_output_tokens (int | None, optional): The maximum number of tokens to output or None for no limit

            temperature (float, optional): The temperature to use for the llm. A higher temperature produces more varied results. Defaults to 0.7.

        Returns:
            str: llm output
        """
        return await self.__token_counter.prompt_llm(
            prompt, max_output_tokens, temperature
        )

    async def get_embeddings(
        self, text: str | list[str]
    ) -> np.ndarray | list[np.ndarray]:
        """Gets a 768-dimensional embedding for the given text

        Args:
            text (str): Input text. This will counted against the token limit but at a lesser extent than the llm.
                Every 10 tokens inputted into the embedding model is equivalent to 1 token inputted into the llm.

        Returns:
            np.ndarray: 768-dimensional embedding
        """
        return await self.__token_counter.get_embeddings(text)

    def count_llm_tokens(self, text_or_prompt: str | list[tuple[LLMRole, str]]) -> int:
        """Used to count the number of tokens that would be used by the llm. This can help agents manage their token usage.

        Args:
            text_or_prompt (str | list[tuple[LLMRole, str]]): Input text as a string or a list of tuples containing the role and text

        Returns:
            int: Number of tokens
        """
        return self.__token_counter.count_llm_tokens(text_or_prompt)

    def count_embedding_tokens(self, text: str | list[str]) -> int:
        """Equivalent to count_llm_tokens but for the embedding model"""
        return self.__token_counter.count_embedding_tokens(text)

    def get_remaining_tokens(self) -> int:
        """
        Returns:
            int: the number of tokens remaining for your agent for the round
        """
        return self.__token_counter.remaining_tokens


if __name__ == "__main__":
    pass
    event_loop = asyncio.get_event_loop()
    # llm_tokenizer = LlamaTokenizer()
    # tokens = llm_tokenizer.count_tokens("How many US states are there?")
    # print(tokens)

    # llm = Llama()
    # prompt = [(LLMRole.USER, "How many US states are there?")]
    # output = event_loop.run_until_complete(llm.prompt(prompt, 100))
    # print(output)

    # bert_tokenizer = BERTTokenizer()
    # tokens = bert_tokenizer.count_tokens("How many US states are there?")
    # print(tokens)

    # tokens = bert_tokenizer.count_tokens(["How many", "US states are there?"])
    # print(tokens)

    # bert = BERTTogether()
    # output = event_loop.run_until_complete(bert.get_embeddings("How many US states are there?"))
    # print(type(output))
    # print(len(output))

    # output = event_loop.run_until_complete(bert.get_embeddings(["How many", "US states are there?"]))
    # print(type(output))
    # print(len(output))

    # bert = BERTLocal()
    # output = event_loop.run_until_complete(
    #     bert.get_embeddings("How many US states are there?")
    # )
    # print(type(output))
    # print(len(output))

    # output = event_loop.run_until_complete(
    #     bert.get_embeddings(["How many", "US states are there?"])
    # )
    # print(type(output))
    # print(len(output))

    # bert = DummyEmbedding()
    # output = event_loop.run_until_complete(
    #     bert.get_embeddings("How many US states are there?")
    # )
    # print(type(output))
    # print(len(output))

    # output = event_loop.run_until_complete(
    #     bert.get_embeddings(["How many", "US states are there?"])
    # )
    # print(type(output))
    # print(len(output))

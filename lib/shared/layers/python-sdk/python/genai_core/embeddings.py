import json
import os
import random
import time
from typing import Optional

import botocore
import numpy as np
from aws_lambda_powertools import Logger

import genai_core.clients
import genai_core.parameters
from genai_core.model_providers import get_model_provider
from genai_core.types import CommonError, Task
from genai_core.types import EmbeddingsModel, Provider

SAGEMAKER_RAG_MODELS_ENDPOINT = os.environ.get("SAGEMAKER_RAG_MODELS_ENDPOINT")
logger = Logger()


def generate_embeddings(
    model: EmbeddingsModel, input: list[str], task: str = "store", batch_size: int = 50
) -> list[list[float]]:
    input = [x[:10000] for x in input]

    ret_value = []
    batch_split = [input[i : i + batch_size] for i in range(0, len(input), batch_size)]

    for batch in batch_split:
        if model.provider == Provider.OPENAI.value:
            ret_value.extend(_generate_embeddings_openai(model, batch))
        elif model.provider == Provider.BEDROCK.value:
            ret_value.extend(_generate_embeddings_bedrock(model, batch, task))
        elif model.provider == Provider.SAGEMAKER.value:
            ret_value.extend(_generate_embeddings_sagemaker(model, batch))
        else:
            raise CommonError(f"Unknown provider: {model.provider}")

    return ret_value


def get_embeddings_models():
    return get_model_provider().get_embedding_models()


def get_embeddings_model(provider: Provider, name: str) -> Optional[EmbeddingsModel]:
    return get_model_provider().get_embeddings_model(provider, name)


def _generate_embeddings_openai(model: EmbeddingsModel, input: list[str]):
    openai = genai_core.clients.get_openai_client()

    if not openai:
        raise CommonError("OpenAI API is not available. Please set OPENAI_API_KEY.")

    data = openai.embeddings.create(input=input, model=model.name).data
    ret_value = [x.embedding for x in data]

    return ret_value


def _generate_embeddings_bedrock(model: EmbeddingsModel, input: list[str], task: Task):
    bedrock = genai_core.clients.get_bedrock_client()

    if not bedrock:
        raise CommonError("Bedrock is not enabled.")

    model_provider = model.name.split(".")[0]
    if model_provider == Provider.AMAZON.value:
        return _generate_embeddings_amazon(model, input, bedrock)
    elif model_provider == Provider.COHERE.value:
        return _generate_embeddings_cohere(model, input, task, bedrock)
    else:
        raise CommonError(f'Unknown embeddings provider "{model_provider}"')


def _generate_embeddings_amazon(model: EmbeddingsModel, input: list[str], bedrock):
    ret_value = []
    for value in input:
        body = json.dumps({"inputText": value})
        response = bedrock.invoke_model(
            body=body,
            modelId=model.name,
            accept="application/json",
            contentType="application/json",
        )
        response_body = json.loads(response.get("body").read())
        embedding = response_body.get("embedding")

        ret_value.append(embedding)

    ret_value = np.array(ret_value)
    ret_value = ret_value / np.linalg.norm(ret_value, axis=1, keepdims=True)
    ret_value = ret_value.tolist()
    return ret_value


def _generate_embeddings_cohere(
    model: EmbeddingsModel, input: list[str], task: Task, bedrock
):
    input_type = (
        Task.SEARCH_QUERY.value if task == Task.RETRIEVE else Task.SEARCH_DOCUMENT.value
    )
    body = json.dumps({"texts": input, "input_type": input_type})
    response = bedrock.invoke_model(
        body=body,
        modelId=model.name,
        accept="application/json",
        contentType="application/json",
    )
    response_body = json.loads(response.get("body").read())
    embeddings = response_body.get("embeddings")

    return embeddings


def _generate_embeddings_sagemaker(model: EmbeddingsModel, input: list[str]):
    client = genai_core.clients.get_sagemaker_client()

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.invoke_endpoint(
                EndpointName=SAGEMAKER_RAG_MODELS_ENDPOINT,
                ContentType="application/json",
                Body=json.dumps(
                    {"type": "embeddings", "model": model.name, "input": input}
                ),
            )

            ret_value = json.loads(response["Body"].read().decode())

            return ret_value
        except botocore.exceptions.ClientError as error:
            # Check if the error is due to a 500 server error
            error_code = error.response.get("Error", {}).get("Code")
            if (
                error_code == "ServiceUnavailableException"
                or error_code == "InternalServerError"
            ):
                logger.info(f"Attempt {attempt + 1} failed with a 500 error.")
                time.sleep(
                    random.uniform(
                        0.3, 1.5
                    )  # nosec B311 Random value not used for cyptographic purposes
                )
                continue
            else:
                # If the exception was due to another reason, raise it.
                raise error

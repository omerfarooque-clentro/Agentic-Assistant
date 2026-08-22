from agent.llm.prompts import QUERY_GENERATOR_PROMPT
from agent.llm.client import llm
from langchain_core.prompts import ChatPromptTemplate
import re
from typing import TypedDict


class ParsedRoutingQuery(TypedDict):
    type: str
    query: str

def _message_text(message):
    if isinstance(message.content, str):
        return message.content

    return str(message.content)

def generate_routing_query(messages) -> ParsedRoutingQuery:

    latest_message = messages[-1] if messages else None
    previous_messages = messages[-3:-1] 

    

    if not latest_message:
        return {"type": "SINGLE", "query": ""}

    prompt = ChatPromptTemplate.from_messages(
    [
        ("system", QUERY_GENERATOR_PROMPT),
        (
            "human",
            "previous messages:\n"
            "{previous_messages}\n\n"
            "Current user message:\n"
            "{latest_message}",
        ),
    ]
)

    previous_messages = "\n".join(_message_text(m) for m in previous_messages)
    current_message_text = _message_text(latest_message)

    formatted_prompt = prompt.format_prompt(
        previous_messages=previous_messages,
        latest_message=current_message_text
    ).to_messages()

    response = llm.invoke(formatted_prompt)
    raw_content = str(response.content).strip()

    # Parse key-value outputs like TYPE: MULTI and QUERY: <text>
    type_match = re.search(r"TYPE:\s*(MULTI|SINGLE)", raw_content, re.IGNORECASE)
    query_match = re.search(r"QUERY:\s*(.*)", raw_content, re.IGNORECASE | re.DOTALL)

    query_type = type_match.group(1).upper() if type_match else "SINGLE"
    extracted_query = query_match.group(1).strip() if query_match else raw_content

    return {"type": query_type, "query": extracted_query}



"""
test = generate_routing_query([
    HumanMessage(content="what's the weather today?"),
    AIMessage(content="Get today's weather information."),
    HumanMessage(content="okay what about the next 7 days? can I travel to office?"),
    AIMessage(content="The 7-day weather forecast suggests that heavy rain is expected for the next 7 days. It may not be advisable to travel to the office during this period."),
    HumanMessage(content="inform Ahmed on Slack about the weather forecast, i'll be working remotely"),
])

print(test)  # Expected output: "TYPE: MULTI\nQUERY: Inform Ahmed on Slack about the weather forecast and that I will be working remotely."

"""
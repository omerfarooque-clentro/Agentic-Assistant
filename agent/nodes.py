from langchain_groq import ChatGroq
import os
from langchain_core.messages import SystemMessage
from agent.nlp_router import nlp_decider
from agent.state import AgentState
from agent.tools import search_gmail, add_to_sheet, read_doc, send_email
from dotenv import load_dotenv
 

load_dotenv()
os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY")

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
)

safe_tools = [
    search_gmail,
    add_to_sheet,
    read_doc,
]

tools = [
    *safe_tools,
    send_email,
]

llm_with_tools = llm.bind_tools(tools)

SYSTEM_PROMPT = """
You are a professional personal operations assistant.

You have access to tools for Gmail, Google Sheets, and Google Docs.

Tool usage rules:

- Use `search_gmail` only when the user asks to search, read, or find emails.
- Use `read_doc` when the user asks to read, inspect, summarize, or extract information from a document.
- Use `add_to_sheet` when the user asks to add, record, or save information in a spreadsheet.
- Use `send_email` ONLY when the user explicitly asks you to send an email.
- Use this format when sending an email:

When composing an email:
- Use a clear greeting on its own line.
- Put the purpose/message in a separate paragraph.
- Put additional context in a separate paragraph when needed.
- Put the closing on its own line.
- Put the sender's name on the line below the closing.
- Keep emails as much natural, concise, and professional as possible.
- Do not write the entire email as one paragraph.

Example structure:

Greeting,

Purpose/message.

Additional context if needed.

Closing,
Name

For tasks requiring multiple tools, execute them in logical order.
For example:
1. Read the requested document.
2. Extract the requested information.
3. Add the extracted information to the spreadsheet.

When calling tools, provide valid arguments matching their schemas.

Do not call a tool unless it is necessary for the user's request.
"""


def nlp_node(state: AgentState):
    result = nlp_decider(state["messages"][-1].content)
    if result["confidence"] > 0.5:
        routing_status = "high_confidence"
    else:
        routing_status = "low_confidence"
         
    return {
        "intent": result["intent"],
        "confidence": result["confidence"],
        "routing_status": routing_status,
    }

def agent_node(state: AgentState):
    print(">>> ENTERING AGENT NODE")
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        *state["messages"],
    ]
    print(f"Messages sent to LLM: {[msg.content for msg in messages]}") 
    response = llm_with_tools.invoke(messages)
    print(f"LLM Response: {response}")
    return {
        "messages": [response]
    }


def should_continue(state: AgentState):
    last_message = state["messages"][-1]

    if not last_message.tool_calls:
        return "end"

    for tool_call in last_message.tool_calls:
        if tool_call["name"] == "send_email":
            return "email_approval"

    return "tools"

def after_tools(state):
    last_message = state["messages"][-1]

    if getattr(last_message, "name", None) == "send_email":
        return "end"

    return "agent"

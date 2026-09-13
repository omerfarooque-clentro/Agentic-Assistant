from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

# Fast, high-accuracy primary model for tool orchestration, reasoning, and rewriters
llm_google = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0,
)

# Optional fallback provider
llm_groq = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
)

llm_groq_fast = ChatGroq(
    model="llama-3.1-8b-instant",
    temperature=0,
)

# Resilient models with cross-provider fallbacks
llm = llm_google.with_fallbacks([llm_groq], exceptions_to_handle=(Exception,))
llm_fast = llm_google.with_fallbacks([llm_groq_fast], exceptions_to_handle=(Exception,))


def bind_tools_with_fallback(tools):
    """Bind tools to primary provider with fallback support."""
    return llm_google.bind_tools(tools).with_fallbacks(
        [llm_groq.bind_tools(tools)],
        exceptions_to_handle=(Exception,),
    )

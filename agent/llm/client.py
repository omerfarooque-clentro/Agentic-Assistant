from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

llm_groq = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
)

llm_google = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    temperature=0,
)

# Keep Groq as the normal provider and retry failed requests with Gemini.
llm = llm_groq.with_fallbacks([llm_google])


def bind_tools_with_fallback(tools):
    """Bind the same tools to each provider before composing its fallback."""
    return llm_groq.bind_tools(tools).with_fallbacks(
        [llm_google.bind_tools(tools)]
    )

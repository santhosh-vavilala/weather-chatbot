"""Start here to learn LangGraph. Each node reads state and returns updates."""
import re
from typing import Annotated, TypedDict
import httpx
from langchain_core.messages import AIMessage, AnyMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import InMemorySaver
import weather_api


class ChatState(TypedDict, total=False):
    # A reducer combines new messages with earlier messages instead of replacing them.
    messages: Annotated[list[AnyMessage], add_messages]
    city: str
    day: int
    awaiting_city: bool
    route: str
    place: dict | None
    forecast: dict | None
    error: str
    reply: str
    trace: list[str]


def understand(state: ChatState):
    """Tiny rule-based parser. Replace this node with an LLM call later."""
    text = str(state["messages"][-1].content).strip().rstrip("?!. ")
    lower = text.lower()
    update = {"trace": ["understand"], "error": "", "reply": "",
              "forecast": None, "place": None}
    if lower in {"hi", "hello", "hey", "help"}:
        return {**update, "route": "help"}
    # Deliberately support today/tomorrow only; avoid pretending to understand all dates.
    if re.search(r"\b(yesterday|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\d{4}-\d{2}-\d{2}", lower):
        return {**update, "route": "help"}
    day = 1 if "tomorrow" in lower else (state.get("day", 0) if state.get("awaiting_city") else 0)
    city = None
    match = re.search(r"\b(?:in|for)\s+(.+)$", text, re.I)
    if match:
        city = match.group(1)
    elif lower.startswith("what about "):
        candidate = text[11:]
        if candidate.lower() not in {"tomorrow", "today", "there"}:
            city = candidate
    elif re.fullmatch(r"[\wÀ-ž ,'-]{2,80}", text) and len(text.split()) <= 4:
        # Bare city names work, but ordinary questions must not become city names.
        if not re.search(r"\b(weather|rain|umbrella|temperature|today|tomorrow|there|should|what|how|tell|thanks|thank|you|me)\b", lower):
            city = text
    if city:
        city = re.sub(r"\b(today|tomorrow|please)\b", "", city, flags=re.I).strip(" ,")
        if city.lower() in {"there", "here"}:
            city = None
    relevant = city or re.search(r"\b(weather|rain|umbrella|temperature|tomorrow|today)\b", lower)
    if not relevant:
        return {**update, "route": "help"}
    # Reuse the previous city when a follow-up contains only 'tomorrow'.
    selected = city or state.get("city", "")
    return {**update, "city": selected, "day": day,
            "awaiting_city": not bool(selected),
            "route": "resolve_city" if selected else "ask_city"}


def ask_city(state: ChatState):
    return {"reply": "Which city? Type a city name, such as Hyderabad, India.",
            "awaiting_city": True, "trace": state["trace"] + ["ask_city"]}


def help_user(state: ChatState):
    return {"reply": "I can check today's or tomorrow's weather. Try 'Weather in Hyderabad, India' or a city name. After that, ask 'What about tomorrow?' or 'Should I carry an umbrella?'. This demo uses simple rules, so other questions are not supported.",
            "trace": state["trace"] + ["help"]}


async def resolve_city(state: ChatState):
    try:
        place = await weather_api.find_city(state["city"])
        return {"place": place, "awaiting_city": not bool(place),
                "city": state["city"] if place else "",
                "error": "" if place else "I couldn't find that city. Please try the city name with a country, such as Hyderabad, India.",
                "trace": state["trace"] + ["resolve_city"]}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"error": "The location service is unavailable. Please try again shortly.",
                "trace": state["trace"] + ["resolve_city"]}


async def get_weather(state: ChatState):
    try:
        data = await weather_api.fetch_forecast(state["place"])
        # Treat absent/null measurements as an error, never invent weather.
        daily = data["daily"]
        i = state["day"]
        for key in ("time", "temperature_2m_min", "temperature_2m_max", "precipitation_probability_max"):
            if daily[key][i] is None:
                raise ValueError("Missing forecast")
        return {"forecast": data, "trace": state["trace"] + ["get_weather"]}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return {"error": "The weather service did not return a usable forecast. Please try again shortly.",
                "trace": state["trace"] + ["get_weather"]}


def answer(state: ChatState):
    if state.get("error"):
        reply = state["error"]
    else:
        place, data, i = state["place"], state["forecast"], state["day"]
        daily = data["daily"]
        label = ", ".join(str(place[k]) for k in ("name", "admin1", "country") if place.get(k))
        probability = daily["precipitation_probability_max"][i]
        advice = "Carry an umbrella." if probability >= 40 else "An umbrella may not be necessary; conditions can change."
        reply = (f"{'Tomorrow' if i else 'Today'} in {label} ({daily['time'][i]}):\n"
                 f"Temperature: {daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]} °C.\n"
                 f"Maximum precipitation probability: {probability}%. {advice}\n"
                 f"Local timezone: {data.get('timezone', 'local')}. Source: Open-Meteo.\n"
                 "Umbrella advice uses a simple 40% threshold, not an AI prediction.")
    return {"reply": reply, "trace": state["trace"] + ["answer"]}


def remember_reply(state: ChatState):
    """This message is appended by add_messages; the checkpointer saves the state."""
    return {"messages": [AIMessage(content=state["reply"])],
            "trace": state["trace"] + ["remember_reply"]}


def build_graph():
    builder = StateGraph(ChatState)
    for name, function in [("understand", understand), ("ask_city", ask_city),
                           ("help", help_user), ("resolve_city", resolve_city),
                           ("get_weather", get_weather), ("answer", answer),
                           ("remember_reply", remember_reply)]:
        builder.add_node(name, function)
    builder.add_edge(START, "understand")
    builder.add_conditional_edges("understand", lambda s: s["route"],
        {name: name for name in ("help", "ask_city", "resolve_city")})
    builder.add_conditional_edges("resolve_city", lambda s: "answer" if s.get("error") else "get_weather",
                                  {"answer": "answer", "get_weather": "get_weather"})
    builder.add_edge("get_weather", "answer")
    for name in ("help", "ask_city", "answer"):
        builder.add_edge(name, "remember_reply")
    builder.add_edge("remember_reply", END)
    return builder.compile(checkpointer=InMemorySaver())

"""HTTP entry point: the API delegates each chat turn to LangGraph."""
import asyncio
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from graph import build_graph

app = FastAPI(title="Weather Chat · LangGraph POC")
graph = build_graph()
# Local POC only: threads, locks, and history live in one server process.
locks: dict[str, asyncio.Lock] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    thread_id: UUID | None = None


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(422, "Please enter a message.")
    thread_id = str(request.thread_id or uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    lock = locks.setdefault(thread_id, asyncio.Lock())
    # Prevent overlapping updates to the same conversation.
    async with lock:
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content=request.message.strip())]}, config=config)
    return {"reply": state["reply"], "thread_id": thread_id,
            "nodes_visited": state["trace"],
            "state": {"city": state.get("city", ""), "day": state.get("day", 0),
                      "message_count": len(state["messages"]),
                      "awaiting_city": state.get("awaiting_city", False)}}

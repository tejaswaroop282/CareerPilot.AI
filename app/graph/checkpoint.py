"""LangGraph checkpointer on MongoDB (spec 5/6). Attached to the parent graph so subgraph interrupts persist.

The saver uses a synchronous PyMongo client; its async methods run it in a thread pool.
"""
from functools import lru_cache

from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from app.config import get_settings


@lru_cache
def get_checkpointer() -> MongoDBSaver:
    s = get_settings()
    return MongoDBSaver(MongoClient(s.mongodb_uri), db_name=s.mongodb_db)

"""Shared integration-test configuration.

The project declares ``langgraph-checkpoint-sqlite`` as a runtime dependency,
so checkpoint recovery tests intentionally exercise its real SQLite saver.
"""

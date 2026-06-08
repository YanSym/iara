"""Agent Tool catalog — individual tool handler implementations.

Each module implements the business logic for one group of tools. Handlers
are pure functions (or async coroutines) that receive validated arguments
and return sanitized result dicts. They never call external services directly;
side-effecting tools emit ProviderCommands to the outbox.
"""

"""Talo backend. Loads a .env file from the working directory (or a parent) if python-dotenv is installed."""
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:  # optional: environment variables can be set the usual way
    pass

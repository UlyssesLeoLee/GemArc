"""``python -m gemarc_mcp`` starts the MCP server over stdio."""
from .server import main

if __name__ == "__main__":  # importing this module (docs tools, walk_packages) must not start a server
    main()

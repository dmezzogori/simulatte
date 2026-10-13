"""Small helpers shared by the uv_project example model."""


def scenario_servers(servers: int) -> int:
    """Clamp the server count to what the example shop supports."""
    return max(1, min(servers, 16))

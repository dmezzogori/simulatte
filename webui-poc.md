# Simulatte Web UI - Proof of Concept Implementation Plan

## Overview

Build a web-based visualization and analytics interface for Simulatte using FastAPI (backend) and Svelte + Threlte (frontend). Primary mode is **replay** (simulation runs fast, then replays visually), with optional real-time mode if feasible.

---

## Architecture Summary

```
User's main.py (Simulation class)
        ↓
CLI: `simulatte main.py`
        ↓
FastAPI Backend ←→ SQLite DB
        ↓
Svelte + Threlte Frontend (2.5D isometric visualization)
```

---

## Project Structure

```
src/simulatte/
├── web/                              # New subpackage
│   ├── __init__.py
│   ├── cli.py                        # CLI entry point
│   ├── app.py                        # FastAPI application
│   ├── simulation_bridge.py          # Wraps user's Simulation class
│   ├── snapshot.py                   # Snapshot capture logic
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py                 # REST endpoints
│   │   ├── websocket.py              # WebSocket for real-time (optional)
│   │   └── schemas.py                # Pydantic models
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py                 # Extended SQLite schema
│   │   └── queries.py                # DB helpers
│   └── frontend/                     # Svelte source
│       ├── package.json
│       ├── svelte.config.js
│       ├── vite.config.ts
│       ├── src/
│       │   ├── routes/
│       │   │   ├── +layout.svelte    # Theme provider, nav
│       │   │   ├── +page.svelte      # Visualization page
│       │   │   └── analytics/
│       │   │       └── +page.svelte  # Analytics page
│       │   └── lib/
│       │       ├── components/
│       │       │   ├── scene/        # Three.js/Threlte
│       │       │   │   ├── ShopFloorScene.svelte
│       │       │   │   ├── ServerMesh.svelte
│       │       │   │   ├── JobCube.svelte
│       │       │   │   └── PSPArea.svelte
│       │       │   ├── controls/
│       │       │   │   ├── PlaybackControls.svelte
│       │       │   │   └── TimelineSlider.svelte
│       │       │   ├── overlay/
│       │       │   │   ├── StatsOverlay.svelte
│       │       │   │   └── ConsolePanel.svelte
│       │       │   └── charts/
│       │       │       ├── WIPChart.svelte
│       │       │       ├── UtilizationChart.svelte
│       │       │       └── ThroughputChart.svelte
│       │       ├── stores/
│       │       │   ├── simulation.ts
│       │       │   ├── playback.ts
│       │       │   └── preferences.ts
│       │       ├── api/
│       │       │   └── client.ts
│       │       └── utils/
│       │           ├── colors.ts     # SKU hashing, urgency
│       │           ├── layout.ts     # Circular positioning
│       │           └── interpolation.ts
│       └── static/
```

---

## User Contract: Simulation Class

Users implement their simulation as a Python class:

```python
# main.py
from simulatte import Environment, ShopFloor, Server, ProductionJob
from simulatte.web import Simulation, SimulationComponents

class Simulation:
    def setup(self, env: Environment) -> SimulationComponents:
        """Configure and return simulation components."""
        self.env = env
        self.shopfloor = ShopFloor(env=env, collect_time_series=True)
        self.servers = tuple(
            Server(env=env, capacity=1, shopfloor=self.shopfloor, collect_time_series=True)
            for _ in range(6)
        )
        # ... job generation logic ...
        return SimulationComponents(
            shopfloor=self.shopfloor,
            servers=self.servers,
        )

    def run(self, until: float) -> None:
        """Run simulation to specified time."""
        self.env.run(until=until)
```

---

## CLI Usage

```bash
simulatte main.py [--port 8765] [--host 127.0.0.1] [--no-browser]
```

Behavior:
1. Load user's `Simulation` class from `main.py`
2. Start FastAPI server on specified port
3. Print URL to console
4. Open browser automatically (unless `--no-browser`)

---

## REST API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/simulation/info` | GET | Metadata (servers, SKUs) |
| `/api/simulation/run` | POST | Start single run (body: `{until, seed?}`) |
| `/api/simulation/run-multiple` | POST | Start multi-run (body: `{until, seeds}`) |
| `/api/simulation/status` | GET | Status and progress (0-100%) |
| `/api/simulation/stop` | POST | Abort running simulation |
| `/api/snapshots` | GET | List snapshots (pagination) |
| `/api/snapshots/{id}` | GET | Single snapshot |
| `/api/analytics/summary` | GET | Aggregate metrics |
| `/api/analytics/timeseries/{metric}` | GET | Time-series data |
| `/api/preferences` | GET/PUT | Theme preferences |

---

## Database Schema (extends existing)

```sql
-- New tables alongside existing log_events
CREATE TABLE simulation_runs (
    id INTEGER PRIMARY KEY,
    env_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    until_time REAL,
    seed INTEGER,
    status TEXT DEFAULT 'running',  -- running, completed, error
    error_message TEXT
);

CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES simulation_runs(id),
    sim_time REAL NOT NULL,
    state TEXT NOT NULL,  -- JSON blob
    UNIQUE(run_id, sim_time)
);

CREATE TABLE analytics_cache (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES simulation_runs(id),
    metric_name TEXT NOT NULL,
    metric_value REAL,
    metadata TEXT  -- JSON for complex metrics
);

CREATE TABLE user_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

---

## Snapshot Structure

Captured periodically (every N simulation time units):

```python
{
    "sim_time": 150.5,
    "servers": [
        {"id": 0, "queue_length": 3, "processing_job": "uuid-1", "utilization": 0.85},
        ...
    ],
    "jobs": [
        {
            "id": "uuid-1",
            "sku": "SKU-A",
            "location": "processing",  # psp | queue | processing | transit
            "server_id": 0,
            "queue_position": null,
            "urgency": 0.7,  # 0=normal, 1=critical
            "due_date": 200.0,
            "created_at": 100.0
        },
        ...
    ],
    "psp_jobs": ["uuid-5", "uuid-6"],
    "wip_total": 45.5,
    "wip_per_server": {0: 12.0, 1: 8.5, ...},
    "jobs_completed": 42
}
```

---

## Frontend Visualization

### Scene Layout
- **2.5D isometric** view with orthographic camera
- **Circular layout** for servers (computed based on server count)
- **PSP area** positioned to the left of the circle
- **Jobs** as colored cubes (color = hash(SKU), urgency = color shift to red)

### Playback Controls
- Play / Pause button
- Speed selector: 1x, 2x, 5x, 10x
- Timeline slider to seek through snapshots

### Overlays
- Hover on server → tooltip with queue length, utilization
- Console panel at bottom for errors/logs

---

## Analytics Page

### Charts (using ECharts)
- **WIP over time** (area chart, total + per-server lines)
- **Server utilization** (bar chart per server)
- **Throughput** (step chart, cumulative jobs completed)
- **Lateness distribution** (scatter: x=time, y=lateness, color=tardy/early)
- **Queue lengths** (line chart per server + PSP)

### Multi-run Mode
- Distribution histograms for metrics
- Summary table with min/max/mean/CI
- No visual replay (too many runs)

---

## Visual Design

- **Aesthetic**: Industrial + minimal
- **Themes**: Light/dark with system preference detection
- **Branding**: "Simulatte" in header/footer
- **Colors**:
  - SKU colors: `hsl(hash(sku) % 360, 70%, 50%)`
  - Urgency gradient: normal → yellow → orange → red

---

## Dependencies to Add

### Python (pyproject.toml)
```toml
[project.optional-dependencies]
web = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.0.0",
]

[project.scripts]
simulatte = "simulatte.web.cli:main"
```

### Frontend (package.json)
```json
{
  "dependencies": {
    "@threlte/core": "^7.0.0",
    "@threlte/extras": "^8.0.0",
    "three": "^0.160.0",
    "echarts": "^5.4.0"
  },
  "devDependencies": {
    "@sveltejs/kit": "^2.0.0",
    "svelte": "^4.2.0",
    "typescript": "^5.0.0",
    "vite": "^5.0.0"
  }
}
```

---

## Implementation Phases

### Phase 0: Setup
1. Create dedicated feature branch: `git checkout -b feature/web-ui`
2. All work done on this branch until ready for review/merge

### Phase 1: Foundation
1. Create `src/simulatte/web/` package structure
2. Implement `cli.py` with argument parsing and module loading
3. Create basic `app.py` FastAPI skeleton
4. Add `SimulationComponents` dataclass and protocol
5. Extend `pyproject.toml` with dependencies and entry point

**Files to create/modify:**
- `src/simulatte/web/__init__.py`
- `src/simulatte/web/cli.py`
- `src/simulatte/web/app.py`
- `src/simulatte/web/simulation_bridge.py`
- `pyproject.toml`

### Phase 2: Backend API
1. Implement database schema extensions (`db/models.py`)
2. Create snapshot capture mechanism (`snapshot.py`)
3. Implement REST API routes (`api/routes.py`, `api/schemas.py`)
4. Add progress tracking during simulation
5. Implement analytics queries

**Files to create:**
- `src/simulatte/web/db/models.py`
- `src/simulatte/web/db/queries.py`
- `src/simulatte/web/api/routes.py`
- `src/simulatte/web/api/schemas.py`
- `src/simulatte/web/snapshot.py`

### Phase 3: Frontend Setup
1. Scaffold SvelteKit + Threlte project in `frontend/`
2. Configure Vite for development and production build
3. Set up API client and stores
4. Implement theme system with light/dark modes

**Files to create:**
- `src/simulatte/web/frontend/` (entire directory)

### Phase 4: 3D Visualization
1. Implement `ShopFloorScene.svelte` with isometric camera
2. Create `ServerMesh.svelte` with queue visualization
3. Create `JobCube.svelte` with SKU colors and urgency
4. Create `PSPArea.svelte`
5. Implement circular layout algorithm
6. Add job movement animation (straight-line interpolation)

**Key components:**
- `frontend/src/lib/components/scene/*.svelte`
- `frontend/src/lib/utils/layout.ts`
- `frontend/src/lib/utils/colors.ts`

### Phase 5: Playback System
1. Implement `PlaybackControls.svelte`
2. Implement `TimelineSlider.svelte`
3. Create playback store with speed control
4. Implement snapshot interpolation for smooth animation

**Key components:**
- `frontend/src/lib/components/controls/*.svelte`
- `frontend/src/lib/stores/playback.ts`

### Phase 6: Analytics & Overlays
1. Create analytics page with ECharts
2. Implement `StatsOverlay.svelte` for hover tooltips
3. Implement `ConsolePanel.svelte` for errors
4. Add multi-run analytics support

**Key components:**
- `frontend/src/routes/analytics/+page.svelte`
- `frontend/src/lib/components/charts/*.svelte`
- `frontend/src/lib/components/overlay/*.svelte`

### Phase 7: Polish
1. Finalize theme styling
2. Add loading states and empty states
3. Add "Simulatte" branding
4. Test end-to-end workflow
5. Build production frontend and embed in Python package

---

## Critical Files to Modify

| File | Changes |
|------|---------|
| `pyproject.toml` | Add FastAPI deps, CLI entry point |
| `src/simulatte/__init__.py` | Export `Simulation`, `SimulationComponents` |
| `src/simulatte/logger.py` | May extend for visualization events (optional) |

---

## Out of Scope (Explicit)

- Warehouse/AGV visualization
- Multi-user support
- Cloud deployment
- Real-time mode (unless trivial to add)
- Drag-drop server positioning
- Video export

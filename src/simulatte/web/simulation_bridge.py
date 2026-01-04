"""Bridge between user's Simulation class and the web backend."""

from __future__ import annotations

import importlib.util
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from simulatte.environment import Environment
    from simulatte.psp import PreShopPool
    from simulatte.router import Router
    from simulatte.server import Server
    from simulatte.shopfloor import ShopFloor


@dataclass(frozen=True, slots=True)
class SimulationComponents:
    """Container for simulation components returned by setup().

    Attributes:
        shopfloor: The main ShopFloor orchestrator.
        servers: Tuple of Server instances in the simulation.
        psp: Optional Pre-Shop Pool for pull systems.
        router: Optional Router for stochastic job generation.
        server_names: Optional mapping of server index to display name.
        sku_colors: Optional mapping of SKU to hex color (overrides auto-generated).
    """

    shopfloor: ShopFloor
    servers: tuple[Server, ...]
    psp: PreShopPool | None = None
    router: Router | None = None
    server_names: dict[int, str] | None = field(default=None)
    sku_colors: dict[str, str] | None = field(default=None)


@runtime_checkable
class SimulationProtocol(Protocol):
    """Protocol defining the interface for user simulations.

    Users can implement this protocol to define their simulation.
    The web UI will call setup() to configure the simulation and
    run() to execute it.
    """

    def setup(self, env: Environment) -> SimulationComponents:
        """Configure the simulation components.

        This method is called once before each simulation run to create
        and configure all simulation components.

        Args:
            env: The simulation environment. Use this for logging,
                 creating servers, jobs, etc.

        Returns:
            SimulationComponents containing all created components.
            At minimum, must include shopfloor and servers.
        """
        ...

    def run(self, until: float) -> None:
        """Run the simulation until the specified time.

        Args:
            until: Simulation end time.
        """
        ...


class Simulation(ABC):
    """Base class for user simulations.

    Users can extend this class to define their simulation,
    or simply implement the SimulationProtocol directly.

    Example:
        class MySimulation(Simulation):
            def setup(self, env: Environment) -> SimulationComponents:
                self.env = env
                self.shopfloor = ShopFloor(env=env)
                self.servers = tuple(
                    Server(env=env, capacity=1, shopfloor=self.shopfloor)
                    for _ in range(6)
                )
                return SimulationComponents(
                    shopfloor=self.shopfloor,
                    servers=self.servers,
                )

            def run(self, until: float) -> None:
                self.env.run(until=until)
    """

    env: Environment
    shopfloor: ShopFloor
    servers: tuple[Server, ...]
    psp: PreShopPool | None
    router: Router | None

    @abstractmethod
    def setup(self, env: Environment) -> SimulationComponents:
        """Configure the simulation components.

        Override this method to create and configure your simulation.

        Args:
            env: The simulation environment.

        Returns:
            SimulationComponents with at least shopfloor and servers.
        """

    def run(self, until: float) -> None:
        """Run the simulation until the specified time.

        Override this method for custom run logic. The default
        implementation simply calls env.run(until=until).

        Args:
            until: Simulation end time.
        """
        self.env.run(until=until)


def load_simulation_class(module_path: Path) -> type[SimulationProtocol]:
    """Dynamically load user's Simulation class from a Python file.

    Args:
        module_path: Path to the Python file containing the Simulation class.

    Returns:
        The Simulation class from the module.

    Raises:
        FileNotFoundError: If the module file doesn't exist.
        ValueError: If the module doesn't define a 'Simulation' class.
        TypeError: If the Simulation class doesn't implement the protocol.
    """
    if not module_path.exists():
        raise FileNotFoundError(f"Module not found: {module_path}")

    # Add parent directory to path so relative imports work
    parent_dir = str(module_path.parent.absolute())
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    # Load the module
    spec = importlib.util.spec_from_file_location("user_simulation", module_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load module spec from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["user_simulation"] = module
    spec.loader.exec_module(module)

    # Get the Simulation class
    if not hasattr(module, "Simulation"):
        raise ValueError(
            f"Module {module_path} must define a 'Simulation' class.\n"
            "Example:\n"
            "    class Simulation:\n"
            "        def setup(self, env): ...\n"
            "        def run(self, until): ..."
        )

    simulation_class = module.Simulation

    # Validate the class has required methods
    if not callable(getattr(simulation_class, "setup", None)):
        raise TypeError("Simulation class must have a 'setup' method")
    if not callable(getattr(simulation_class, "run", None)):
        raise TypeError("Simulation class must have a 'run' method")

    return simulation_class

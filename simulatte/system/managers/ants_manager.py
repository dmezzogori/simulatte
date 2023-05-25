from __future__ import annotations

import csv
from statistics import mean
from typing import TYPE_CHECKING, Sequence

from IPython.display import Markdown, display
from tabulate import tabulate

from simulatte.system.policies import AntSelectionPolicy

if TYPE_CHECKING:
    from simulatte.ant import Ant


class AntsManager:
    def __init__(self, ants: Sequence[Ant], ant_selection_policy: AntSelectionPolicy) -> None:
        self.ants = ants
        self._ant_selection_policy = ant_selection_policy

    @property
    def feeding_ants(self) -> tuple[Ant]:
        return tuple(ant for ant in self.ants if ant.kind == "feeding")

    @property
    def replenishment_ants(self) -> tuple[Ant]:
        return tuple(ant for ant in self.ants if ant.kind == "replenishment")

    @property
    def input_ants(self) -> tuple[Ant]:
        return tuple(ant for ant in self.ants if ant.kind == "input")

    @property
    def output_ants(self) -> tuple[Ant]:
        return tuple(ant for ant in self.ants if ant.kind == "output")

    def get_best_ant(self, exceptions: Sequence[Ant] | None = None) -> Ant:
        return self._ant_selection_policy(ants=self.ants, exceptions=exceptions)

    def export_mission_logs_csv(self, path: str) -> None:
        with open(path, "w") as csvfile:
            fieldnames = [
                "ant_id",
                "start_timestamp",
                "start_location",
                "end_timestamp",
                "end_location",
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for ant in self.ants:
                for mission in ant.mission_logs:
                    writer.writerow(
                        {
                            "ant_id": ant.id,
                            "start_timestamp": mission.start_time,
                            "start_location": mission.start_location.name,
                            "end_timestamp": mission.end_time,
                            "end_location": mission.end_location.name,
                        }
                    )

    def summary(self) -> None:
        display(Markdown("## Ant fleet"))

        headers = [
            "ANT",
            "SAT\n[%]",
            "TOT WAIT\n[min]",
            "WAIT@AVSRS\nAVG [min]",
            "WAIT@FEED\nAVG [min]",
            "WAIT@STAG\nAVG [min]",
            "WAIT@INT\nAVG [min]",
            "WAIT@PICKING\nAVG [min]",
        ]
        table = []
        for ant in self.ants:
            saturation = f"{ant.saturation * 100:.2f}"
            total_waiting_time = f"{ant.waiting_time / 60:.2f}"
            waiting_at_avsrs = f"{(mean(ant.loading_waiting_times) if ant.loading_waiting_times else 0) / 60:.2f}"
            waiting_in_feeding_area = (
                f"{(mean(ant.feeding_area_waiting_times) if ant.feeding_area_waiting_times else 0) / 60:.2f}"
            )
            waiting_in_staging_area = (
                f"{(mean(ant.staging_area_waiting_times) if ant.staging_area_waiting_times else 0) / 60:.2f}"
            )
            waiting_in_internal_area = (
                f"{(mean(ant.unloading_waiting_times) if ant.unloading_waiting_times else 0) / 60:.2f}"
            )
            waiting_at_picking = f"{(mean(ant.picking_waiting_times) if ant.picking_waiting_times else 0) / 60:.2f}"

            table.append(
                [
                    ant.id,
                    saturation,
                    total_waiting_time,
                    waiting_at_avsrs,
                    waiting_in_feeding_area,
                    waiting_in_staging_area,
                    waiting_in_internal_area,
                    waiting_at_picking,
                ]
            )
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))

        headers = [
            "ANT",
            "SAT\n[%]",
            "TOT WAIT\n[min]",
            "WAIT@AVSRS\nTOT [min]",
            "WAIT@FEED\nTOT [min]",
            "WAIT@STAG\nTOT [min]",
            "WAIT@INT\nTOT [min]",
            "WAIT@PICKING\nTOT [min]",
            "SUM\n[min]",
        ]
        table = []
        for ant in self.ants:
            saturation = f"{ant.saturation * 100:.2f}%"
            total_waiting_time = f"{ant.waiting_time / 60:.2f}"
            waiting_at_avsrs = f"{sum(ant.loading_waiting_times) / 60:.2f}"
            waiting_in_feeding_area = f"{sum(ant.feeding_area_waiting_times) / 60:.2f}"
            waiting_in_staging_area = f"{sum(ant.staging_area_waiting_times) / 60:.2f}"
            waiting_in_internal_area = f"{sum(ant.unloading_waiting_times) / 60:.2f}"
            waiting_at_picking = f"{sum(ant.picking_waiting_times) / 60:.2f}"

            tot = (
                sum(ant.loading_waiting_times)
                + sum(ant.feeding_area_waiting_times)
                + sum(ant.staging_area_waiting_times)
                + sum(ant.unloading_waiting_times)
                + sum(ant.picking_waiting_times)
            )
            tot = f"{tot / 60:.2f}"

            table.append(
                [
                    ant.id,
                    saturation,
                    total_waiting_time,
                    waiting_at_avsrs,
                    waiting_in_feeding_area,
                    waiting_in_staging_area,
                    waiting_in_internal_area,
                    waiting_at_picking,
                    tot,
                ]
            )
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))

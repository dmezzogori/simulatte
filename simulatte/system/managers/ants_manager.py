from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from simulatte.system.policies import AntSelectionPolicy
import csv

if TYPE_CHECKING:
    from simulatte.ant import Ant


class AntsManager:
    def __init__(self, ants: Sequence[Ant], ant_selection_policy: AntSelectionPolicy) -> None:
        self.ants = ants
        self._ant_selection_policy = ant_selection_policy

    def get_best_ant(self) -> Ant:
        return self._ant_selection_policy(ants=self.ants)

    def export_mission_logs_csv(self, path: str) -> None:
        with open(path, "w") as csvfile:
            fieldnames = [
                'ant_id',
                'start_timestamp',
                'start_location',
                'end_timestamp',
                'end_location'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for ant in self.ants:
                for mission in ant.mission_logs:
                    writer.writerow({
                        'ant_id': ant.id,
                        'start_timestamp': mission.start_time,
                        'start_location': mission.start_location.name,
                        'end_timestamp': mission.end_time,
                        'end_location': mission.end_location.name
                    })

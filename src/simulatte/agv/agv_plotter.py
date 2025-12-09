from __future__ import annotations

from typing import TYPE_CHECKING

from matplotlib import pyplot as plt

if TYPE_CHECKING:
    from simulatte.agv import AGV


class AGVPlotter:
    def __init__(self, *, agv: AGV):
        self.agv = agv

    def plot_travel_time(self):
        """
        Plot the travel time history and distribution for an AGV.

        Plots a line chart of the AGV's trip travel times over time.
        Also plots a histogram of the travel time distribution.

        Uses:
            self.agv: The AGV instance to plot data for.
            self.agv.trips: The list of AGVTrip objects.

        Generates:
            Line chart with x-axis as trip number and y-axis as travel time.
            Histogram with x-axis as travel time and y-axis as frequency.
        """

        data = [trip.duration / 60 for trip in self.agv.trips]

        plt.plot(data)
        plt.title(f"AGV {self.agv.id} travel time history")
        plt.xlabel("Trips")
        plt.ylabel("Travel time (min)")

        plt.hist(data)
        plt.title(f"AGV {self.agv.id} travel time distribution")
        plt.xlabel("Travel time (min)")
        plt.ylabel("Frequency")

        plt.show()

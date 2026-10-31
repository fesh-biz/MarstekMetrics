"""Графіки на pyqtgraph."""

from __future__ import annotations

import math

import pyqtgraph as pg

from ..metrics import MAX_GAP_S, EnergySummary
from ..models import Telemetry
from . import theme

pg.setConfigOptions(antialias=True)


def _style_plot(w: pg.PlotWidget) -> None:
    c = theme.colors()
    w.setBackground(c["card"])
    w.showGrid(x=True, y=True, alpha=0.15)
    for name in ("left", "bottom"):
        ax = w.getAxis(name)
        ax.setPen(pg.mkPen(c["border"]))
        ax.setTextPen(pg.mkPen(c["muted"]))


SERIES = (
    ("house_power", "Споживання будинку", theme.HOUSE),
    ("grid_power", "Мережа (+ імпорт / − експорт)", theme.GRID),
    ("station_power", "Станція (+ віддає / − заряджання)", theme.STATION),
)


class PowerChart(pg.PlotWidget):
    """Потужності в часі. Розриви в даних не з'єднуються лінією."""

    def __init__(self) -> None:
        super().__init__(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        _style_plot(self)
        self.setLabel("left", "Вт")
        self.setMouseEnabled(x=True, y=False)
        self.addLegend(offset=(10, 5), labelTextColor=theme.colors()["muted"])
        self.addLine(y=0, pen=pg.mkPen(theme.colors()["border"], width=1))
        self.curves = {
            key: self.plot([], [], name=name, pen=pg.mkPen(color, width=2), connect="finite")
            for key, name, color in SERIES
        }

    def set_samples(self, samples: list[Telemetry], start: float, end: float) -> None:
        xs: list[float] = []
        ys: dict[str, list[float]] = {key: [] for key, _, _ in SERIES}
        prev_ts: float | None = None
        for t in samples:
            if prev_ts is not None and t.ts - prev_ts > MAX_GAP_S:
                xs.append((prev_ts + t.ts) / 2)
                for key in ys:
                    ys[key].append(math.nan)
            xs.append(t.ts)
            for key in ys:
                v = getattr(t, key)
                ys[key].append(math.nan if v is None else v)
            prev_ts = t.ts
        for key, curve in self.curves.items():
            curve.setData(xs, ys[key])
        self.setXRange(start, end, padding=0.01)
        self.enableAutoRange(axis="y")


class DailyBars(pg.PlotWidget):
    """Споживання за днями: споживання будинку та взяте з мережі, кВт·год."""

    def __init__(self) -> None:
        super().__init__()
        _style_plot(self)
        self.setLabel("left", "кВт·год")
        self.setMouseEnabled(x=False, y=False)
        self.legend = self.addLegend(offset=(10, 5), labelTextColor=theme.colors()["muted"])
        self.house = pg.BarGraphItem(x=[0], height=[0], width=0.38, brush=theme.HOUSE,
                                     pen=pg.mkPen(None), name="Споживання будинку")
        self.grid = pg.BarGraphItem(x=[0], height=[0], width=0.38, brush=theme.GRID,
                                    pen=pg.mkPen(None), name="Взято з мережі")
        self.addItem(self.house)
        self.addItem(self.grid)

    def set_days(self, days: list[EnergySummary]) -> None:
        n = len(days)
        self.house.setOpts(x=[i - 0.2 for i in range(n)], height=[d.house_wh / 1000 for d in days])
        self.grid.setOpts(x=[i + 0.2 for i in range(n)], height=[d.grid_import_wh / 1000 for d in days])
        step = 1 if n <= 14 else 2
        ticks = [(i, d.label) for i, d in enumerate(days) if (n - 1 - i) % step == 0]
        self.getAxis("bottom").setTicks([ticks, []])
        top = max([d.house_wh for d in days] + [d.grid_import_wh for d in days] + [100]) / 1000
        self.setXRange(-0.7, n - 0.3, padding=0)
        self.setYRange(0, top * 1.15, padding=0)

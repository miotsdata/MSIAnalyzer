"""
feature_overlay.py
Several features in one image, one color each, like a multi-channel
immunofluorescence composite (ADR 67).

Each channel is one feature: its per-pixel values are scaled to 0-1 by
its own contrast (`vmin`, `vmax`, `gamma`), multiplied by its color, and
the channels are combined pixel by pixel:

- `"add"` (default): summed and clipped, so overlaps mix (red + green
  shows yellow, all three primaries white). The order of the channels
  doesn't matter.
- `"max"`: each color component takes its brightest channel, crisper when
  many channels overlap.

Pixels outside the sample's grid are black, as in Visual Inspection.
`heatmap.render_feature_overlay` builds the per-channel grids and calls
`composite`; this module stays free of `heatmap` imports.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MODES = ("add", "max")

#: Upper percentile of a channel's values used as its automatic `vmax`, so
#: a few hotspot pixels don't make everything else look dark.
AUTO_VMAX_PERCENTILE = 99.5


@dataclass
class Channel:
    mz: float
    color: tuple[float, float, float]  # 0-1 RGB
    vmin: float | None = None  # None: automatic
    vmax: float | None = None
    gamma: float = 1.0


def parse_channels(field: str) -> list[Channel]:
    """The channels part of an `image://heatmap/<sample>|overlay|...` id:
    `mz:rrggbb:vmin:vmax:gamma` per channel, joined by commas, `auto` for
    an automatic bound. No `#` before the color: it would start the URL
    fragment."""
    channels = []
    for item in filter(None, field.split(",")):
        mz, color, vmin, vmax, gamma = item.split(":")
        rgb = tuple(int(color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        channels.append(Channel(
            mz=float(mz),
            color=rgb,
            vmin=None if vmin == "auto" else float(vmin),
            vmax=None if vmax == "auto" else float(vmax),
            gamma=float(gamma),
        ))
    return channels


def auto_range(values: np.ndarray) -> tuple[float, float]:
    """Automatic `(vmin, vmax)` for one channel: the smallest finite value,
    and the `AUTO_VMAX_PERCENTILE` percentile of the values above it (a
    sparse feature, mostly 0, would otherwise get `vmax == vmin`)."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    vmin = float(finite.min())
    above = finite[finite > vmin]
    if above.size == 0:
        return vmin, vmin + 1.0
    return vmin, float(np.percentile(above, AUTO_VMAX_PERCENTILE))


def _scaled(values: np.ndarray, channel: Channel) -> np.ndarray:
    auto_min, auto_max = auto_range(values)
    vmin = auto_min if channel.vmin is None else channel.vmin
    vmax = auto_max if channel.vmax is None else channel.vmax
    if vmax <= vmin:
        vmax = vmin + 1e-12
    scaled = np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)
    scaled = np.where(np.isfinite(scaled), scaled, 0.0)
    return scaled ** channel.gamma if channel.gamma != 1.0 else scaled


def composite(grids: list[np.ndarray], channels: list[Channel], mode: str = "add") -> np.ndarray:
    """Combine per-channel value grids (`n_y x n_x`, NaN outside the
    sample) into one RGBA `uint8` image.

    Args:
        grids: One per channel, same shape.
        channels: Their colors and contrast.
        mode: `"add"` or `"max"`.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not grids:
        raise ValueError("an overlay needs at least one channel")
    shape = grids[0].shape
    rgb = np.zeros(shape + (3,))
    inside = np.zeros(shape, dtype=bool)
    for grid, channel in zip(grids, channels):
        inside |= np.isfinite(grid)
        contribution = _scaled(grid, channel)[..., None] * np.asarray(channel.color)
        rgb = rgb + contribution if mode == "add" else np.maximum(rgb, contribution)
    rgba = np.zeros(shape + (4,), dtype=np.uint8)
    rgba[..., :3] = np.round(np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)
    rgba[..., 3] = 255
    rgba[~inside, :3] = 0
    return np.ascontiguousarray(rgba)


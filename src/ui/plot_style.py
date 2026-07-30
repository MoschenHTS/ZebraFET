import matplotlib.pyplot as plt

PLOT_FONT_FAMILY = 'Times New Roman'

# Times New Roman ships with Windows and macOS but not with most Linux
# distributions, where matplotlib would fall back to its own sans-serif default
# and emit a warning per figure — so the same project produced differently
# typeset figures depending on where it was analysed. The metric-compatible
# substitutes come first, then a serif that is always present.
PLOT_FONT_STACK = [
    PLOT_FONT_FAMILY,
    'Liberation Serif',
    'Nimbus Roman',
    'Tinos',
    'DejaVu Serif',
]

PLOT_PARAMS = {
    'font.family': 'serif',
    'font.serif': PLOT_FONT_STACK,
    'font.size': 11,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'axes.titleweight': 'bold',
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 9,
    'legend.frameon': True,
    'legend.edgecolor': '0.8',
    'axes.linewidth': 0.8,
    'xtick.major.width': 0.8,
    'ytick.major.width': 0.8,
    'lines.linewidth': 1.2,
    # 'figure.dpi' intentionally omitted -> matplotlib default (~100), screen-appropriate.
    # Export stays print-quality via the explicit dpi=600 passed at savefig() call sites.
    'savefig.dpi': 600,
    'savefig.bbox': 'tight',
    'axes.spines.top': False,
    'axes.spines.right': False,
}

# Publication color palette (ColorBrewer-derived, colorblind-safe, print-friendly)
PUB_COLORS = {
    'Control':          '#4d4d4d',   # dark grey
    'Solvent Control':  '#878787',   # mid grey
    'Substrate':        '#2166ac',   # deep blue
    'Positive Control': '#d73027',   # red
}

# Colorblind-safe, ordered palette extending the PUB_COLORS spirit to an
# arbitrary number of malformation categories.
MALFORMATION_PALETTE = [
    '#2166ac', '#d73027', '#1b9e77', '#e6ab02',
    '#7570b3', '#66a61e', '#a6761d', '#666666',
]


def apply_style():
    plt.rcParams.update(PLOT_PARAMS)


def style_axes_ticks(ax, rotate_xticks=False):
    """Rotate x-tick labels and anchor them so they don't collide with tick marks."""
    if rotate_xticks:
        ax.tick_params(axis='x', rotation=45)
        for label in ax.get_xticklabels():
            label.set_ha('right')
            label.set_rotation_mode('anchor')


def get_malformation_colors(n):
    if n <= len(MALFORMATION_PALETTE):
        return MALFORMATION_PALETTE[:n]
    import matplotlib
    return [matplotlib.colormaps['tab10'](i / max(n, 10)) for i in range(n)]

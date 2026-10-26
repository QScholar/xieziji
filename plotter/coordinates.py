"""Paper coordinates to output coordinates, with an explicit visual axis guide."""
import dataclasses


ORIGINS = {'top-left': '左上', 'top-right': '右上', 'bottom-left': '左下', 'bottom-right': '右下'}
PADDING = 32


@dataclasses.dataclass(frozen=True)
class Coordinates:
    width: float
    height: float
    reverse_x: bool = False
    reverse_y: bool = False
    swap_xy: bool = False
    origin: str = 'top-left'

    @classmethod
    def from_data(cls, data, width, height):
        settings = data.get('coordinate_system', {})
        if not isinstance(settings, dict):
            raise ValueError('坐标设置必须是对象')
        flags = {key: settings.get(key, False) for key in ('reverse_x', 'reverse_y', 'swap_xy')}
        if any(type(value) is not bool for value in flags.values()):
            raise ValueError('反转与对调选项必须是布尔值')
        origin = settings.get('origin', 'top-left')
        if not isinstance(origin, str) or origin not in ORIGINS:
            raise ValueError('起点位置必须是左上、右上、左下或右下')
        return cls(width, height, **flags, origin=origin)

    @property
    def paper_origin(self):
        return (self.width if self.origin.endswith('right') else 0.,
                self.height if self.origin.startswith('bottom') else 0.)

    def transform(self, x, y):
        ox, oy = self.paper_origin
        x, y = x - ox, y - oy
        if self.swap_xy:
            x, y = y, x
        return (round(-x if self.reverse_x else x, 3), round(-y if self.reverse_y else y, 3))

    @property
    def vectors(self):
        sx, sy = (-1 if self.reverse_x else 1), (-1 if self.reverse_y else 1)
        return ((0, sx), (sy, 0)) if self.swap_xy else ((sx, 0), (0, sy))

    def metadata(self):
        points = [self.transform(x, y) for x in (0, self.width) for y in (0, self.height)]
        labels = {(1, 0): '向右', (-1, 0): '向左', (0, 1): '向下', (0, -1): '向上'}
        return dict(paper=[self.width, self.height], coordinate_system=dict(reverse_x=self.reverse_x, reverse_y=self.reverse_y,
                                            swap_xy=self.swap_xy, origin=self.origin),
                    origin_label=ORIGINS[self.origin], paper_origin=list(self.paper_origin),
                    axis_directions=[labels[vector] for vector in self.vectors],
                    machine_paper=[self.height, self.width] if self.swap_xy else [self.width, self.height],
                    machine_page_bounds=[min(x for x, y in points), min(y for x, y in points),
                                         max(x for x, y in points), max(y for x, y in points)],
                    preview_size=[self.width + 2 * PADDING, self.height + 2 * PADDING])

    def svg_axes(self):
        ox, oy = self.paper_origin
        parts = ['<g id="axis-guide" font-family="sans-serif" font-size="7">', '<defs>']
        for axis, color in (('x', '#b15b3b'), ('y', '#397da2')):
            parts.append(f'<marker id="arrow-{axis}" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L6 3 L0 6 Z" fill="{color}"/></marker>')
        parts.append('</defs>')
        for axis, color, (dx, dy) in zip(('x', 'y'), ('#b15b3b', '#397da2'), self.vectors):
            parts.append(f'<line data-axis="{axis}" x1="{ox}" y1="{oy}" x2="{ox+dx*20}" y2="{oy+dy*20}" stroke="{color}" stroke-width=".8" marker-end="url(#arrow-{axis})"/>')
            tx, ty = ox + dx * 26 + (4 if dy else 0), oy + dy * 26 + (-2 if dx else 1.5)
            parts.append(f'<text x="{tx}" y="{ty}" text-anchor="middle" fill="{color}">{axis.upper()}+</text>')
        ix, iy = (-1 if ox else 1), (-1 if oy else 1)
        parts.append(f'<circle cx="{ox}" cy="{oy}" r="1.4" fill="#315b4e"/>')
        parts.append(f'<text x="{ox-ix*4}" y="{oy-iy*4}" text-anchor="middle" fill="#315b4e">0</text>')
        parts.append(f'<text x="{self.width/2}" y="{self.height+16}" text-anchor="middle" fill="#829084">{self.width:g} × {self.height:g} mm</text></g>')
        return '\n'.join(parts)


def paper_svg(coordinates, margin, paths=(), rows=None, pitch=None, size=5):
    width, height = coordinates.width, coordinates.height
    margins = margin if isinstance(margin, dict) else dict.fromkeys(('left', 'right', 'top', 'bottom'), margin)
    left, top = margins['left'], margins['top']
    right, bottom = width - margins['right'], height - margins['bottom']
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-PADDING} {-PADDING} {width+2*PADDING} {height+2*PADDING}" data-paper-width="{width}" data-paper-height="{height}" role="img" aria-label="纸张路径与 XY 坐标轴">',
             f'<rect x="{-PADDING}" y="{-PADDING}" width="{width+2*PADDING}" height="{height+2*PADDING}" fill="#f5f7f0"/>',
             f'<rect width="{width}" height="{height}" fill="white" stroke="#c3ced4" stroke-width=".4"/>',
             f'<rect id="writable-area" x="{left}" y="{top}" width="{right-left}" height="{bottom-top}" fill="none" stroke="#c3ced4" stroke-width=".3" stroke-dasharray="2 2"/>']
    if rows is not None:
        parts.append('<g id="row-guides" stroke="#dce5dc" stroke-width=".25" stroke-dasharray="1 2">')
        for row in range(rows):
            y = top + row * pitch + size
            parts.append(f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}"/>')
        parts.append('</g>')
    parts.append('<g id="writing-paths">')
    parts += ['<polyline points="' + ' '.join(f'{x},{y}' for x, y in stroke) + '" fill="none" stroke="#243e42" stroke-width=".22" stroke-linecap="round" stroke-linejoin="round"/>' for stroke in paths]
    parts += ['</g>', coordinates.svg_axes(), '</svg>']
    return '\n'.join(parts)

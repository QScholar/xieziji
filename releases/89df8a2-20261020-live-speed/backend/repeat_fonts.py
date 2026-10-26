"""Page-local glyph diversity; maximize the closest repeated-font distance in 2D."""
import collections
import heapq
import math
import random


def occurrences(paths):
    """Measure the final ink footprint, retaining formula/script transformations."""
    found = {}
    for stroke in paths:
        info = getattr(stroke, 'glyph', None)
        if not info or not stroke:
            continue
        key = info['occurrence']
        xs, ys = zip(*stroke)
        bounds = [min(xs), min(ys), max(xs), max(ys)]
        if key not in found:
            found[key] = dict(info, bounds=bounds)
        else:
            old = found[key]['bounds']
            found[key]['bounds'] = [min(old[0], bounds[0]), min(old[1], bounds[1]),
                                    max(old[2], bounds[2]), max(old[3], bounds[3])]
    for item in found.values():
        b = item['bounds']
        item['center'] = [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
    return list(found.values())


class Neighbors:
    """Small static k-d tree; no all-pairs matrix for long pages."""
    def __init__(self, points):
        self.points = points
        def build(indices, depth):
            if not indices:
                return None
            axis = depth % 2
            indices.sort(key=lambda i: (points[i][axis], i))
            middle = len(indices) // 2
            return indices[middle], axis, build(indices[:middle], depth + 1), build(indices[middle + 1:], depth + 1)
        self.root = build(list(range(len(points))), 0)

    def nearest(self, index, count):
        point, heap = self.points[index], []
        def visit(node):
            if node is None:
                return
            other, axis, left, right = node
            delta = point[axis] - self.points[other][axis]
            near, far = (left, right) if delta <= 0 else (right, left)
            visit(near)
            if other != index:
                distance = sum((a - b) ** 2 for a, b in zip(point, self.points[other]))
                if len(heap) < count:
                    heapq.heappush(heap, (-distance, -other))
                elif (distance, other) < (-heap[0][0], -heap[0][1]):
                    heapq.heapreplace(heap, (-distance, -other))
            if len(heap) < count or delta * delta < -heap[0][0]:
                visit(far)
        visit(self.root)
        return [(-distance, -other) for distance, other in sorted(heap, reverse=True)]


def separation(points, colors):
    groups = collections.defaultdict(list)
    for point, color in zip(points, colors):
        groups[color].append(point)
    distances = []
    for group in groups.values():
        if len(group) > 1:
            tree = Neighbors(group)
            distances.extend(tree.nearest(i, 1)[0][0] for i in range(len(group)))
    return (min(distances), sum(distances) / len(distances)) if distances else (math.inf, math.inf)


def _exact(points, count, initial):
    """Enumerate unlabeled partitions for small groups, with a maximin bound."""
    n = len(points)
    distances = [[sum((a - b) ** 2 for a, b in zip(p, q)) for q in points] for p in points]
    best, score = initial, separation(points, initial)
    groups, colors = [[] for _ in range(count)], []
    def visit(index, used, closest):
        nonlocal best, score
        if closest < score[0] - 1e-9 or used + n - index < count:
            return
        if index == n:
            candidate = separation(points, colors)
            if used == count and candidate > score:
                best, score = colors[:], candidate
            return
        for color in range(min(count, used + 1)):
            new_closest = min([closest] + [distances[index][j] for j in groups[color]])
            groups[color].append(index); colors.append(color)
            visit(index + 1, max(used, color + 1), new_closest)
            colors.pop(); groups[color].pop()
    visit(0, 0, math.inf)
    return best


def color_points(points, count, seed):
    n = len(points)
    if n <= count:
        return list(range(n))
    if count == 1:
        return [0] * n
    rng = random.Random(seed)
    if n <= 128:
        edges = [(sum((a - b) ** 2 for a, b in zip(points[i], points[j])), i, j)
                 for i in range(n) for j in range(i)]
    else:
        tree, pairs = Neighbors(points), {}
        for i in range(n):
            for distance, j in tree.nearest(i, min(n - 1, max(12, count * 2))):
                pairs[min(i, j), max(i, j)] = distance
        edges = [(distance, i, j) for (i, j), distance in pairs.items()]
    edges.sort()
    levels = sorted({distance for distance, i, j in edges})
    priorities = [rng.random() for _ in points]

    def color(threshold):
        adjacent = [set() for _ in points]
        for distance, i, j in edges:
            if distance >= threshold:
                break
            adjacent[i].add(j); adjacent[j].add(i)
        colors, seen = [-1] * n, [set() for _ in points]
        heap = [(0, -len(adjacent[i]), priorities[i], i) for i in range(n)]
        heapq.heapify(heap)
        while heap:
            saturation, degree, priority, i = heapq.heappop(heap)
            if colors[i] >= 0 or -saturation != len(seen[i]):
                continue
            available = next((c for c in range(count) if c not in seen[i]), None)
            if available is None:
                return None
            colors[i] = available
            for j in adjacent[i]:
                if colors[j] < 0 and available not in seen[j]:
                    seen[j].add(available)
                    heapq.heappush(heap, (-len(seen[j]), -len(adjacent[j]), priorities[j], j))
        # Using an additional font never reduces separation.
        for new in range(max(colors) + 1, count):
            sizes = collections.Counter(colors)
            old = max(sizes, key=lambda c: (sizes[c], -c))
            i = next(i for i, c in enumerate(colors) if c == old)
            colors[i] = new
        return colors

    best = [i % count for i in range(n)]
    score = separation(points, best)
    low, high = 0, len(levels)
    while low < high:
        middle = (low + high) // 2
        candidate = color(levels[middle])
        if candidate is None:
            high = middle
        else:
            candidate_score = separation(points, candidate)
            if candidate_score > score:
                best, score = candidate, candidate_score
            low = middle + 1
    return _exact(points, count, best) if n <= 10 and len({tuple(p) for p in points}) > 1 else best


def groups_on_page(paths):
    groups = collections.defaultdict(list)
    for item in occurrences(paths):
        if len(item['options']) > 1:
            groups[item['character'], tuple(item['options'])].append(item)
    return groups


def plan_pages(pages, weights, seed):
    plan = {}
    for page_number, page in enumerate(pages):
        for (char, options), group in groups_on_page(page[0]).items():
            if len(group) < 2:
                continue
            key = f'{seed}:page:{page_number}:char:{char}'
            colors = color_points([item['center'] for item in group], len(options), key)
            # Diversity and physical separation take priority. Weights select the
            # font labels for equally valid spatial groups, including unique uses.
            rng, remaining, labels = random.Random(key + ':labels'), list(options), {}
            for color, uses in collections.Counter(colors).most_common():
                selected = rng.choices(remaining, weights=[weights[font] for font in remaining])[0]
                labels[color] = selected; remaining.remove(selected)
            plan.update((item['occurrence'], labels[color]) for item, color in zip(group, colors))
    return plan


def quality(pages):
    missing, distances = 0, []
    for page in pages:
        for (char, options), group in groups_on_page(page[0]).items():
            if len(group) < 2:
                continue
            colors = [item['font_id'] for item in group]
            missing += min(len(group), len(options)) - len(set(colors))
            distance = separation([item['center'] for item in group], colors)[0]
            if math.isfinite(distance):
                distances.append(distance)
    return (-missing, min(distances, default=math.inf), sum(math.sqrt(d) for d in distances))


def metadata(paths):
    result = []
    for (char, options), group in groups_on_page(paths).items():
        if len(group) < 2:
            continue
        colors = [item['font_id'] for item in group]
        distance = separation([item['center'] for item in group], colors)[0]
        result.append(dict(character=char, occurrences=len(group), available_fonts=len(options),
                           used_fonts=len(set(colors)), nearest_same_font_mm=round(math.sqrt(distance), 3)
                           if math.isfinite(distance) else None))
    return dict(policy='page-spatial', groups=result)

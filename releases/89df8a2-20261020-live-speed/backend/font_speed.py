"""Comparable font motion costs. No device access or job generation."""
import math
import statistics

BENCHMARK = dict(size_mm=5, spacing_mm=1, draw_feed=3000, move_feed=3000,
                 xy_acceleration=3000, z_acceleration=8000, pen_travel_mm=10,
                 median_score=100, description='5 mm 中文字高，绘图/移动均 3000 mm/min，Z 行程 10 mm；100 为样本充分字体的中位水平，越高越快。')


def travel_seconds(length, feed, acceleration):
    """Trapezoidal/triangular move with zero entry and exit speed."""
    speed=feed/60
    return 2*math.sqrt(length/acceleration) if length<speed*speed/acceleration else length/speed+speed/acceleration


def motion_cost(strokes, advance, settings=BENCHMARK):
    strokes=[s for s in strokes if len(s)>=2]
    if not strokes:return None
    draw=move=draw_time=move_time=0.
    for index,stroke in enumerate(strokes):
        length=sum(math.dist(a,b) for a,b in zip(stroke,stroke[1:]))
        draw+=length
        draw_time+=travel_seconds(length,settings['draw_feed'],settings['xy_acceleration'])
        # Include one character transition per character, using its own repeated form.
        target=strokes[index+1][0] if index+1<len(strokes) else (strokes[0][0][0]+advance,strokes[0][0][1])
        distance=math.dist(stroke[-1],target)
        move+=distance
        move_time+=travel_seconds(distance,settings['move_feed'],settings['xy_acceleration'])
    pen_time=2*len(strokes)*travel_seconds(settings['pen_travel_mm'],settings['move_feed'],settings['z_acceleration'])
    return dict(seconds=draw_time+move_time+pen_time,draw_mm=draw,move_mm=move,
                strokes=len(strokes),pen_seconds=pen_time,points=sum(len(s) for s in strokes))


def assign_scores(measurements):
    """Control for missing characters by comparing each glyph to its library median."""
    characters={c for costs in measurements.values() for c in costs}
    baselines={c:statistics.median(costs[c]['seconds'] for costs in measurements.values() if c in costs)
               for c in characters}
    relative={key:math.exp(statistics.mean(math.log(v['seconds']/baselines[c]) for c,v in costs.items()))
              for key,costs in measurements.items() if costs}
    # Sparse fonts receive provisional scores but cannot set the reference median.
    reliable=[ratio for key,ratio in relative.items() if len(measurements[key])>=min(96,len(characters))]
    middle=statistics.median(reliable or relative.values()) if relative else 1.
    reference_seconds=statistics.mean(baselines.values()) if baselines else 0.
    results={}
    for key,costs in measurements.items():
        count=len(costs)
        if not count:
            results[key]=dict(score=None,sample_characters=0,confidence='unavailable');continue
        ratio=relative[key]
        results[key]=dict(score=round(100*middle/ratio,1),sample_characters=count,
            confidence='full' if count>=min(96,len(characters)) else 'limited',
            standardized_seconds_per_100=round(reference_seconds*ratio*100,1),
            draw_mm_per_character=round(statistics.mean(v['draw_mm'] for v in costs.values()),2),
            move_mm_per_character=round(statistics.mean(v['move_mm'] for v in costs.values()),2),
            strokes_per_character=round(statistics.mean(v['strokes'] for v in costs.values()),2),
            pen_time_percent=round(100*sum(v['pen_seconds'] for v in costs.values())/sum(v['seconds'] for v in costs.values()),1))
    return results

"""Read-only native motion candidates. Reversals include legitimate mixing/routing.

Only unambiguous equal-shape one-cell transitions are linked. A candidate is
not a finding: splitting, region avoidance, capture and mixing need review.
"""
import json
import xml.etree.ElementTree as E
from waste_recovery import boxes


def scan(path):
    results = []
    for flow in E.parse(path).getroot():
        for cmd in flow:
            if cmd.get('Component') != 'HighVoltage':
                continue
            tracks, active, next_id = {}, {}, 0
            for frame, relay in enumerate(cmd):
                rects = boxes(relay.get('RelayCode', ''))
                unmatched, old, active, used = set(range(len(rects))), active, {}, set()
                for i, rect in enumerate(rects):
                    if rect in old:
                        active[rect] = old[rect]
                        unmatched.remove(i)
                        used.add(rect)
                candidates = {}
                for i in unmatched:
                    rect = rects[i]
                    candidates[i] = [p for p in old if p not in used and p[2:] == rect[2:]
                                     and abs(p[0]-rect[0])+abs(p[1]-rect[1]) == 1]
                for i, matches in candidates.items():
                    if len(matches) != 1 or sum(matches[0] in x for x in candidates.values()) != 1:
                        continue
                    prev, rect = matches[0], rects[i]
                    track_id = old[prev]
                    active[rect] = track_id
                    unmatched.remove(i)
                    track = tracks[track_id]
                    track['directions'].append((rect[0]-prev[0], rect[1]-prev[1]))
                    track.update(last=rect, end_frame=frame)
                for i in unmatched:
                    rect = rects[i]
                    next_id += 1
                    active[rect] = next_id
                    tracks[next_id] = dict(first=rect, last=rect, start_frame=frame,
                                           end_frame=frame, directions=[])
            for track in tracks.values():
                dirs = track.pop('directions')
                px, nx = sum(dx > 0 for dx, dy in dirs), sum(dx < 0 for dx, dy in dirs)
                py, ny = sum(dy > 0 for dx, dy in dirs), sum(dy < 0 for dx, dy in dirs)
                excess = 2*(min(px, nx)+min(py, ny))
                if excess >= 4:
                    results.append(dict(section=flow.get('Name').split()[0], flow=int(flow.get('ID')),
                                        command=int(cmd.get('ID')), name=cmd.get('Name'),
                                        excess_cells=excess, moves=dict(right=px, left=nx, down=py, up=ny),
                                        **track))
    results.sort(key=lambda a: -a['excess_cells'])
    return results

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('script');args=ap.parse_args()
    print(json.dumps(scan(args.script),ensure_ascii=False,indent=2))

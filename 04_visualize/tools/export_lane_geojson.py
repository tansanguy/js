#!/usr/bin/env python3
"""Export per-lane geometry from a SUMO net to GeoJSON (lon/lat).

Unlike the per-edge geojson (one centerline per edge), this writes one LineString
per *lane*, already offset to the lane's real position, so a 2-lane road shows as
two parallel lines in the animation. Run with the venv python (needs sumolib):

    .venv/bin/python 04_visualize/tools/export_lane_geojson.py
"""

import argparse
import json
from pathlib import Path

import sumolib

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_OUT = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_lanes.geojson"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export per-lane geometry to GeoJSON")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    net = sumolib.net.readNet(str(args.net))
    features = []
    for edge in net.getEdges(withInternal=False):
        lane_count = edge.getLaneNumber()
        for lane in edge.getLanes():
            shape = lane.getShape()  # [(x, y), ...] in net coords
            coords = [list(net.convertXY2LonLat(x, y)) for x, y in shape]  # [lon, lat]
            if len(coords) < 2:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "edge_id": edge.getID(),
                    "lane_id": lane.getID(),
                    "lane_index": lane.getIndex(),
                    "lane_count": lane_count,
                },
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(features)} lanes -> {args.output}")


if __name__ == "__main__":
    main()

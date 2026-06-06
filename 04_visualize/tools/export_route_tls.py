#!/usr/bin/env python3
"""Export the traffic lights ON a given route to a GeoJSON subset.

Authoritative corridor-signal set: the traffic-light junctions the emergency
route actually passes through (from the SUMO net + the route's edge list), in
order. This is exactly "the signals used in the simulation that lie on the 대로",
without the off-corridor signals a geometric buffer would also catch.

    .venv/bin/python 04_visualize/tools/export_route_tls.py \
        --rou runs/.../B2/.../emergency_*.rou.xml
"""

import argparse
import json
import re
from pathlib import Path

import sumolib

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_OUT = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_route_tls.geojson"


def route_edges_from_rou(rou_path: Path) -> list[str]:
    text = Path(rou_path).read_text(encoding="utf-8")
    match = re.search(r'edges="([^"]+)"', text)
    if not match:
        raise ValueError(f"No route edges found in {rou_path}")
    return match.group(1).split()


def route_tls(net: "sumolib.net.Net", edges: list[str]) -> list[dict]:
    """Ordered, de-duplicated traffic-light junctions the route passes through."""
    nodes = [net.getEdge(edges[0]).getFromNode()] + [net.getEdge(e).getToNode() for e in edges]
    seen: set[str] = set()
    out: list[dict] = []
    for node in nodes:
        nid = node.getID()
        if nid in seen or node.getType() != "traffic_light":
            continue
        seen.add(nid)
        x, y = node.getCoord()
        lon, lat = net.convertXY2LonLat(x, y)
        out.append({"tls_id": nid, "lat": round(lat, 6), "lon": round(lon, 6),
                    "route_order": len(out)})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Export on-route traffic lights to GeoJSON")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--rou", type=Path, required=True,
                        help="Emergency route .rou.xml (edge list source)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    net = sumolib.net.readNet(str(args.net))
    edges = route_edges_from_rou(args.rou)
    tls = route_tls(net, edges)

    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [t["lon"], t["lat"]]},
        "properties": t,
    } for t in tls]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(features)} on-route traffic lights -> {args.output}")
    for t in tls:
        print(f"  #{t['route_order']:>2}  {t['tls_id']}  ({t['lat']:.5f}, {t['lon']:.5f})")


if __name__ == "__main__":
    main()
